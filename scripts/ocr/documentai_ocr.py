"""OCR com Document AI de PDFs catalogados em `inteiro_teor_pdfs`.

Uso (na raiz, com o banco desempacotado e o gcloud autenticado):
    python scripts/ocr/documentai_ocr.py --ids ETICA_PDF:546,ETICA_PDF:591 --saida /tmp/ocr_out
    python scripts/ocr/documentai_ocr.py --ids ... --processador <ID>   # usa um processador existente

Grava um JSON por PDF com o texto de cada página exatamente como o OCR devolveu.
Não grava nada no banco.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ted_search.ocr_documentai import DocumentAI  # noqa: E402

BANCO = Path("data/ted_jurisprudencia.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="ids de inteiro_teor_pdfs separados por vírgula")
    ap.add_argument("--projeto", default="dicionario-503613")
    ap.add_argument("--local", default="us")
    ap.add_argument("--processador", default="", help="id do processador OCR; vazio = obter ou criar 'ted-ocr'")
    ap.add_argument("--saida", default="ocr_out")
    a = ap.parse_args()

    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    con = sqlite3.connect(f"file:{BANCO}?mode=ro", uri=True)
    marcas = ",".join("?" * len(ids))
    linhas = con.execute(
        f"SELECT id, origem, titulo, numero_processo, paginas, pdf_url FROM inteiro_teor_pdfs WHERE id IN ({marcas})", ids
    ).fetchall()
    faltam = set(ids) - {r[0] for r in linhas}
    if faltam:
        print("ids não encontrados:", sorted(faltam), file=sys.stderr)
        return 2

    dai = DocumentAI(a.projeto, a.local)
    proc = a.processador or dai.obter_ou_criar_ocr()
    print("processador:", proc, flush=True)
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)

    for id_, origem, titulo, processo, paginas, url in linhas:
        t0 = time.time()
        pdf = httpx.get(url, timeout=180, follow_redirects=True).content
        paginas_txt = dai.ocr_pdf(proc, pdf)
        registro = {
            "id": id_, "origem": origem, "titulo": titulo, "numero_processo": processo,
            "pdf_url": url, "processador": proc, "n_paginas": len(paginas_txt),
            "paginas": paginas_txt, "texto": "\n".join(paginas_txt),
        }
        nome = re.sub(r"[^A-Za-z0-9_.-]", "_", id_) + ".json"
        (saida / nome).write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{id_}: {len(paginas_txt)} páginas, {len(registro['texto'])} caracteres, {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
