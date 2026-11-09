"""Coletor assíncrono para o Piloto da Seccional OAB/SP via API do Diário Eletrônico (DEOAB).

Coleta os Acórdãos das Turmas Disciplinares do TED (1ª a 27ª Turmas Disciplinares),
da Turma Deontológica (TED-I) e das Câmaras Recursais (1ª a 9ª Câmaras Recursais)
publicados pela Seccional São Paulo (idOrganizacao = 25) em diario.oab.org.br,
priorizando decisões com Acórdão e vinculando o PDF oficial do Inteiro Teor.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ted_search.config import (
    DEOAB_API_BASE_URL,
    DEOAB_ASSETS_BASE_URL,
    DIARIO_MATERIA_URL,
    RAW_DIR,
    create_async_http_client,
)
from ted_search.parser import (
    clean_text,
    detect_orgao_julgador_oabsp,
    detect_resultado,
    detect_sancao,
    extract_dispositivos,
    extract_relator_fallback,
)

RE_HTML_TAGS = re.compile(r"<[^>]+>")
RE_HTML_BREAKS = re.compile(r"</p>|<br\s*/?>|</div>|</section>", re.IGNORECASE)
RE_PROC_CNJ_OAB = re.compile(r"\b(25\.\d{4}\.(?:19|20)\d{2}\.\d{6}-\d)\b")
RE_PROC_LEGACY_SP = re.compile(r"\b(\d{2,5}R\d{6}(?:19|20)\d{2})\b", re.IGNORECASE)
RE_ACORDAO_NUM = re.compile(
    r"A\s*C\s*[ÓO]\s*R\s*D\s*[ÃA]\s*O\s*(?:N[º°o.]?\s*)?(\d{1,6})\b",
    re.IGNORECASE,
)
RE_VISTOS_SPLIT = re.compile(
    r"(?:^|\n)\s*(Vistos\s*,?\s*relatados\s+e\s+(?:discutidos|examinados).+)$",
    re.IGNORECASE | re.DOTALL,
)


def html_to_paragraphs(raw_html: str) -> str:
    """Converte o HTML de conteudoCodificado do DEOAB em texto limpo separado por parágrafos."""
    if not raw_html:
        return ""
    s = RE_HTML_BREAKS.sub("\n", raw_html)
    s = RE_HTML_TAGS.sub(" ", s)
    s = clean_text(s)
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    return "\n\n".join(lines)


def normalize_deoab_sp_item(pub: dict[str, Any]) -> dict[str, Any] | None:
    """Transforma um registro de /api/v1/publicacao/{id}/publicado da OAB/SP no schema do banco."""
    if "objeto" in pub and isinstance(pub["objeto"], dict):
        pub = pub["objeto"]
    pub_id = pub.get("id")
    if not pub_id:
        return None

    tipo_pub = clean_text((pub.get("tipoPublicacao") or {}).get("descricao") or "")
    # Filtro prioritário: apenas decisões classificadas como Acórdão (ou Ementa com acórdão)
    if tipo_pub.upper() not in {"ACÓRDÃO", "ACORDAO", "EMENTA"}:
        return None

    titulo = clean_text(pub.get("titulo") or "")
    setor_obj = pub.get("setorPublicacao") or {}
    setor_nome = clean_text(setor_obj.get("nomeAlternativo") or "")
    full_text = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
    if not full_text or len(full_text) < 80:
        return None

    # Remover preâmbulo padrão do Art. 69 do EAOAB quando presente no topo de publicações de Câmaras Recursais
    cleaned_body = re.sub(
        r"^\d{1,2}\s*[-–]\s*[“\"]?Art\.\s*69\.\s*EAOAB.*?\(\s*NR\s*\)\s*\d*\s*",
        "",
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()

    # Separar Ementa/Fundamentação e Acórdão ("Vistos, relatados e discutidos...")
    m_vistos = RE_VISTOS_SPLIT.search(cleaned_body)
    if m_vistos:
        acordao_text = m_vistos.group(1).strip()
        ementa_text = cleaned_body[: m_vistos.start()].strip()
    else:
        acordao_text = ""
        ementa_text = cleaned_body

    # Priorizar registros que efetivamente possuem texto de ementa e/ou acórdão
    if not ementa_text and acordao_text:
        ementa_text = acordao_text

    # Extrair número do processo (padrão nacional 25.0886.AAAA.NNNNNN-D ou padrão TED-SP 21R0001382012)
    m_proc = RE_PROC_CNJ_OAB.search(full_text)
    if not m_proc:
        m_proc = RE_PROC_LEGACY_SP.search(full_text)
    numero_processo = m_proc.group(1).upper() if m_proc else None

    # Extrair número do Acórdão
    m_ac_num = RE_ACORDAO_NUM.search(f"{titulo}\n{full_text[:600]}")
    numero_acordao = m_ac_num.group(1) if m_ac_num else None

    sigla_orgao, orgao_norm = detect_orgao_julgador_oabsp(setor_nome, titulo, full_text)
    numero_ementa = f"AC-{numero_acordao}/{sigla_orgao}" if numero_acordao else None

    dispositivos = extract_dispositivos(None, full_text)
    sancao = detect_sancao(full_text)
    resultado = detect_resultado(ementa_text, acordao_text)
    relator = extract_relator_fallback(None, full_text)

    data_pub = (pub.get("dataPublicacao") or "")[:10] or None
    ano_ementa = int(data_pub[:4]) if (data_pub and data_pub[:4].isdigit()) else None
    edicao = pub.get("edicao")
    pagina = pub.get("pagina")
    ano_romano = pub.get("ano") or ""
    pub_str = f"DEOAB/SP, Ano {ano_romano}, Ed. {edicao}, p. {pagina} ({data_pub})"

    tipo_ementa = "CONSULTA" if sigla_orgao == "OABSP-TDEONT" else "PROCESSO_DISCIPLINAR"

    return {
        "id": f"DEOAB_SP:{pub_id}",
        "fonte": "DEOAB_SP",
        "contexto": "EMENTA_CONSULTA" if tipo_ementa == "CONSULTA" else "EMENTA",
        "tipo_ementa": tipo_ementa,
        "numero_processo": numero_processo,
        "numero_ementa": numero_ementa,
        "ano_ementa": ano_ementa,
        "id_organizacao": 25,
        "nome_organizacao": "Conselho Seccional - São Paulo",
        "orgao_julgador_raw": f"Conselho Seccional - São Paulo / {setor_nome} ({titulo})" if setor_nome else f"Conselho Seccional - São Paulo / {titulo}",
        "orgao_julgador_norm": orgao_norm,
        "sigla_orgao": sigla_orgao,
        "uf_origem": "SP",
        "relator_principal": relator,
        "relatores_json": "[]",
        "partes_json": "[]",
        "dispositivos_json": json.dumps(dispositivos, ensure_ascii=False),
        "dispositivos_texto": " | ".join(dispositivos),
        "sancao_identificada": sancao,
        "resultado_julgamento": resultado,
        "descricao_ementa": ementa_text,
        "descricao_acordao": acordao_text,
        "inteiro_teor_texto": full_text,
        "pdf_url": f"/api/diario-pdf/{pub_id}",
        "diario_url": f"{DIARIO_MATERIA_URL}/{pub_id}",
        "publicacao_str": pub_str,
        "data_decisao": data_pub,
        "is_ted_disciplinar": 1,
        "tem_inteiro_teor": 1,
    }


async def resolve_deoab_pdf_url(materia_id: int | str) -> str | None:
    """Aciona /api/v1/publicacao/{id}/publicado/path no DEOAB para gerar/obter a URL direta do PDF."""
    url = f"{DEOAB_API_BASE_URL}/publicacao/{materia_id}/publicado/path"
    async with create_async_http_client(timeout=20.0) as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            val = (resp.json() or {}).get("value")
            if val:
                return f"{DEOAB_ASSETS_BASE_URL}/{val.lstrip('/')}"
    return None


async def fetch_deoab_sp_pilot(
    pages_per_year: int = 12,
    years: list[int] | None = None,
    concurrency: int = 20,
    use_cache: bool = True,
    raw_dir: Path = RAW_DIR,
) -> list[dict[str, Any]]:
    """Coleta acórdãos das Turmas Disciplinares, Deontológicas e Câmaras Recursais da OAB/SP no DEOAB."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_file = raw_dir / "deoab_sp_acordaos.json"
    if use_cache and cache_file.exists():
        print(f"[cache] Carregando Acórdãos OAB/SP (DEOAB) de {cache_file}...")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    target_years = years or [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019]
    candidate_ids: list[int] = []
    seen_ids: set[int] = set()
    sem = asyncio.Semaphore(concurrency)

    async with create_async_http_client(timeout=25.0) as client:
        # 1. Buscar páginas de resultados para termo=EMENTA em cada ano + termo=DEONTOLOGICA
        search_tasks = []
        for yr in target_years:
            for p in range(1, pages_per_year + 1):
                search_tasks.append((yr, "EMENTA", p))
        for p in range(1, 9):
            search_tasks.append((0, "DEONTOLOGICA", p))

        async def _fetch_search_page(yr: int, termo: str, page: int) -> list[int]:
            inicio = f"01/01/{yr}" if yr else "01/01/2019"
            fim = f"31/12/{yr}" if yr else "31/12/2026"
            params = {
                "pagina": str(page),
                "termo": termo,
                "inicio": inicio,
                "fim": fim,
                "idOrganizacao": "25",
            }
            async with sem:
                try:
                    resp = await client.get(f"{DEOAB_API_BASE_URL}/diario/pesquisa", params=params)
                    if resp.status_code == 200:
                        itens = (resp.json() or {}).get("itens") or []
                        return [int(it["id"]) for it in itens if it.get("id")]
                except Exception:
                    pass
            return []

        print(f"[deoab_sp] Listando IDs de acórdãos OAB/SP ({len(search_tasks)} páginas de índice)...")
        pages_results = await asyncio.gather(*(_fetch_search_page(y, t, p) for y, t, p in search_tasks))
        for id_list in pages_results:
            for mid in id_list:
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    candidate_ids.append(mid)

        print(f"[deoab_sp] {len(candidate_ids)} publicações únicas localizadas. Baixando inteiro teor dos Acórdãos...")

        raw_publications: list[dict[str, Any]] = []

        async def _fetch_pub(mid: int) -> None:
            async with sem:
                for attempt in range(3):
                    try:
                        r = await client.get(f"{DEOAB_API_BASE_URL}/publicacao/{mid}/publicado")
                        if r.status_code == 200:
                            pub_data = r.json()
                            tipo = ((pub_data.get("tipoPublicacao") or {}).get("descricao") or "").upper()
                            if tipo in {"ACÓRDÃO", "ACORDAO", "EMENTA"}:
                                raw_publications.append(pub_data)
                        break
                    except Exception:
                        await asyncio.sleep(0.3 * (attempt + 1))

        batch_size = 200
        for start in range(0, len(candidate_ids), batch_size):
            chunk = candidate_ids[start : start + batch_size]
            await asyncio.gather(*(_fetch_pub(mid) for mid in chunk))
            print(f"[deoab_sp] Progresso: {min(start + batch_size, len(candidate_ids))}/{len(candidate_ids)} inspecionados ({len(raw_publications)} acórdãos válidos)...")

    # Ordenar por ID decrescente para determinismo
    raw_publications.sort(key=lambda x: x.get("id", 0), reverse=True)
    cache_file.write_text(json.dumps(raw_publications, ensure_ascii=False), encoding="utf-8")
    print(f"[deoab_sp] Salvo em {cache_file} ({len(raw_publications)} acórdãos completos da OAB/SP).")
    return raw_publications
