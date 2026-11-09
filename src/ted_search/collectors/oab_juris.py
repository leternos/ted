"""Coletor assíncrono para o portal OAB Jurisprudência (jurisprudencia.oab.org.br).

Faz o backfill de todos os registros de EMENTA, EMENTA_CONSULTA e DECISAO_SUMULA
em lotes grandes (size=1000), salva os dumps JSON em data/raw/ como checkpoint
e aplica o Parser Jurídico para enriquecer e normalizar os dados no SQLite.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from ted_search.config import (
    DIARIO_MATERIA_URL,
    OAB_JURIS_BASE_URL,
    OAB_JURIS_CONTEXTS,
    RAW_DIR,
    create_async_http_client,
)
from ted_search.parser import (
    classify_is_ted_disciplinar,
    clean_text,
    detect_orgao_julgador,
    detect_resultado,
    detect_sancao,
    detect_uf_origem,
    extract_dispositivos,
    extract_relator_fallback,
    normalize_numero_ementa,
    split_legacy_ementa_acordao,
)

RE_YEAR_IN_STR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _extract_year(
    data_decisao: str | None,
    numero_ementa: str | None,
    numero_processo: str | None,
    pub_str: str | None,
) -> int | None:
    if data_decisao and len(data_decisao) >= 4 and data_decisao[:4].isdigit():
        y = int(data_decisao[:4])
        if 1990 <= y <= 2030:
            return y
    if numero_ementa:
        m = re.search(r"/(19\d{2}|20\d{2})\b", numero_ementa)
        if m:
            return int(m.group(1))
    if pub_str:
        m = RE_YEAR_IN_STR.search(pub_str)
        if m:
            return int(m.group(1))
    if numero_processo:
        m = re.search(r"\d{2}\.\d{4}\.(19\d{2}|20\d{2})\.", numero_processo)
        if m:
            return int(m.group(1))
    return None


async def fetch_context_all_pages(
    contexto: str,
    page_size: int = 1000,
    use_cache: bool = True,
    raw_dir: Path = RAW_DIR,
) -> list[dict[str, Any]]:
    """Baixa todas as páginas de um contexto da API OAB Jurisprudência."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_file = raw_dir / f"oab_juris_{contexto.lower()}.json"
    if use_cache and cache_file.exists():
        print(f"[cache] Carregando {contexto} de {cache_file}...")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    all_items: list[dict[str, Any]] = []
    async with create_async_http_client(headers={"Accept": "application/json"}) as client:
        # Primeira página para descobrir totalRegistros
        page = 0
        while True:
            params = {
                "pesquisaLivre": "",
                "modoPesquisaLivre": "TODOS_OS_TERMOS",
                "numeroProcesso": "",
                "page": str(page),
                "size": str(page_size),
                "sort": "dataOrdenacao,desc",
            }
            url = f"{OAB_JURIS_BASE_URL}/contexto/{contexto}"
            for attempt in range(4):
                try:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except Exception as exc:
                    if attempt == 3:
                        raise
                    wait_s = 2 ** attempt
                    print(f"[retry] Erro em {contexto} page={page}: {exc}. Aguardando {wait_s}s...")
                    await asyncio.sleep(wait_s)

            batch = data.get("resultado") or []
            meta = data.get("metadata") or []
            total_registros = meta[0].get("totalRegistros", 0) if meta else 0

            all_items.extend(batch)
            print(
                f"[oab_juris] {contexto}: página {page} concluída "
                f"({len(all_items)}/{total_registros} registros)"
            )

            if not batch or len(all_items) >= total_registros:
                break
            page += 1
            await asyncio.sleep(0.15)  # Politeness delay entre lotes

    cache_file.write_text(json.dumps(all_items, ensure_ascii=False), encoding="utf-8")
    print(f"[oab_juris] {contexto} salvo em {cache_file} ({len(all_items)} registros).")
    return all_items


