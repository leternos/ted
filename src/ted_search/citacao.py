"""Texto como publicado: o que aparece na caixa do resultado e na citação copiada.

Regra pedida pelo cliente (nov/2026):
- julgado do Conselho Federal: o texto exatamente como o Portal de Jurisprudência do CFOAB o mostra
  (campo `descricaoEmenta` da API, sem cortes nem separação de ementa e acórdão), guardado em `texto_publicado`;
- julgado de Seccional: o texto da publicação no Diário como saiu (`inteiro_teor_texto` das fontes DEOAB_*);
- quando o mesmo processo tem julgado no Conselho Federal, o resultado da Seccional mostra também esse texto.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ted_search.config import RAW_DIR

ARQ_OAB_JURIS = "oab_juris_ementa.json"
LIMITE_NA_LISTA = 6000  # caracteres por resultado na listagem; a ficha traz o texto inteiro

# Expressão SQL do texto como publicado de um julgado `j`
SQL_TEXTO_PUBLICADO = (
    "COALESCE(j.texto_publicado, CASE WHEN j.fonte LIKE 'DEOAB_%' OR j.fonte LIKE 'SITE_%' THEN j.inteiro_teor_texto END)"
)


def garantir_esquema(conn: sqlite3.Connection) -> None:
    if "texto_publicado" not in {r[1] for r in conn.execute("PRAGMA table_info(julgados)")}:
        conn.execute("ALTER TABLE julgados ADD COLUMN texto_publicado TEXT")


def gravar_texto_cfoab(conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    """Grava em `texto_publicado` o texto original do Portal de Jurisprudência para cada julgado OAB_JURIS."""
    from ted_search.collectors.oab_juris import normalize_oab_juris_item

    garantir_esquema(conn)
    arq = raw_dir / ARQ_OAB_JURIS
    n = {"no_bruto": 0, "gravados": 0, "sem_registro_no_banco": 0}
    if not arq.exists():
        return n
    existentes = {r[0] for r in conn.execute("SELECT id FROM julgados WHERE fonte = 'OAB_JURIS'")}
    with conn:
        for bruto in json.loads(arq.read_text(encoding="utf-8")):
            original = (bruto.get("descricaoEmenta") or "").strip()
            if not original:
                continue
            n["no_bruto"] += 1
            jid = normalize_oab_juris_item(bruto, "EMENTA")["id"]
            if jid in existentes:
                conn.execute("UPDATE julgados SET texto_publicado = ? WHERE id = ?", (original, jid))
                n["gravados"] += 1
            else:
                n["sem_registro_no_banco"] += 1
    n["oab_juris_sem_texto_original"] = conn.execute(
        "SELECT COUNT(*) FROM julgados WHERE fonte = 'OAB_JURIS' AND texto_publicado IS NULL"
    ).fetchone()[0]
    return n


def cfoab_no_processo(conn: sqlite3.Connection, itens: list[dict[str, Any]]) -> None:
    """Acrescenta a cada resultado de Seccional os julgados do Conselho Federal no mesmo processo (texto do portal)."""
    alvo = {i["numero_processo"] for i in itens if i.get("numero_processo") and i.get("fonte") not in ("OAB_JURIS", "ETICA_STRAPI")}
    por_processo: dict[str, list[dict[str, Any]]] = {}
    if alvo:
        marcas = ",".join("?" * len(alvo))
        for r in conn.execute(
            f"""
            SELECT id, numero_processo, numero_ementa, orgao_julgador_norm, data_decisao,
                   COALESCE(texto_publicado, descricao_ementa) AS texto
            FROM julgados
            WHERE fonte = 'OAB_JURIS' AND (id_organizacao = 49 OR id_organizacao IS NULL) AND numero_processo IN ({marcas})
            ORDER BY COALESCE(data_decisao, '') DESC
            """,
            list(alvo),
        ):
            d = dict(zip(("id", "numero_processo", "numero_ementa", "orgao", "data_decisao", "texto"), r))
            por_processo.setdefault(d.pop("numero_processo"), []).append(d)
    for i in itens:
        i["cfoab_no_processo"] = por_processo.get(i.get("numero_processo") or "", []) if i.get("fonte") not in ("OAB_JURIS", "ETICA_STRAPI") else []
