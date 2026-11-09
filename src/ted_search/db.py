"""Camada de persistência e busca Full-Text (SQLite + FTS5 com ranking BM25)."""

from __future__ import annotations

import gzip
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ted_search.config import DB_PATH

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS julgados (
    id TEXT PRIMARY KEY,
    fonte TEXT NOT NULL,
    contexto TEXT NOT NULL,
    tipo_ementa TEXT,
    numero_processo TEXT,
    numero_ementa TEXT,
    ano_ementa INTEGER,
    id_organizacao INTEGER,
    nome_organizacao TEXT,
    orgao_julgador_raw TEXT,
    orgao_julgador_norm TEXT,
    sigla_orgao TEXT,
    uf_origem TEXT,
    relator_principal TEXT,
    relatores_json TEXT,
    partes_json TEXT,
    dispositivos_json TEXT,
    dispositivos_texto TEXT,
    sancao_identificada TEXT,
    resultado_julgamento TEXT,
    descricao_ementa TEXT,
    descricao_acordao TEXT,
    inteiro_teor_texto TEXT,
    pdf_url TEXT,
    diario_url TEXT,
    publicacao_str TEXT,
    data_decisao TEXT,
    is_ted_disciplinar INTEGER DEFAULT 0,
    tem_inteiro_teor INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_julgados_orgao ON julgados(orgao_julgador_norm);
CREATE INDEX IF NOT EXISTS idx_julgados_sigla ON julgados(sigla_orgao);
CREATE INDEX IF NOT EXISTS idx_julgados_ano ON julgados(ano_ementa);
CREATE INDEX IF NOT EXISTS idx_julgados_uf ON julgados(uf_origem);
CREATE INDEX IF NOT EXISTS idx_julgados_sancao ON julgados(sancao_identificada);
CREATE INDEX IF NOT EXISTS idx_julgados_resultado ON julgados(resultado_julgamento);
CREATE INDEX IF NOT EXISTS idx_julgados_ted ON julgados(is_ted_disciplinar);
CREATE INDEX IF NOT EXISTS idx_julgados_inteiro_teor ON julgados(tem_inteiro_teor);
CREATE INDEX IF NOT EXISTS idx_julgados_num_ementa ON julgados(numero_ementa);
CREATE INDEX IF NOT EXISTS idx_julgados_num_processo ON julgados(numero_processo);
CREATE INDEX IF NOT EXISTS idx_julgados_data ON julgados(data_decisao DESC);

CREATE TABLE IF NOT EXISTS julgado_dispositivos (
    julgado_id TEXT NOT NULL,
    dispositivo TEXT NOT NULL,
    PRIMARY KEY (julgado_id, dispositivo)
);

CREATE INDEX IF NOT EXISTS idx_disp_nome ON julgado_dispositivos(dispositivo);

CREATE VIRTUAL TABLE IF NOT EXISTS julgados_fts USING fts5(
    id UNINDEXED,
    numero_processo,
    numero_ementa,
    orgao_julgador_norm,
    relator_principal,
    dispositivos_texto,
    descricao_ementa,
    descricao_acordao,
    inteiro_teor_texto,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    gz_path = db_path.with_name(f"{db_path.name}.gz")
    if (not db_path.exists() or db_path.stat().st_size == 0) and gz_path.exists():
        with gzip.open(gz_path, "rb") as f_in, open(db_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Reconstrói a tabela virtual FTS5 a partir da tabela principal julgados."""
    conn.execute("DELETE FROM julgados_fts;")
    conn.execute(
        """
        INSERT INTO julgados_fts (
            rowid, id, numero_processo, numero_ementa, orgao_julgador_norm,
            relator_principal, dispositivos_texto, descricao_ementa,
            descricao_acordao, inteiro_teor_texto
        )
        SELECT
            rowid, id, COALESCE(numero_processo, ''), COALESCE(numero_ementa, ''),
            COALESCE(orgao_julgador_norm, ''), COALESCE(relator_principal, ''),
            COALESCE(dispositivos_texto, ''), COALESCE(descricao_ementa, ''),
            COALESCE(descricao_acordao, ''), COALESCE(inteiro_teor_texto, '')
        FROM julgados;
        """
    )


def build_fts_query(raw_query: str, mode: str = "todos") -> str | None:
    """Converte uma consulta do usuário em expressão segura e rica para SQLite FTS5."""
    q = (raw_query or "").strip()
    if not q:
        return None

    if mode == "frase":
        escaped = q.replace('"', '""')
        return f'"{escaped}"'

    # Preservar frases entre aspas se o usuário digitou explicitamente
    phrases: list[str] = []

    def _save_phrase(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if inner:
            phrases.append(f'"{inner}"')
        return " "

    q_without_quotes = re.sub(r'"([^"]+)"', _save_phrase, q)
    # Extrair tokens alfanuméricos + hífen/asterisco
    raw_tokens = re.findall(r"[A-Za-zÀ-ÿ0-9]+(?:\*)?", q_without_quotes)
    # Filtrar stopwords muito curtas exceto algarismos/romanos
    stopwords = {"de", "da", "do", "das", "dos", "em", "na", "no", "nas", "nos", "e", "ou", "a", "o", "as", "os", "por", "para", "com"}
    tokens: list[str] = []
    for t in raw_tokens:
        t_low = t.lower()
        if t_low in {"and", "or", "not", "near"}:
            continue
        if len(t) == 1 and not t.isdigit() and t.upper() not in {"I", "V", "X"}:
            continue
        if t_low in stopwords and len(raw_tokens) > 2:
            continue
        if t.endswith("*"):
            base_tok = re.sub(r"[^A-Za-zÀ-ÿ0-9]", "", t[:-1])
            if base_tok:
                tokens.append(f"{base_tok}*")
        else:
            tokens.append(f'"{t}"')

    all_parts = phrases + tokens
    if not all_parts:
        return None

    joiner = " OR " if mode == "qualquer" else " AND "
    return joiner.join(all_parts)


def _apply_esfera_filter(where_clauses: list[str], esfera: str, apenas_cfoab: bool) -> None:
    esf = (esfera or "").strip().upper()
    if esf == "CFOAB":
        where_clauses.append("(j.id_organizacao = 49 OR j.id_organizacao IS NULL)")
    elif esf == "OABSP":
        where_clauses.append("j.id_organizacao = 25")
    elif esf == "OABSP_TURMAS":
        where_clauses.append("j.id_organizacao = 25 AND (j.sigla_orgao LIKE 'OABSP-%TD%' OR j.sigla_orgao = 'OABSP-TED')")
    elif esf == "OABSP_RECURSOS":
        where_clauses.append("j.id_organizacao = 25 AND j.sigla_orgao LIKE 'OABSP-%CR%'")
    elif esf == "TODAS":
        pass
    elif apenas_cfoab:
        where_clauses.append("(j.id_organizacao = 49 OR j.id_organizacao IS NULL)")


def search_julgados(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    mode: str = "todos",
    esfera: str = "",
    apenas_cfoab: bool = False,
    apenas_ted: bool = False,
    apenas_com_acordao: bool = False,
    apenas_inteiro_teor: bool = False,
    contexto: str = "",
    orgao: str = "",
    sigla_orgao: str = "",
    dispositivo: str = "",
    sancao: str = "",
    resultado: str = "",
    uf: str = "",
    relator: str = "",
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
    numero_processo: str = "",
    numero_ementa: str = "",
    sort: str = "relevancia",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Executa busca híbrida (FTS5 BM25 + filtros facetados) com snippets destacados."""
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size

    fts_expr = build_fts_query(q, mode=mode)
    where_clauses: list[str] = []
    params: list[Any] = []

    if fts_expr:
        where_clauses.append("julgados_fts MATCH ?")
        params.append(fts_expr)

    _apply_esfera_filter(where_clauses, esfera, apenas_cfoab)

    if apenas_ted:
        where_clauses.append("j.is_ted_disciplinar = 1")

    if apenas_com_acordao:
        where_clauses.append(
            "((j.descricao_acordao IS NOT NULL AND j.descricao_acordao != '') OR j.tem_inteiro_teor = 1)"
        )

    if apenas_inteiro_teor:
        where_clauses.append("j.tem_inteiro_teor = 1")

    if contexto:
        where_clauses.append("j.contexto = ?")
        params.append(contexto)

    if orgao:
        where_clauses.append("j.orgao_julgador_norm = ?")
        params.append(orgao)

    if sigla_orgao:
        where_clauses.append("j.sigla_orgao = ?")
        params.append(sigla_orgao)

    if dispositivo:
        where_clauses.append(
            "EXISTS (SELECT 1 FROM julgado_dispositivos jd WHERE jd.julgado_id = j.id AND jd.dispositivo = ?)"
        )
        params.append(dispositivo)

    if sancao:
        where_clauses.append("j.sancao_identificada = ?")
        params.append(sancao)

    if resultado:
        where_clauses.append("j.resultado_julgamento = ?")
        params.append(resultado)

    if uf:
        where_clauses.append("j.uf_origem = ?")
        params.append(uf.upper())

    if relator:
        where_clauses.append("j.relator_principal LIKE ?")
        params.append(f"%{relator.strip().upper()}%")

    if ano_inicio is not None:
        where_clauses.append("j.ano_ementa >= ?")
        params.append(ano_inicio)

    if ano_fim is not None:
        where_clauses.append("j.ano_ementa <= ?")
        params.append(ano_fim)

    if numero_processo:
        clean_proc = re.sub(r"\D", "", numero_processo)
        where_clauses.append("(j.numero_processo LIKE ? OR REPLACE(REPLACE(j.numero_processo, '.', ''), '-', '') LIKE ?)")
        params.extend([f"%{numero_processo.strip()}%", f"%{clean_proc}%"])

    if numero_ementa:
        where_clauses.append("j.numero_ementa LIKE ?")
        params.append(f"%{numero_ementa.strip().upper()}%")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    if fts_expr:
        from_sql = "FROM julgados_fts JOIN julgados j ON julgados_fts.rowid = j.rowid"
        # Pesos BM25: numero_processo(3), numero_ementa(5), orgao(2), relator(2), dispositivos(4), ementa(3), acordao(1.5), inteiro_teor(2)
        score_expr = "bm25(julgados_fts, 0.0, 3.0, 5.0, 2.0, 2.0, 4.0, 3.0, 1.5, 2.0)"
        highlight_ementa = "highlight(julgados_fts, 6, '<mark>', '</mark>')"
        snippet_inteiro_teor = "snippet(julgados_fts, 8, '<mark>', '</mark>', ' … ', 42)"
    else:
        from_sql = "FROM julgados j"
        score_expr = "0.0"
        highlight_ementa = "j.descricao_ementa"
        snippet_inteiro_teor = "SUBSTR(j.inteiro_teor_texto, 1, 360)"

    # Contagem total
    count_sql = f"SELECT COUNT(*) AS total {from_sql} WHERE {where_sql}"
    total = conn.execute(count_sql, params).fetchone()["total"]

    # Ordenação
    if sort == "data_asc":
        order_sql = "ORDER BY COALESCE(j.data_decisao, '') ASC, j.ano_ementa ASC"
    elif sort == "data_desc" or not fts_expr:
        order_sql = "ORDER BY COALESCE(j.data_decisao, '') DESC, COALESCE(j.ano_ementa, 0) DESC, j.numero_ementa DESC"
    else:
        # Relevância BM25 (em SQLite FTS5, quanto menor/mais negativo o valor de bm25, mais relevante)
        order_sql = f"ORDER BY {score_expr} ASC, COALESCE(j.data_decisao, '') DESC"

    select_sql = f"""
        SELECT
            j.id,
            j.fonte,
            j.contexto,
            j.tipo_ementa,
            j.numero_processo,
            j.numero_ementa,
            j.ano_ementa,
            j.id_organizacao,
            j.nome_organizacao,
            j.orgao_julgador_raw,
            j.orgao_julgador_norm,
            j.sigla_orgao,
            j.uf_origem,
            j.relator_principal,
            j.dispositivos_json,
            j.sancao_identificada,
            j.resultado_julgamento,
            j.descricao_ementa,
            {highlight_ementa} AS ementa_highlighted,
            j.descricao_acordao,
            {snippet_inteiro_teor} AS inteiro_teor_snippet,
            j.pdf_url,
            j.diario_url,
            j.publicacao_str,
            j.data_decisao,
            j.is_ted_disciplinar,
            j.tem_inteiro_teor,
            {score_expr} AS bm25_score
        {from_sql}
        WHERE {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(select_sql, [*params, page_size, offset]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["dispositivos"] = json.loads(d.pop("dispositivos_json") or "[]")
        d["is_ted_disciplinar"] = bool(d["is_ted_disciplinar"])
        d["tem_inteiro_teor"] = bool(d["tem_inteiro_teor"])
        d["tem_acordao"] = bool((d.get("descricao_acordao") or "").strip() or d["tem_inteiro_teor"])
        items.append(d)

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
        "query_fts": fts_expr,
        "items": items,
    }


def get_facets(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    mode: str = "todos",
    esfera: str = "",
    apenas_cfoab: bool = False,
    apenas_ted: bool = False,
    apenas_com_acordao: bool = False,
    apenas_inteiro_teor: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Retorna agregações dinâmicas para os filtros da barra lateral."""
    fts_expr = build_fts_query(q, mode=mode)
    where_clauses: list[str] = []
    params: list[Any] = []

    if fts_expr:
        from_sql = "FROM julgados_fts JOIN julgados j ON julgados_fts.rowid = j.rowid"
        where_clauses.append("julgados_fts MATCH ?")
        params.append(fts_expr)
    else:
        from_sql = "FROM julgados j"

    _apply_esfera_filter(where_clauses, esfera, apenas_cfoab)
    if apenas_ted:
        where_clauses.append("j.is_ted_disciplinar = 1")
    if apenas_com_acordao:
        where_clauses.append(
            "((j.descricao_acordao IS NOT NULL AND j.descricao_acordao != '') OR j.tem_inteiro_teor = 1)"
        )
    if apenas_inteiro_teor:
        where_clauses.append("j.tem_inteiro_teor = 1")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    def _agg(col: str, limit: int = 40) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {col} AS valor, COUNT(*) AS total
            {from_sql}
            WHERE {where_sql} AND {col} IS NOT NULL AND {col} != ''
            GROUP BY {col}
            ORDER BY total DESC
            LIMIT {limit}
        """
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Dispositivos top
    disp_sql = f"""
        SELECT jd.dispositivo AS valor, COUNT(*) AS total
        {from_sql}
        JOIN julgado_dispositivos jd ON jd.julgado_id = j.id
        WHERE {where_sql}
        GROUP BY jd.dispositivo
        ORDER BY total DESC
        LIMIT 35
    """
    dispositivos = [dict(r) for r in conn.execute(disp_sql, params).fetchall()]

    # Anos ordenados decrescentemente
    anos_sql = f"""
        SELECT j.ano_ementa AS valor, COUNT(*) AS total
        {from_sql}
        WHERE {where_sql} AND j.ano_ementa IS NOT NULL AND j.ano_ementa >= 1990 AND j.ano_ementa <= 2030
        GROUP BY j.ano_ementa
        ORDER BY j.ano_ementa DESC
    """
    anos = [dict(r) for r in conn.execute(anos_sql, params).fetchall()]

    return {
        "orgaos": _agg("j.orgao_julgador_norm", 45),
        "siglas_orgao": _agg("j.sigla_orgao", 45),
        "dispositivos": dispositivos,
        "sancoes": _agg("j.sancao_identificada", 15),
        "resultados": _agg("j.resultado_julgamento", 15),
        "ufs": _agg("j.uf_origem", 30),
        "relatores": _agg("j.relator_principal", 30),
        "anos": anos,
        "contextos": _agg("j.contexto", 10),
    }


def get_global_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Retorna métricas consolidadas do acervo (CFOAB + Seccional OAB/SP) e do enriquecimento."""
    total_geral = conn.execute("SELECT COUNT(*) FROM julgados").fetchone()[0]
    total_cfoab = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE id_organizacao = 49 OR id_organizacao IS NULL"
    ).fetchone()[0]
    total_oabsp = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE id_organizacao = 25"
    ).fetchone()[0]
    total_oabsp_turmas = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE id_organizacao = 25 AND (sigla_orgao LIKE 'OABSP-%TD%' OR sigla_orgao = 'OABSP-TED')"
    ).fetchone()[0]
    total_oabsp_recursos = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE id_organizacao = 25 AND sigla_orgao LIKE 'OABSP-%CR%'"
    ).fetchone()[0]
    total_ted = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE is_ted_disciplinar = 1"
    ).fetchone()[0]
    total_com_acordao = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE (descricao_acordao IS NOT NULL AND descricao_acordao != '') OR tem_inteiro_teor = 1"
    ).fetchone()[0]
    total_com_pdf = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE pdf_url IS NOT NULL AND pdf_url != ''"
    ).fetchone()[0]
    total_inteiro_teor_indexado = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE tem_inteiro_teor = 1"
    ).fetchone()[0]
    total_orgao_especifico = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE sigla_orgao != 'CFOAB' AND (id_organizacao = 49 OR id_organizacao IS NULL)"
    ).fetchone()[0]
    total_com_dispositivos = conn.execute(
        "SELECT COUNT(DISTINCT julgado_id) FROM julgado_dispositivos"
    ).fetchone()[0]
    total_com_resultado = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE resultado_julgamento IS NOT NULL"
    ).fetchone()[0]
    total_com_sancao = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE sancao_identificada IS NOT NULL"
    ).fetchone()[0]

    por_orgao = [
        dict(r)
        for r in conn.execute(
            """
            SELECT sigla_orgao, orgao_julgador_norm, COUNT(*) AS total
            FROM julgados
            WHERE id_organizacao = 49 OR id_organizacao IS NULL
            GROUP BY sigla_orgao, orgao_julgador_norm
            ORDER BY total DESC
            """
        ).fetchall()
    ]

    por_orgao_oabsp = [
        dict(r)
        for r in conn.execute(
            """
            SELECT sigla_orgao, orgao_julgador_norm, COUNT(*) AS total
            FROM julgados
            WHERE id_organizacao = 25
            GROUP BY sigla_orgao, orgao_julgador_norm
            ORDER BY total DESC
            """
        ).fetchall()
    ]

    return {
        "total_geral": total_geral,
        "total_cfoab": total_cfoab,
        "total_oabsp": total_oabsp,
        "total_oabsp_turmas": total_oabsp_turmas,
        "total_oabsp_recursos": total_oabsp_recursos,
        "total_ted_disciplinar": total_ted,
        "total_com_acordao": total_com_acordao,
        "total_com_pdf_url": total_com_pdf,
        "total_inteiro_teor_extraido": total_inteiro_teor_indexado,
        "total_orgao_especifico_identificado": total_orgao_especifico,
        "total_com_dispositivos_extraidos": total_com_dispositivos,
        "total_com_resultado_extraido": total_com_resultado,
        "total_com_sancao_identificada": total_com_sancao,
        "por_orgao": por_orgao,
        "por_orgao_oabsp": por_orgao_oabsp,
    }