def normalize_oab_juris_item(raw: dict[str, Any], contexto: str) -> dict[str, Any]:
    """Transforma um registro bruto da API OAB Jurisprudência no formato enriquecido do banco."""
    orgao_obj = raw.get("orgaoJulgador") or {}
    id_org = orgao_obj.get("idOrganizacao")
    nome_org = clean_text(orgao_obj.get("nomeOrganizacao") or "Conselho Federal")
    nome_setor = clean_text(orgao_obj.get("nomeSetor") or "")
    orgao_raw = f"{nome_org} / {nome_setor}" if nome_setor else nome_org

    # Em EMENTA / EMENTA_CONSULTA os campos são descricaoEmenta e descricaoAcordao;
    # Em DECISAO_SUMULA são descricao e texto.
    if contexto == "DECISAO_SUMULA":
        raw_ementa = raw.get("descricao") or raw.get("texto") or ""
        raw_acordao = raw.get("texto") if raw.get("descricao") else ""
        raw_num_ementa = raw.get("numero")
        tipo_ementa = "SUMULA"
        raw_date = raw.get("data")
    else:
        raw_ementa = raw.get("descricaoEmenta") or ""
        raw_acordao = raw.get("descricaoAcordao") or ""
        raw_num_ementa = raw.get("numeroEmenta")
        tipo_ementa = raw.get("tipoEmenta") or ("CONSULTA" if contexto == "EMENTA_CONSULTA" else "OUTRO")
        raw_date = raw.get("dataDecisao")

    ementa_clean, acordao_clean, pub_legacy = split_legacy_ementa_acordao(raw_ementa, raw_acordao)
    combined_text = f"{ementa_clean}\n{acordao_clean}".strip()

    info_comp = raw.get("informacoesComplementares") or {}
    numero_processo = clean_text(
        raw.get("numeroProcesso") or info_comp.get("numeroProcessoAntigo") or ""
    ) or None

    numero_ementa = normalize_numero_ementa(raw_num_ementa, combined_text)
    sigla_orgao, orgao_norm = detect_orgao_julgador(orgao_obj, numero_ementa, combined_text)

    # Dispositivos normativos (API + Regex)
    dispositivos = extract_dispositivos(raw.get("dispositivosNormativos"), combined_text)
    sancao = detect_sancao(combined_text)
    resultado = detect_resultado(ementa_clean, acordao_clean)
    partes = raw.get("partes") or []
    uf_origem = "SP" if id_org == 25 else detect_uf_origem(numero_processo, partes, combined_text)
    relator_principal = extract_relator_fallback(raw.get("relatores"), combined_text)

    # Publicação no Diário Eletrônico (DEOAB) ou DOU legado
    pub_diario = raw.get("publicacaoDiario") or {}
    diario_id = pub_diario.get("_id")
    diario_url = f"{DIARIO_MATERIA_URL}/{diario_id}" if diario_id else None

    if pub_diario:
        data_pub = (pub_diario.get("dataPublicacao") or "")[:10]
        pub_str = f"DEOAB, Ano {pub_diario.get('ano', '')}, Ed. {pub_diario.get('edicao', '')}, p. {pub_diario.get('pagina', '')} ({data_pub})"
        if not raw_date and data_pub:
            raw_date = data_pub
    else:
        pub_str = clean_text(info_comp.get("publicacao") or pub_legacy or "") or None

    data_iso = (raw_date or "")[:10] if raw_date else None
    ano_ementa = _extract_year(data_iso, numero_ementa, numero_processo, pub_str)

    # Todos os acórdãos publicados no DEOAB possuem PDF individual oficial gerado via /api/v1/publicacao/{id}/publicado/path
    pdf_url = f"/api/diario-pdf/{diario_id}" if diario_id else None

    is_ted = classify_is_ted_disciplinar(
        sigla_orgao=sigla_orgao,
        tipo_ementa=tipo_ementa,
        dispositivos=dispositivos,
        sancao=sancao,
        text=combined_text,
    )

    #Se o tipo_ementa era genérico "OUTRO" mas é da 2ª Câmara/Turmas ou tem infração do Art. 34, refinamos:
    if tipo_ementa == "OUTRO" and is_ted:
        tipo_ementa = "PROCESSO_DISCIPLINAR"

    # Gerar ID determinístico único
    hash_input = f"{contexto}|{numero_processo or ''}|{numero_ementa or ''}|{ementa_clean[:200]}"
    short_hash = hashlib.sha1(hash_input.encode("utf-8")).hexdigest()[:12]
    rec_id = f"{contexto}:{short_hash}"

    has_full_sp = bool(id_org == 25 and diario_id and acordao_clean)
    inteiro_teor_sp = f"{ementa_clean}\n\nACÓRDÃO\n{acordao_clean}" if has_full_sp else None

    return {
        "id": rec_id,
        "fonte": "OAB_JURIS",
        "contexto": contexto,
        "tipo_ementa": tipo_ementa,
        "numero_processo": numero_processo,
        "numero_ementa": numero_ementa,
        "ano_ementa": ano_ementa,
        "id_organizacao": id_org,
        "nome_organizacao": nome_org,
        "orgao_julgador_raw": orgao_raw,
        "orgao_julgador_norm": orgao_norm,
        "sigla_orgao": sigla_orgao,
        "uf_origem": uf_origem,
        "relator_principal": relator_principal,
        "relatores_json": json.dumps(raw.get("relatores") or [], ensure_ascii=False),
        "partes_json": json.dumps(partes, ensure_ascii=False),
        "dispositivos_json": json.dumps(dispositivos, ensure_ascii=False),
        "dispositivos_texto": " | ".join(dispositivos),
        "sancao_identificada": sancao,
        "resultado_julgamento": resultado,
        "descricao_ementa": ementa_clean,
        "descricao_acordao": acordao_clean,
        "inteiro_teor_texto": inteiro_teor_sp,
        "pdf_url": pdf_url,
        "diario_url": diario_url,
        "publicacao_str": pub_str,
        "data_decisao": data_iso,
        "is_ted_disciplinar": 1 if is_ted else 0,
        "tem_inteiro_teor": 1 if has_full_sp else 0,
    }
