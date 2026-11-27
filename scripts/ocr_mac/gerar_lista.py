"""Gera data/inteiro_teor/ocr_lista.tsv a partir do catálogo `inteiro_teor_pdfs` do banco.

Uso (com o banco desempacotado: python -m ted_search.banco desempacotar):
    python scripts/ocr_mac/gerar_lista.py

Colunas: id, origem, numero_processo, paginas, motor_sugerido, pdf_url, titulo
- motor_sugerido = ocr   para o Portal de Ética (PDF escaneado; a camada de texto do ABBYY é fraca)
- motor_sugerido = texto para o portal de jurisprudência (PDF digital com texto real; OCR só pioraria)
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
BANCO = RAIZ / "data" / "ted_jurisprudencia.db"
SAIDA = RAIZ / "data" / "inteiro_teor" / "ocr_lista.tsv"


def motor_sugerido(origem: str) -> str:
    return "texto" if origem == "PORTAL_JURISPRUDENCIA_CFOAB" else "ocr"


def main() -> None:
    con = sqlite3.connect(f"file:{BANCO}?mode=ro", uri=True)
    linhas = con.execute(
        "SELECT id, origem, numero_processo, paginas, pdf_url, titulo FROM inteiro_teor_pdfs ORDER BY origem, ano, id"
    ).fetchall()
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    with open(SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["id", "origem", "numero_processo", "paginas", "motor_sugerido", "pdf_url", "titulo"])
        for id_, origem, proc, pag, url, titulo in linhas:
            w.writerow([id_, origem, proc or "", pag or "", motor_sugerido(origem), url, (titulo or "").replace("\t", " ")])
    n_ocr = sum(1 for r in linhas if motor_sugerido(r[1]) == "ocr")
    pag_ocr = sum((r[3] or 0) for r in linhas if motor_sugerido(r[1]) == "ocr")
    print(f"{len(linhas)} PDFs em {SAIDA}  (OCR: {n_ocr} PDFs, {pag_ocr} páginas; texto direto: {len(linhas) - n_ocr})")


if __name__ == "__main__":
    main()
