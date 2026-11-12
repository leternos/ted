"""Servidor FastAPI para o Buscador Nacional de Jurisprudência Ético-Disciplinar da OAB (CFOAB + Seccionais)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ted_search.collectors.deoab_sp import html_to_paragraphs, resolve_deoab_pdf_url
from ted_search.collectors.etica_strapi import (
    enrich_record_from_inteiro_teor,
    extract_text_from_pdf_bytes,
)
from ted_search.config import (
    AUTH_SESSION_COOKIE,
    AUTH_TOKEN,
    DB_PATH,
    DEOAB_API_BASE_URL,
    PDFS_DIR,
    TED_ACCESS_PASSWORD,
    create_async_http_client,
)
from ted_search.db import (
    get_connection,
    get_facets,
    get_global_stats,
    get_seccionais_status,
    init_db,
    rebuild_fts,
    search_julgados,
)

from contextlib import asynccontextmanager

STATIC_DIR = Path(__file__).resolve().parent / "static"


class LoginRequest(BaseModel):
    password: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DB_PATH)
    yield


app = FastAPI(
    title="Jurisprudência Ético-Disciplinar da OAB — CFOAB e Seccionais",
    description="Buscador nacional de jurisprudência ético-disciplinar: Conselho Federal e as 27 Seccionais (TED — 1ª instância e Câmaras Recursais — 2ª instância)",
    version="0.3.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Bloqueia rotas de API sem a senha de acesso configurada."""
    if not TED_ACCESS_PASSWORD:
        return await call_next(request)

    path = request.url.path
    if (
        path in ("/health", "/", "/favicon.ico")
        or path.startswith("/static")
        or path.startswith("/api/auth")
    ):
        return await call_next(request)

    token_cookie = request.cookies.get(AUTH_SESSION_COOKIE)
    token_header = request.headers.get("X-Auth-Token")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""

    if (
        (token_cookie and token_cookie == AUTH_TOKEN)
        or (token_header and token_header == AUTH_TOKEN)
        or (bearer_token and bearer_token == AUTH_TOKEN)
    ):
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Acesso restrito. Digite a senha para continuar."},
    )


@app.get("/health")
def healthcheck() -> dict[str, str]:
    """Endpoint de checagem de integridade (liveness/readiness probe para Cloud Run)."""
    return {"status": "ok", "version": "0.3.0"}


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    """Retorna se a sessão atual está autenticada."""
    if not TED_ACCESS_PASSWORD:
        return {"authenticated": True, "required": False}

    token_cookie = request.cookies.get(AUTH_SESSION_COOKIE)
    token_header = request.headers.get("X-Auth-Token")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""

    is_auth = bool(
        (token_cookie and token_cookie == AUTH_TOKEN)
        or (token_header and token_header == AUTH_TOKEN)
        or (bearer_token and bearer_token == AUTH_TOKEN)
    )
    return {"authenticated": is_auth, "required": True}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, response: Response) -> dict[str, Any]:
    """Autentica o usuário com a senha do sistema."""
    if not TED_ACCESS_PASSWORD or req.password.strip() == TED_ACCESS_PASSWORD:
        response.set_cookie(
            key=AUTH_SESSION_COOKIE,
            value=AUTH_TOKEN,
            max_age=30 * 24 * 3600,
            httponly=False,
            samesite="lax",
            path="/",
        )
        return {"authenticated": True, "token": AUTH_TOKEN}
    raise HTTPException(status_code=401, detail="Senha incorreta. Tente novamente.")


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    """Encerra a sessão e apaga o cookie de autenticação."""
    response.delete_cookie(key=AUTH_SESSION_COOKIE, path="/")
    return {"authenticated": False}



@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        return get_global_stats(conn)


@app.get("/api/seccionais")
def api_seccionais() -> list[dict[str, Any]]:
    """Cobertura nacional das 27 Seccionais (ativas / em breve) com contagem por instância."""
    with get_connection(DB_PATH) as conn:
        return get_seccionais_status(conn)


