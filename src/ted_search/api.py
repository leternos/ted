"""Servidor FastAPI para o Buscador de Jurisprudências do CFOAB / TED."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ted_search.collectors.etica_strapi import (
    enrich_record_from_inteiro_teor,
    extract_text_from_pdf_bytes,
)
from ted_search.config import DB_PATH, PDFS_DIR, create_async_http_client
from ted_search.db import (
    get_connection,
    get_facets,
    get_global_stats,
    init_db,
    rebuild_fts,
    search_julgados,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="CFOAB / TED Jurisprudência Search API",
    description="Buscador avançado de jurisprudências ético-disciplinares do Conselho Federal da OAB (1994–2026)",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db(DB_PATH)


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        return get_global_stats(conn)


@app.get("/api/facets")
def api_facets(
    q: str = Query("", description="Termos de busca para filtrar facetas"),
    mode: str = Query("todos", description="Modo de busca: todos, qualquer, frase"),
    apenas_cfoab: bool = Query(True, description="Apenas julgados do Conselho Federal"),
    apenas_ted: bool = Query(False, description="Apenas matéria ético-disciplinar / TED"),
    apenas_inteiro_teor: bool = Query(False, description="Apenas com inteiro teor extraído"),
) -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        return get_facets(
            conn,
            q=q,
            mode=mode,
            apenas_cfoab=apenas_cfoab,
            apenas_ted=apenas_ted,
            apenas_inteiro_teor=apenas_inteiro_teor,
        )


@app.get("/api/search")
def api_search(
    q: str = Query("", description="Consulta textual livre"),
    mode: str = Query("todos", description="Modo: todos (AND), qualquer (OR), frase"),
    apenas_cfoab: bool = Query(True, description="Filtrar apenas Conselho Federal da OAB"),
    apenas_ted: bool = Query(False, description="Filtrar apenas processos ético-disciplinares / 2ª Câmara / TED"),
    apenas_inteiro_teor: bool = Query(False, description="Filtrar apenas julgados com texto integral do voto"),
    contexto: str = Query("", description="EMENTA, EMENTA_CONSULTA, DECISAO_SUMULA"),
    orgao: str = Query("", description="Nome normalizado do órgão julgador"),
    sigla_orgao: str = Query("", description="Sigla do órgão: SCA, SCA-PTU, SCA-STU, SCA-TTU, OEP, COP, PCA, TCA"),
    dispositivo: str = Query("", description="Dispositivo normativo exato (ex: EAOAB - Art. 34 inciso XX)"),
    sancao: str = Query("", description="Sanção identificada"),
    resultado: str = Query("", description="Resultado do julgamento"),
    uf: str = Query("", description="UF da Seccional de origem"),
    relator: str = Query("", description="Nome do Conselheiro Relator"),
    ano_inicio: int | None = Query(None, description="Ano inicial"),
    ano_fim: int | None = Query(None, description="Ano final"),
    numero_processo: str = Query("", description="Número do processo"),
    numero_ementa: str = Query("", description="Número da ementa"),
    sort: str = Query("relevancia", description="Ordenação: relevancia, data_desc, data_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        return search_julgados(
            conn,
            q=q,
            mode=mode,
            apenas_cfoab=apenas_cfoab,
            apenas_ted=apenas_ted,
            apenas_inteiro_teor=apenas_inteiro_teor,
            contexto=contexto,
            orgao=orgao,
            sigla_orgao=sigla_orgao,
            dispositivo=dispositivo,
            sancao=sancao,
            resultado=resultado,
            uf=uf,
            relator=relator,
            ano_inicio=ano_inicio,
            ano_fim=ano_fim,
            numero_processo=numero_processo,
            numero_ementa=numero_ementa,
            sort=sort,
            page=page,
            page_size=page_size,
        )


@app.get("/api/julgado/{julgado_id:path}")
async def api_get_julgado(
    julgado_id: str,
    auto_extract_pdf: bool = Query(True, description="Extrair PDF sob demanda se ainda não extraído"),
) -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM julgados WHERE id = ?", (julgado_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Julgado não encontrado")
        rec = dict(row)

    # Se possui pdf_url mas o texto integral ainda não foi extraído, extrai sob demanda
    if auto_extract_pdf and rec.get("pdf_url") and not rec.get("inteiro_teor_texto"):
        try:
            PDFS_DIR.mkdir(parents=True, exist_ok=True)
            url = rec["pdf_url"]
            url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            cache_pdf = PDFS_DIR / f"{url_hash}.pdf"
            if cache_pdf.exists() and cache_pdf.stat().st_size > 100:
                pdf_bytes = cache_pdf.read_bytes()
            else:
                async with create_async_http_client(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    pdf_bytes = resp.content
                    cache_pdf.write_bytes(pdf_bytes)

            pdf_text = await asyncio.to_thread(extract_text_from_pdf_bytes, pdf_bytes)
            if pdf_text:
                with get_connection(DB_PATH) as conn:
                    with conn:
                        enrich_record_from_inteiro_teor(conn, julgado_id, pdf_text)
                        rebuild_fts(conn)
                    row = conn.execute("SELECT * FROM julgados WHERE id = ?", (julgado_id,)).fetchone()
                    rec = dict(row)
        except Exception as exc:
            rec["pdf_extract_error"] = str(exc)

    rec["dispositivos"] = json.loads(rec.pop("dispositivos_json") or "[]")
    rec["relatores"] = json.loads(rec.pop("relatores_json") or "[]")
    rec["partes"] = json.loads(rec.pop("partes_json") or "[]")
    rec["is_ted_disciplinar"] = bool(rec["is_ted_disciplinar"])
    rec["tem_inteiro_teor"] = bool(rec["tem_inteiro_teor"])
    return rec


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
