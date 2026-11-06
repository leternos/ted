"""Coletor para o Portal Ética e Disciplina (services.oab.org.br/etica-disciplina) e extrator de PDFs.

1. Coleta os 1.402 acórdãos selecionados da 2ª Câmara e 1ª/2ª/3ª Turmas no Strapi.
2. Reconcilia com os registros da API Nacional pelo número da ementa normalizado (ex: 001/2017/SCA)
   ou cria novos registros quando exclusivos do portal de Ética e Disciplina.
3. Faz o download concorrente dos PDFs de inteiro teor (Relatório + Voto + Acórdão) no Azure Blob
   Storage e extrai o texto integral com pypdf para indexação no SQLite FTS5.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader

from ted_search.config import (
    ETICA_STRAPI_EMENTARIOS_URL,
    ETICA_STRAPI_PUBLIC_TOKEN,
    PDFS_DIR,
    RAW_DIR,
    create_async_http_client,
)
from ted_search.ocr_cleaner import is_gibberish_ocr_line
from ted_search.parser import (
    clean_text,
    detect_orgao_julgador,
    detect_resultado,
    detect_sancao,
    detect_uf_origem,
    extract_dispositivos,
    extract_relator_fallback,
    normalize_numero_ementa,
)

RE_PROC_IN_TEXT = re.compile(r"\b(\d{2}\.\d{4}\.(?:19\d{2}|20\d{2})\.\d{6}-\d)\b")


async def fetch_etica_strapi_all(
    use_cache: bool = True,
    raw_dir: Path = RAW_DIR,
) -> list[dict[str, Any]]:
    """Baixa todos os registros de /api/ementarios do Strapi do Portal Ética e Disciplina."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_file = raw_dir / "etica_strapi_ementarios.json"
    if use_cache and cache_file.exists():
        print(f"[cache] Carregando Strapi Ética e Disciplina de {cache_file}...")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    all_items: list[dict[str, Any]] = []
    headers = {
        "Authorization": f"Bearer {ETICA_STRAPI_PUBLIC_TOKEN}",
        "Accept": "application/json",
    }

    async with create_async_http_client(headers=headers) as client:
        page = 1
        page_size = 500
        while True:
            params = {
                "populate": "*",
                "sort[0]": "numero:asc",
                "pagination[page]": str(page),
                "pagination[pageSize]": str(page_size),
            }
            resp = await client.get(ETICA_STRAPI_EMENTARIOS_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("data") or []
            meta = (payload.get("meta") or {}).get("pagination") or {}
            page_count = meta.get("pageCount", 1)
            total = meta.get("total", len(batch))

            all_items.extend(batch)
            print(f"[etica_strapi] Página {page}/{page_count} concluída ({len(all_items)}/{total} acórdãos)")
            if page >= page_count or not batch:
                break
            page += 1
            await asyncio.sleep(0.1)

    cache_file.write_text(json.dumps(all_items, ensure_ascii=False), encoding="utf-8")
    print(f"[etica_strapi] Salvo em {cache_file} ({len(all_items)} acórdãos com PDF).")
    return all_items


def build_canonical_ementa_key(numero_ementa: str | None, sigla_orgao: str | None = None) -> str | None:
    """Cria chave canônica (ex: '001/2017/SCA-PTU') para reconciliar OAB Juris e Strapi Ética."""
    if not numero_ementa:
        return None
    s = re.sub(r"\s+", "", numero_ementa.upper())
    m = re.match(r"^0*(\d{1,4})/((?:19|20)\d{2})(?:[-/](.+))?$", s)
    if not m:
        return s
    num_int = int(m.group(1))
    year = m.group(2)
    suffix = (m.group(3) or "").replace("/", "-")
    if not suffix and sigla_orgao:
        suffix = sigla_orgao.upper()
    if suffix in {"PTU", "STU", "TTU"}:
        suffix = f"SCA-{suffix}"
    return f"{num_int:03d}/{year}/{suffix}" if suffix else f"{num_int:03d}/{year}"


def normalize_etica_strapi_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza um registro vindo do Strapi (eticaedisciplina.oab.org.br)."""
    nome = clean_text(raw.get("nome") or "")
    descricao = clean_text(raw.get("descricao") or "")
    ano_str = str(raw.get("ano") or "").strip()
    ano_int = int(ano_str) if ano_str.isdigit() else None

    camara_obj = raw.get("camara") or {}
    camara_nome = clean_text(camara_obj.get("nome") or "")

    doc_obj = raw.get("documento") or {}
    pdf_url = doc_obj.get("url")

    combined = f"{nome}\n{descricao}".strip()
    sigla_orgao, orgao_norm = detect_orgao_julgador(None, nome, combined, camara_strapi=camara_nome)

    num_ementa = normalize_numero_ementa(nome, combined)
    if num_ementa and sigla_orgao in {"SCA-PTU", "SCA-STU", "SCA-TTU"} and not num_ementa.endswith(("PTU", "STU", "TTU")):
        # Se no Strapi o nome é "Ementa 001/2024/PTU" ou "Ementa 001/2024" dentro da Primeira Turma
        if num_ementa.count("/") == 1:
            num_ementa = f"{num_ementa}/{sigla_orgao}"

    m_proc = RE_PROC_IN_TEXT.search(combined)
    numero_processo = m_proc.group(1) if m_proc else None

    dispositivos = extract_dispositivos(None, combined)
    sancao = detect_sancao(combined)
    resultado = detect_resultado(descricao, "")
    uf_origem = detect_uf_origem(numero_processo, None, combined)
    relator_principal = extract_relator_fallback(None, combined)

    created_at = (raw.get("publishedAt") or raw.get("createdAt") or "")[:10]
    data_decisao = f"{ano_int}-01-01" if ano_int else (created_at or None)

    hash_input = f"ETICA_STRAPI|{raw.get('id')}|{nome}"
    short_hash = hashlib.sha1(hash_input.encode("utf-8")).hexdigest()[:12]

    return {
        "id": f"ETICA:{short_hash}",
        "fonte": "ETICA_STRAPI",
        "contexto": "EMENTA",
        "tipo_ementa": "PROCESSO_DISCIPLINAR",
        "numero_processo": numero_processo,
        "numero_ementa": num_ementa,
        "ano_ementa": ano_int,
        "id_organizacao": 49,
        "nome_organizacao": "Conselho Federal",
        "orgao_julgador_raw": f"Conselho Federal / {camara_nome}" if camara_nome else "Conselho Federal / Ética e Disciplina",
        "orgao_julgador_norm": orgao_norm,
        "sigla_orgao": sigla_orgao,
        "uf_origem": uf_origem,
        "relator_principal": relator_principal,
        "relatores_json": "[]",
        "partes_json": "[]",
        "dispositivos_json": json.dumps(dispositivos, ensure_ascii=False),
        "dispositivos_texto": " | ".join(dispositivos),
        "sancao_identificada": sancao,
        "resultado_julgamento": resultado,
        "descricao_ementa": f"{nome}. {descricao}" if nome and not descricao.startswith(nome) else (descricao or nome),
        "descricao_acordao": "",
        "inteiro_teor_texto": None,
        "pdf_url": pdf_url,
        "diario_url": None,
        "publicacao_str": None,
        "data_decisao": data_decisao,
        "is_ted_disciplinar": 1,
        "tem_inteiro_teor": 0,
    }


def _ocr_pdf_page_tesseract(pdf_bytes: bytes, page_num: int) -> str | None:
    """Executa OCR via pdftoppm + tesseract (por) em uma página específica quando a camada de texto do PDF está corrompida."""
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_file = Path(tmp) / "doc.pdf"
            pdf_file.write_bytes(pdf_bytes)
            out_prefix = f"{tmp}/page"
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-f", str(page_num), "-l", str(page_num), str(pdf_file), out_prefix],
                check=True,
                capture_output=True,
                timeout=20,
            )
            pngs = sorted(Path(tmp).glob("page-*.png"))
            if not pngs:
                return None
            res = subprocess.run(
                ["tesseract", str(pngs[0]), "stdout", "-l", "por", "--psm", "6"],
                check=True,
                capture_output=True,
                text=True,
                timeout=25,
            )
            return res.stdout if res.stdout.strip() else None
    except Exception:
        return None


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extrai e limpa o texto de todas as páginas de um PDF em memória, com fallback de OCR quando a camada nativa está corrompida."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_txt: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        t = page.extract_text() or ""
        gib_lines = sum(1 for ln in t.splitlines() if is_gibberish_ocr_line(ln))
        if (not t.strip() or gib_lines >= 3):
            ocr_page = _ocr_pdf_page_tesseract(pdf_bytes, idx)
            if ocr_page:
                t = ocr_page
        if t.strip():
            pages_txt.append(t)
    full_txt = "\n\n".join(pages_txt)
    return clean_text(full_txt)


def enrich_record_from_inteiro_teor(conn: sqlite3.Connection, julgado_id: str, pdf_text: str) -> None:
    """Atualiza o julgado com o texto integral do PDF e enriquece metadados ausentes (processo, relator, artigos)."""
    row = conn.execute("SELECT * FROM julgados WHERE id = ?", (julgado_id,)).fetchone()
    if not row:
        return

    numero_processo = row["numero_processo"]
    if not numero_processo:
        m_proc = RE_PROC_IN_TEXT.search(pdf_text[:2000])
        if m_proc:
            numero_processo = m_proc.group(1)

    relator = row["relator_principal"]
    if not relator:
        relator = extract_relator_fallback(None, pdf_text[:2500])

    uf = row["uf_origem"]
    if not uf:
        uf = detect_uf_origem(numero_processo, None, pdf_text[:2500])

    sancao = row["sancao_identificada"] or detect_sancao(pdf_text)
    resultado = row["resultado_julgamento"] or detect_resultado(row["descricao_ementa"] or "", pdf_text[-2000:])

    existing_disp = json.loads(row["dispositivos_json"] or "[]")
    merged_disp = sorted(set(existing_disp) | set(extract_dispositivos(None, pdf_text)))

    conn.execute(
        """
        UPDATE julgados
        SET inteiro_teor_texto = ?,
            tem_inteiro_teor = 1,
            numero_processo = COALESCE(?, numero_processo),
            relator_principal = COALESCE(?, relator_principal),
            uf_origem = COALESCE(?, uf_origem),
            sancao_identificada = COALESCE(?, sancao_identificada),
            resultado_julgamento = COALESCE(?, resultado_julgamento),
            dispositivos_json = ?,
            dispositivos_texto = ?
        WHERE id = ?
        """,
        (
            pdf_text,
            numero_processo,
            relator,
            uf,
            sancao,
            resultado,
            json.dumps(merged_disp, ensure_ascii=False),
            " | ".join(merged_disp),
            julgado_id,
        ),
    )
    for d in merged_disp:
        conn.execute(
            "INSERT OR IGNORE INTO julgado_dispositivos (julgado_id, dispositivo) VALUES (?, ?)",
            (julgado_id, d),
        )


async def sync_pdfs_inteiro_teor(
    conn: sqlite3.Connection,
    limit: int | None = None,
    concurrency: int = 10,
    pdfs_dir: Path = PDFS_DIR,
) -> int:
    """Baixa os PDFs de inteiro teor pendentes, faz cache em disco e extrai o texto para o banco."""
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    sql = """
        SELECT id, pdf_url
        FROM julgados
        WHERE pdf_url IS NOT NULL AND pdf_url != ''
          AND (tem_inteiro_teor = 0 OR inteiro_teor_texto IS NULL)
        ORDER BY ano_ementa DESC, id ASC
    """
    if limit is not None and limit > 0:
        sql += f" LIMIT {int(limit)}"

    rows = conn.execute(sql).fetchall()
    if not rows:
        print("[pdfs] Nenhum PDF pendente de extração.")
        return 0

    print(f"[pdfs] Iniciando download e extração de {len(rows)} PDFs de inteiro teor (concorrência={concurrency})...")
    sem = asyncio.Semaphore(concurrency)
    extracted_results: list[tuple[str, str]] = []

    async with create_async_http_client(timeout=45.0, follow_redirects=True) as client:

        async def _worker(jid: str, url: str) -> None:
            url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            cache_pdf = pdfs_dir / f"{url_hash}.pdf"
            async with sem:
                try:
                    if cache_pdf.exists() and cache_pdf.stat().st_size > 100:
                        pdf_bytes = cache_pdf.read_bytes()
                    else:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        pdf_bytes = resp.content
                        cache_pdf.write_bytes(pdf_bytes)

                    text = await asyncio.to_thread(extract_text_from_pdf_bytes, pdf_bytes)
                    if text:
                        extracted_results.append((jid, text))
                except Exception as exc:
                    print(f"[pdfs] Aviso: falha ao processar {jid} ({url}): {exc}")

        # Processar em lotes de 100 para commitar incrementalmente no SQLite
        batch_size = 100
        total_processed = 0
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            extracted_results.clear()
            await asyncio.gather(*(_worker(r["id"], r["pdf_url"]) for r in chunk))
            with conn:
                for jid, txt in extracted_results:
                    enrich_record_from_inteiro_teor(conn, jid, txt)
            total_processed += len(extracted_results)
            print(f"[pdfs] Progresso: {total_processed}/{len(rows)} PDFs extraídos e indexados...")

    return total_processed
