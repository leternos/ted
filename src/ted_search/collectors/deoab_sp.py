"""Coletor assíncrono da Seccional OAB/SP via API do Diário Eletrônico (DEOAB).

Coleta os Acórdãos das Turmas Disciplinares do TED e das Câmaras Recursais publicados pela
Seccional São Paulo (idOrganizacao = 25) em diario.oab.org.br, com paginação completa por ano
e vínculo ao PDF oficial do Inteiro Teor.

As Consultas da 1ª Turma (Deontológica, TED-I) praticamente não aparecem no DEOAB: ficam no ementário
do site da OAB/SP e não são cobertas por este coletor.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
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


DEOAB_PAGE_SIZE = 10  # a API /diario/pesquisa devolve 10 itens por página, em ordem crescente de data
DEOAB_SP_YEARS = list(range(2019, 2027))  # o DEOAB começa em 2019 para a OAB/SP
# "ACORDAO" cobre todos os Acórdãos; "EMENTA" sozinho perde ~6% (acórdãos publicados sem a palavra "ementa")
DEOAB_SP_SEARCH_TERMS = ("ACORDAO",)
_DECISION_TYPES = {"ACÓRDÃO", "ACORDAO", "EMENTA"}
_PUB_KEEP_KEYS = (
    "id", "titulo", "dataPublicacao", "edicao", "pagina", "ano",
    "tipoPublicacao", "setorPublicacao", "conteudoCodificado",
)


def _slim_publication(pub: dict[str, Any]) -> dict[str, Any]:
    """Mantém só os campos usados por normalize_deoab_sp_item (reduz o cache bruto em disco)."""
    slim = {k: pub.get(k) for k in _PUB_KEEP_KEYS if pub.get(k) is not None}
    if not slim.get("conteudoCodificado") and pub.get("conteudo"):
        slim["conteudo"] = pub["conteudo"]
    return slim


async def _get_json_with_retry(client: Any, url: str, params: dict[str, str] | None, sem: asyncio.Semaphore, attempts: int = 5) -> Any:
    """GET com retentativas e backoff. Devolve None se todas as tentativas falharem (nunca engole em silêncio)."""
    for attempt in range(attempts):
        try:
            async with sem:
                resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return {}
        except Exception:
            pass
        await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def fetch_deoab_sp_year(
    client: Any,
    year: int,
    sem: asyncio.Semaphore,
    raw_dir: Path,
    terms: tuple[str, ...] = DEOAB_SP_SEARCH_TERMS,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Coleta TODOS os acórdãos da OAB/SP publicados no DEOAB em um ano (paginação completa, retomável).

    O progresso fica em ``raw_dir/deoab_sp/{year}.index.json`` (ids por dia) e ``{year}.jsonl`` (uma linha
    por publicação inspecionada); reexecutar retoma de onde parou. ``max_pages`` vira uma AMOSTRA:
    só a 1ª página de ``max_pages`` dias igualmente espaçados no ano (uso em testes).
    """
    out_dir = raw_dir / "deoab_sp"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / f"{year}.jsonl"
    inspected: set[int] = set()
    if jsonl.exists():
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    inspected.add(int(json.loads(line)["id"]))

    # Busca por janelas de UM DIA: consultas de ano inteiro obrigam o DEOAB a contar milhares de
    # resultados a cada página (20s+ por requisição sob concorrência); por dia respondem em ~1s.
    search_url = f"{DEOAB_API_BASE_URL}/diario/pesquisa"
    index_file = out_dir / f"{year}.index.json"
    day_index: dict[str, list[int]] = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    today = date.today()
    first_day, last_day = date(year, 1, 1), min(date(year, 12, 31), today)
    all_days = [first_day + timedelta(days=i) for i in range((last_day - first_day).days + 1)] if last_day >= first_day else []
    if max_pages and len(all_days) > max_pages:
        # Amostra: dias igualmente espaçados ao longo do ano (e só a 1ª página de cada um)
        step = len(all_days) / max_pages
        all_days = [all_days[int(i * step)] for i in range(max_pages)]
    # Dias já indexados são reaproveitados, exceto os mais recentes (o Diário ainda pode receber edições)
    todo_days = [d for d in all_days if d.isoformat() not in day_index or (today - d).days <= 3]
    expected: dict[str, int] = {}
    failed_pages: list[tuple[str, int]] = []

    async def _day(d: date) -> None:
        dstr = d.strftime("%d/%m/%Y")
        ids: set[int] = set()
        ok = True
        for termo in terms:
            params = {"inicio": dstr, "fim": dstr, "idOrganizacao": "25", "termo": termo}
            first = await _get_json_with_retry(client, search_url, {**params, "pagina": "1"}, sem)
            if first is None:
                failed_pages.append((f"{d.isoformat()} {termo}", 1))
                ok = False
                continue
            total = int(first.get("quantidade") or 0)
            expected[termo] = expected.get(termo, 0) + total
            ids.update(int(it["id"]) for it in (first.get("itens") or []) if it.get("id"))
            n_pages = 1 if max_pages else -(-total // DEOAB_PAGE_SIZE)

            async def _page(p: int, params: dict[str, str] = params, termo: str = termo) -> bool:
                for _ in range(2):  # página dentro do intervalo esperado não pode vir vazia
                    data = await _get_json_with_retry(client, search_url, {**params, "pagina": str(p)}, sem)
                    itens = (data or {}).get("itens") or []
                    if itens:
                        ids.update(int(it["id"]) for it in itens if it.get("id"))
                        return True
                failed_pages.append((f"{d.isoformat()} {termo}", p))
                return False

            results = await asyncio.gather(*(_page(p) for p in range(2, n_pages + 1)))
            ok = ok and all(results)
        if ok:
            day_index[d.isoformat()] = sorted(ids)

    chunk = 31
    for start in range(0, len(todo_days), chunk):
        await asyncio.gather(*(_day(d) for d in todo_days[start : start + chunk]))
        index_file.write_text(json.dumps(day_index), encoding="utf-8")
        print(f"[deoab_sp] {year}: índice {min(start + chunk, len(todo_days))}/{len(todo_days)} dias...", flush=True)

    wanted_days = {d.isoformat() for d in all_days}
    candidate_ids: set[int] = {i for day, ids in day_index.items() if day in wanted_days for i in ids}

    pending = sorted(candidate_ids - inspected)
    failed_pubs: list[int] = []
    lock = asyncio.Lock()
    with jsonl.open("a", encoding="utf-8") as fh:

        async def _pub(mid: int) -> None:
            data = await _get_json_with_retry(client, f"{DEOAB_API_BASE_URL}/publicacao/{mid}/publicado", None, sem)
            if not data:
                failed_pubs.append(mid)
                return
            if isinstance(data.get("objeto"), dict):
                data = data["objeto"]
            tipo = ((data.get("tipoPublicacao") or {}).get("descricao") or "").strip()
            if tipo.upper() in _DECISION_TYPES:
                row = _slim_publication(data)
                row["id"] = mid
            else:
                row = {"id": mid, "_skip": tipo or "?"}
            async with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        batch = 400
        for start in range(0, len(pending), batch):
            await asyncio.gather(*(_pub(mid) for mid in pending[start : start + batch]))
            fh.flush()
            print(f"[deoab_sp] {year}: {min(start + batch, len(pending))}/{len(pending)} publicações inspecionadas...", flush=True)

    summary = {
        "year": year,
        "esperado_por_termo": expected,
        "candidatos": len(candidate_ids),
        "novos_inspecionados": len(pending) - len(failed_pubs),
        "paginas_com_falha": failed_pages,
        "publicacoes_com_falha": failed_pubs,
    }
    (out_dir / f"{year}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_deoab_sp_raw(raw_dir: Path = RAW_DIR, years: list[int] | None = None) -> list[dict[str, Any]]:
    """Lê os acórdãos já coletados em raw_dir/deoab_sp/*.jsonl (ignora publicações que não são decisão)."""
    out_dir = raw_dir / "deoab_sp"
    pubs: dict[int, dict[str, Any]] = {}
    for year in years or DEOAB_SP_YEARS:
        jsonl = out_dir / f"{year}.jsonl"
        if not jsonl.exists():
            continue
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if "_skip" not in row:
                    pubs[int(row["id"])] = row
    return sorted(pubs.values(), key=lambda x: x.get("id", 0), reverse=True)


async def fetch_deoab_sp_pilot(
    pages_per_year: int | None = None,
    years: list[int] | None = None,
    concurrency: int = 12,
    use_cache: bool = True,
    raw_dir: Path = RAW_DIR,
) -> list[dict[str, Any]]:
    """Coleta os acórdãos das Turmas Disciplinares e Câmaras Recursais da OAB/SP no DEOAB.

    Por padrão percorre TODAS as páginas de cada ano (2019 em diante). ``pages_per_year`` > 0 colhe só
    uma amostra distribuída ao longo do ano (1ª página de N dias igualmente espaçados).
    Com ``use_cache`` a coleta é retomável: publicações já inspecionadas não são baixadas de novo.
    """
    target_years = sorted(years or DEOAB_SP_YEARS)
    if not use_cache:
        for yr in target_years:
            (raw_dir / "deoab_sp" / f"{yr}.jsonl").unlink(missing_ok=True)
            (raw_dir / "deoab_sp" / f"{yr}.index.json").unlink(missing_ok=True)

    sem = asyncio.Semaphore(concurrency)
    async with create_async_http_client(timeout=40.0) as client:
        for yr in target_years:
            summary = await fetch_deoab_sp_year(client, yr, sem, raw_dir, max_pages=pages_per_year or None)
            print(
                f"[deoab_sp] {yr}: {summary['candidatos']} candidatos "
                f"(esperado {summary['esperado_por_termo']}), "
                f"{len(summary['paginas_com_falha'])} páginas e {len(summary['publicacoes_com_falha'])} publicações com falha.",
                flush=True,
            )

    pubs = load_deoab_sp_raw(raw_dir, target_years)
    print(f"[deoab_sp] {len(pubs)} acórdãos da OAB/SP disponíveis em {raw_dir / 'deoab_sp'}.")
    return pubs
