"""Fontes próprias das Seccionais (ementários e bases nos sites de cada OAB), fora do DEOAB.

O registro em ``data/fontes/fontes.json`` lista, por UF, cada base conhecida. Cada base coletada tem um
adaptador em ``ted_search/collectors/sites/<uf>_<slug>.py`` com quatro funções:

    async def esperado(client, sem) -> int | None      # total que o próprio site declara (None se não declara)
    async def listar(client, sem) -> list[dict]         # todos os itens, cada um com "id" estável
    async def baixar(client, sem, item) -> dict | None  # item completo, serializável em JSON
    def normalizar(item) -> dict | None                 # registro de ``julgados`` (use montar_registro)

O motor abaixo cuida do resto: cache retomável, carga no banco com fonte ``SITE_<UF>_<SLUG>`` e a linha
no livro-razão ``cobertura_coleta`` (ano 0 = a base inteira), com a mesma regra contábil do DEOAB.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

from ted_search.collectors.deoab_nacional import UF_TO_ORG, save_records_to_database
from ted_search.config import DATA_DIR, DB_PATH, RAW_DIR, create_async_http_client
from ted_search.db import get_connection, rebuild_fts
from ted_search.parser import detect_resultado, detect_sancao, extract_dispositivos, extract_relator_fallback
from ted_search.varredura import COBERTURA_SCHEMA, _agora

FONTES_FILE = DATA_DIR / "fontes" / "fontes.json"
SITUACOES = ("mapeada", "verificada", "adaptador", "coletada", "sem_base", "descartada")


def carregar_fontes() -> dict[str, list[dict[str, Any]]]:
    return json.loads(FONTES_FILE.read_text(encoding="utf-8")) if FONTES_FILE.exists() else {}


def salvar_fontes(fontes: dict[str, list[dict[str, Any]]]) -> None:
    FONTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    FONTES_FILE.write_text(json.dumps(fontes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def nome_fonte(uf: str, slug: str) -> str:
    return f"SITE_{uf.upper()}_{slug.upper().replace('-', '_')}"


def raw_dir_fonte(uf: str, slug: str, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"site_{uf.lower()}_{slug.lower().replace('-', '_')}"


def _adaptador(uf: str, slug: str) -> Any:
    return importlib.import_module(f"ted_search.collectors.sites.{uf.lower()}_{slug.lower().replace('-', '_')}")


def montar_registro(
    uf: str,
    slug: str,
    item_id: str | int,
    *,
    ementa: str,
    url: str,
    orgao_raw: str,
    sigla_orgao: str,
    orgao_norm: str,
    data_decisao: str | None = None,
    acordao: str = "",
    inteiro_teor: str = "",
    numero_processo: str | None = None,
    numero_ementa: str | None = None,
    relator: str | None = None,
    consulta: bool = False,
    publicacao: str | None = None,
) -> dict[str, Any]:
    """Monta um registro de ``julgados`` a partir de um item de site, com os campos derivados do texto."""
    uf = uf.upper()
    sec = UF_TO_ORG[uf]
    fonte = nome_fonte(uf, slug)
    texto = inteiro_teor or "\n\n".join(t for t in (ementa, acordao) if t)
    dispositivos = extract_dispositivos(None, texto)
    ano = int(data_decisao[:4]) if data_decisao and data_decisao[:4].isdigit() else None
    return {
        "id": f"{fonte}:{item_id}",
        "fonte": fonte,
        "contexto": "EMENTA_CONSULTA" if consulta else "EMENTA",
        "tipo_ementa": "CONSULTA" if consulta else "PROCESSO_DISCIPLINAR",
        "numero_processo": numero_processo,
        "numero_ementa": numero_ementa,
        "ano_ementa": ano,
        "id_organizacao": sec["id_organizacao"],
        "nome_organizacao": f"Conselho Seccional - {sec['nome']}",
        "orgao_julgador_raw": orgao_raw,
        "orgao_julgador_norm": orgao_norm,
        "sigla_orgao": sigla_orgao,
        "uf_origem": uf,
        "relator_principal": relator or extract_relator_fallback(None, texto),
        "relatores_json": "[]",
        "partes_json": "[]",
        "dispositivos_json": json.dumps(dispositivos, ensure_ascii=False),
        "dispositivos_texto": " | ".join(dispositivos),
        "sancao_identificada": None if consulta else detect_sancao(texto),
        "resultado_julgamento": None if consulta else detect_resultado(ementa, acordao),
        "descricao_ementa": ementa,
        "descricao_acordao": acordao,
        "inteiro_teor_texto": texto,
        "pdf_url": None,
        "diario_url": url,
        "publicacao_str": publicacao or f"Site da OAB/{uf}",
        "data_decisao": data_decisao,
        "is_ted_disciplinar": 1,
        "tem_inteiro_teor": 1 if (inteiro_teor or acordao) else 0,
    }


async def coletar_fonte(uf: str, slug: str, concurrency: int = 6, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Lista e baixa todos os itens de uma base de site. Retomável: itens já baixados não são refeitos."""
    mod = _adaptador(uf, slug)
    out = raw_dir_fonte(uf, slug, raw_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "itens.jsonl"
    feitos: set[str] = set()
    if jsonl.exists():
        with jsonl.open(encoding="utf-8") as fh:
            feitos = {str(json.loads(line)["id"]) for line in fh if line.strip()}

    sem = asyncio.Semaphore(concurrency)
    falhas: list[str] = []
    async with create_async_http_client(timeout=60.0) as client:
        esperado = await mod.esperado(client, sem)
        itens = await mod.listar(client, sem)
        ids = {str(it["id"]) for it in itens}
        pendentes = [it for it in itens if str(it["id"]) not in feitos]
        lock = asyncio.Lock()
        with jsonl.open("a", encoding="utf-8") as fh:

            async def _um(item: dict[str, Any]) -> None:
                try:
                    completo = await mod.baixar(client, sem, item)
                except Exception:
                    completo = None
                if not completo:
                    falhas.append(str(item["id"]))
                    return
                completo["id"] = item["id"]
                async with lock:
                    fh.write(json.dumps(completo, ensure_ascii=False) + "\n")

            for start in range(0, len(pendentes), 200):
                await asyncio.gather(*(_um(it) for it in pendentes[start : start + 200]))
                fh.flush()
                print(f"[site] {uf}/{slug}: {min(start + 200, len(pendentes))}/{len(pendentes)} itens baixados...", flush=True)

    resumo = {
        "uf": uf.upper(), "fonte": nome_fonte(uf, slug), "esperado": esperado, "listados": len(ids),
        "baixados": len((feitos & ids)) + len(pendentes) - len(falhas),
        "falhas": falhas, "coletado_em": _agora(),
    }
    (out / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumo


def carregar_fonte(uf: str, slug: str, rebuild: bool = True, db_path: Path = DB_PATH, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Normaliza os itens baixados, substitui os registros da fonte no banco e fecha a linha do livro-razão."""
    uf = uf.upper()
    mod = _adaptador(uf, slug)
    fonte = nome_fonte(uf, slug)
    out = raw_dir_fonte(uf, slug, raw_dir)
    resumo = json.loads((out / "resumo.json").read_text(encoding="utf-8"))
    itens: dict[str, dict[str, Any]] = {}
    with (out / "itens.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                it = json.loads(line)
                itens[str(it["id"])] = it
    recs, rejeitados = [], []
    for it in itens.values():
        rec = mod.normalizar(it)
        if rec:
            recs.append(rec)
        else:
            rejeitados.append({"id": it["id"]})
    (out / "rejeitados.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejeitados), encoding="utf-8")

    with get_connection(db_path) as conn:
        conn.executescript(COBERTURA_SCHEMA)
        havia = conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = ?", (fonte,)).fetchone()[0]
        if len(recs) < havia:
            return {"fonte": fonte, "preservado": True, "havia": havia, "coletados": len(recs)}
        with conn:
            conn.execute("DELETE FROM julgado_dispositivos WHERE julgado_id IN (SELECT id FROM julgados WHERE fonte = ?)", (fonte,))
            conn.execute("DELETE FROM julgados WHERE fonte = ?", (fonte,))
    save_records_to_database(recs, db_path, rebuild_fts_after=False)
    with get_connection(db_path) as conn:
        no_banco = conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = ?", (fonte,)).fetchone()[0]
        esperado = resumo.get("esperado")
        fecha = (
            not resumo["falhas"]
            and len(itens) == resumo["listados"]
            and (esperado is None or esperado == resumo["listados"])
            and no_banco + len(rejeitados) == len(itens)
        )
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO cobertura_coleta (uf, fonte, ano, esperado_json, candidatos, inspecionados, decisoes, rejeitados, no_banco, "
                "fundidos, janelas_parciais, falhas, status, auditoria_json, atualizado_em) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, NULL, ?)",
                (uf, fonte, json.dumps({"site": esperado}), resumo["listados"], len(itens), len(itens), len(rejeitados), no_banco,
                 len(resumo["falhas"]), "COMPLETO" if fecha else "PARCIAL", _agora()),
            )
            if rebuild:
                rebuild_fts(conn)
    return {"fonte": fonte, "esperado_no_site": esperado, "listados": resumo["listados"], "baixados": len(itens), "rejeitados": len(rejeitados),
            "no_banco": no_banco, "falhas": len(resumo["falhas"]), "status": "COMPLETO" if fecha else "PARCIAL"}


def relatorio_fontes(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Uma linha por base de site registrada, com a situação e o que há no banco."""
    linhas = []
    with get_connection(db_path) as conn:
        conn.executescript(COBERTURA_SCHEMA)
        for uf, bases in sorted(carregar_fontes().items()):
            for b in bases:
                fonte = nome_fonte(uf, b["slug"]) if b.get("slug") else None
                row = conn.execute("SELECT status, no_banco, esperado_json FROM cobertura_coleta WHERE fonte = ?", (fonte,)).fetchone() if fonte else None
                linhas.append({
                    "uf": uf, "slug": b.get("slug") or "-", "situacao": b.get("situacao", "mapeada"), "exclusiva": b.get("exclusiva"),
                    "conteudo": b.get("conteudo", ""), "periodo": b.get("periodo", ""), "acesso": b.get("acesso", ""),
                    "livro": row["status"] if row else "-", "no_banco": row["no_banco"] if row else 0, "url": b.get("url", ""),
                })
    return linhas