@app.get("/api/facets")
def api_facets(
    q: str = Query("", description="Termos de busca para filtrar facetas"),
    mode: str = Query("todos", description="Modo de busca: todos, qualquer, frase"),
    esfera: str = Query("TODAS", description="TODAS, CFOAB, SECCIONAIS (aliases legados: OABSP, OABSP_TURMAS, OABSP_RECURSOS)"),
    seccional: str = Query("", description="UF da Seccional julgadora (ex: SP, SE, RJ)"),
    instancia: str = Query("", description="Instância nas Seccionais: TED (1ª instância — Turmas) ou RECURSAL (2ª instância — Câmaras)"),
    apenas_cfoab: bool = Query(False, description="Apenas julgados do Conselho Federal"),
    apenas_ted: bool = Query(False, description="Apenas matéria ético-disciplinar / TED"),
    apenas_com_acordao: bool = Query(False, description="Priorizar decisões com Acórdão"),
    apenas_inteiro_teor: bool = Query(False, description="Apenas com inteiro teor extraído"),
) -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        return get_facets(
            conn,
            q=q,
            mode=mode,
            esfera=esfera,
            seccional=seccional,
            instancia=instancia,
            apenas_cfoab=apenas_cfoab,
            apenas_ted=apenas_ted,
            apenas_com_acordao=apenas_com_acordao,
            apenas_inteiro_teor=apenas_inteiro_teor,
        )


