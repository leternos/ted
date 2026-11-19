"""Segunda passada: PDFs do portal sem camada de texto (só imagem) são lidos por OCR nas primeiras páginas.

Uso: python scripts/etica_pdfs_ocr.py   (regrava as linhas correspondentes de data/inteiro_teor/etica_pdfs.jsonl)
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from etica_pdfs_scan import RE_PROC, SAIDA, _dig, sem_acento  # noqa: E402

PAGINAS_OCR = 12


def ocr(pdf: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        arq = Path(tmp) / "d.pdf"
        arq.write_bytes(pdf)
        subprocess.run(["pdftoppm", "-png", "-r", "170", "-f", "1", "-l", str(PAGINAS_OCR), str(arq), f"{tmp}/p"], check=True, capture_output=True, timeout=180)
        partes = []
        for png in sorted(Path(tmp).glob("p-*.png")):
            r = subprocess.run(["tesseract", str(png), "stdout", "-l", "por", "--psm", "6"], capture_output=True, text=True, timeout=180)
            partes.append(r.stdout)
        return "\n".join(partes)


linhas = [json.loads(l) for l in SAIDA.read_text().splitlines() if l.strip()]
alvo = [l for l in linhas if ((l.get("chars") or 0) < 500 or not (l.get("tem_relatorio") and l.get("tem_voto"))) and not l.get("ocr")]
print(len(alvo), "PDFs a conferir por OCR", flush=True)
for l in alvo:
    try:
        texto = ocr(httpx.get(l["pdf_url"], timeout=120, follow_redirects=True).content)
    except Exception as e:  # noqa: BLE001
        print("  falhou", l["nome"], type(e).__name__, flush=True)
        continue
    plano = sem_acento(texto)
    procs = []
    for g in RE_PROC.finditer(texto[:8000]):
        p = "{}.{}.{}.{}-{}".format(*(_dig(x) for x in g.groups()))
        if p not in procs:
            procs.append(p)
    l.update({
        "ocr": True, "ocr_paginas": PAGINAS_OCR, "chars_ocr": len(texto), "processos": procs[:5],
        "tem_relatorio": bool(re.search(r"RELAT[O0]RI[O0]", plano)), "tem_voto": bool(re.search(r"\bV\s?[O0]\s?T\s?[O0]\b", plano)),
        "tem_ementa": "EMENTA" in plano, "tem_acordao": bool(re.search(r"AC[O0]RD[AÃ][O0M]", plano)),
        "cabecalho": " ".join(texto[:700].split())[:400],
    })
    print("  ", l["nome"], l["paginas"], "p.", procs[:1], l["tem_relatorio"], l["tem_voto"], flush=True)
SAIDA.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in linhas))
print("FIM")