@app.get("/api/search")
def api_search(
    q: str = Query("", description="Consulta textual livre"),
    mode: str = Query("todos", description="Modo: todos (AND), qualquer (OR), frase"),
    esfera: str = Query("TODAS", description="TODAS, CFOAB, SECCIONAIS (aliases legados: OABSP, OABSP_TURMAS, OABSP_RECURSOS)"),
    seccional: str = Query("", description="UF da Seccional julgadora (ex: SP, SE, RJ)"),
    instancia: str = Query("", description="Instância nas Seccionais: TED (1ª instância — Turmas) ou RECURSAL (2ª instância — Câmaras)"),
    apenas_cfoab: bool = Query(False, description="Filtrar apenas Conselho Federal da OAB"),
    apenas_ted: bool = Query(False, description="Filtrar apenas processos ético-disciplinares / TED"),
    apenas_com_acordao: bool = Query(False, description="Filtrar prioritariamente decisões com Acórdão"),
    apenas_inteiro_teor: bool = Query(False, description="Filtrar apenas julgados com texto integral do voto"),
    contexto: str = Query("", description="EMENTA, EMENTA_CONSULTA, DECISAO_SUMULA"),
    orgao: str = Query("", description="Nome normalizado do órgão julgador"),
    sigla_orgao: str = Query("", description="Sigla do órgão (SCA, OABSP-1TD, OABSP-5CR, etc.)"),
    dispositivo: str = Query("", description="Dispositivo normativo exato (ex: EAOAB - Art. 34 inciso XX)"),
    sancao: str = Query("", description="Sanção identificada"),
    resultado: str = Query("", description="Resultado do julgamento"),
    uf: str = Query("", description="UF da Seccional de origem"),
    relator: str = Query("", description="Nome do Relator"),
    ano_inicio: int | None = Query(None, description="Ano inicial"),
    ano_fim: int | None = Query(None, description="Ano final"),
    numero_processo: str = Query("", description="Número do processo"),
    numero_ementa: str = Query("", description="Número da ementa / acórdão"),
    sort: str = Query("relevancia", description="Ordenação: relevancia, data_desc, data_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        return search_julgados(
            conn,
            q=q,
            mode=mode,
            esfera=esfera,
            seccional=seccional,
            instancia=instancia,
            apenas_cfoab=apenas_cfoab,
            apenas_ted=apenas_ted,
            apenas_com_acordao=apenas_com_acordao,
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


@app.get("/api/diario-pdf/{materia_id}")
async def api_diario_pdf(
    materia_id: int,
    redirect: bool = Query(False, description="Se true, redireciona 302 para a URL direta na OAB em vez de fazer stream inline"),
) -> Response:
    """Gera/obtém e transmite o PDF oficial do Inteiro Teor publicado no Diário Eletrônico da OAB."""
    PDFS_DIR.mkdir(parents=True, exist_ok=True)
    cache_pdf = PDFS_DIR / f"deoab_{materia_id}.pdf"
    if not redirect and cache_pdf.exists() and cache_pdf.stat().st_size > 500:
        return Response(
            content=cache_pdf.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="acordao_oab_{materia_id}.pdf"'},
        )

    direct_url = await resolve_deoab_pdf_url(materia_id)
    if not direct_url:
        raise HTTPException(status_code=404, detail="PDF do Diário Eletrônico não localizado para esta publicação")

    if redirect:
        return RedirectResponse(url=direct_url, status_code=302)

    async with create_async_http_client(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(direct_url)
        resp.raise_for_status()
        pdf_bytes = resp.content
        if pdf_bytes.startswith(b"%PDF"):
            cache_pdf.write_bytes(pdf_bytes)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="acordao_oab_{materia_id}.pdf"'},
            )
    return RedirectResponse(url=direct_url, status_code=302)


@app.get("/api/julgado/{julgado_id:path}")
async def api_get_julgado(
    julgado_id: str,
    auto_extract_pdf: bool = Query(True, description="Extrair PDF/Inteiro Teor sob demanda se ainda não extraído"),
) -> dict[str, Any]:
    with get_connection(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM julgados WHERE id = ?", (julgado_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Julgado não encontrado")
        rec = dict(row)

    pdf_url = rec.get("pdf_url") or ""

    # Se for publicação do DEOAB (/api/diario-pdf/{id}) e ainda não tiver inteiro_teor_texto, busca o conteúdo oficial
    if auto_extract_pdf and pdf_url.startswith("/api/diario-pdf/") and not rec.get("inteiro_teor_texto"):
        materia_id = pdf_url.rsplit("/", 1)[-1]
        try:
            async with create_async_http_client(timeout=20.0) as client:
                r_pub = await client.get(f"{DEOAB_API_BASE_URL}/publicacao/{materia_id}/publicado")
                if r_pub.status_code == 200:
                    pub_data = r_pub.json()
                    full_txt = html_to_paragraphs(pub_data.get("conteudoCodificado") or pub_data.get("conteudo") or "")
                    if full_txt:
                        with get_connection(DB_PATH) as conn:
                            with conn:
                                enrich_record_from_inteiro_teor(conn, julgado_id, full_txt)
                            row = conn.execute("SELECT * FROM julgados WHERE id = ?", (julgado_id,)).fetchone()
                            rec = dict(row)
        except Exception as exc:
            rec["pdf_extract_error"] = str(exc)

    # Se possui pdf_url externo (Azure Strapi) mas o texto integral ainda não foi extraído, extrai sob demanda
    elif auto_extract_pdf and pdf_url.startswith("https://") and not rec.get("inteiro_teor_texto"):
        try:
            PDFS_DIR.mkdir(parents=True, exist_ok=True)
            url_hash = hashlib.sha1(pdf_url.encode("utf-8")).hexdigest()[:16]
            cache_pdf = PDFS_DIR / f"{url_hash}.pdf"
            if cache_pdf.exists() and cache_pdf.stat().st_size > 100:
                pdf_bytes = cache_pdf.read_bytes()
            else:
                async with create_async_http_client(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(pdf_url)
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
    rec["tem_acordao"] = bool((rec.get("descricao_acordao") or "").strip() or rec["tem_inteiro_teor"])
    return rec


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
