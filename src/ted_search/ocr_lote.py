"""OCR em lote de PDFs de inteiro teor, com motores intercambiáveis.

Motores:
- vision     Vision da Apple (só macOS; precisa de pyobjc-framework-Vision e pyobjc-framework-Quartz)
- tesseract  Tesseract (`tesseract` e `pdftoppm` no PATH)
- texto      camada de texto do próprio PDF (pypdf); para PDFs digitais, sem OCR

Regra do dono do produto: o texto sai exatamente como o motor lê. Nada é mascarado,
abreviado, anonimizado, corrigido ou cortado aqui. Onde a fonte tem tarja ou espaço
em branco, fica o que o motor devolver.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PAGINAS_DPI = 300


def agrupar_linhas(obs: list[dict[str, Any]]) -> str:
    """Junta fragmentos de texto (coordenadas do Vision: origem embaixo à esquerda) em linhas,
    de cima para baixo e, dentro da linha, da esquerda para a direita. Não altera nenhum texto."""
    ordenadas = sorted(obs, key=lambda o: -(o["y"] + o["h"] / 2))
    linhas: list[dict[str, Any]] = []
    for o in ordenadas:
        cy = o["y"] + o["h"] / 2
        if linhas and abs(cy - linhas[-1]["cy"]) <= 0.5 * max(o["h"], linhas[-1]["h"]):
            linhas[-1]["itens"].append(o)
            linhas[-1]["h"] = max(linhas[-1]["h"], o["h"])
        else:
            linhas.append({"cy": cy, "h": o["h"], "itens": [o]})
    return "\n".join(" ".join(i["texto"] for i in sorted(l["itens"], key=lambda i: i["x"])) for l in linhas)


# ---------------------------------------------------------------- motor: texto
def paginas_texto(pdf: bytes) -> list[dict[str, Any]]:
    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(pdf))
    return [{"n": i + 1, "texto": p.extract_text() or "", "confianca": None} for i, p in enumerate(leitor.pages)]


# ------------------------------------------------------------ motor: tesseract
def paginas_tesseract(pdf: bytes, idioma: str = "por+eng") -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        arq = Path(tmp) / "d.pdf"
        arq.write_bytes(pdf)
        subprocess.run(["pdftoppm", "-r", str(PAGINAS_DPI), "-gray", "-png", str(arq), f"{tmp}/p"], check=True, capture_output=True, timeout=600)
        saida = []
        for i, png in enumerate(sorted(Path(tmp).glob("p-*.png")), start=1):
            r = subprocess.run(
                ["tesseract", str(png), "stdout", "-l", idioma, "--psm", "1"],
                capture_output=True, text=True, timeout=300, env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
            saida.append({"n": i, "texto": r.stdout, "confianca": None})
        return saida


# --------------------------------------------------------------- motor: vision
def paginas_vision(pdf: bytes, correcao: bool = True) -> list[dict[str, Any]]:
    """OCR página a página com o framework Vision da Apple. Só roda no macOS."""
    import Quartz  # type: ignore[import-not-found]
    import Vision  # type: ignore[import-not-found]
    from Foundation import NSData  # type: ignore[import-not-found]

    dados = NSData.dataWithBytes_length_(pdf, len(pdf))
    doc = Quartz.PDFDocument.alloc().initWithData_(dados)
    if doc is None:
        raise RuntimeError("PDFKit não conseguiu abrir o PDF")
    saida = []
    escala = PAGINAS_DPI / 72.0
    for i in range(doc.pageCount()):
        pagina = doc.pageAtIndex_(i)
        caixa = pagina.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
        tamanho = (caixa.size.width * escala, caixa.size.height * escala)
        imagem = pagina.thumbnailOfSize_forBox_(tamanho, Quartz.kPDFDisplayBoxMediaBox)
        cg = imagem.CGImageForProposedRect_context_hints_(None, None, None)[0]
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(correcao)
        try:
            req.setRecognitionLanguages_(["pt-BR", "en-US"])
        except Exception:  # noqa: BLE001  (sistemas antigos sem pt-BR)
            req.setRecognitionLanguages_(["en-US"])
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
        ok, erro = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"Vision falhou na página {i + 1}: {erro}")
        obs, confs = [], []
        for r in req.results() or []:
            cand = r.topCandidates_(1)
            if not cand:
                continue
            box = r.boundingBox()
            obs.append({"texto": str(cand[0].string()), "x": box.origin.x, "y": box.origin.y, "w": box.size.width, "h": box.size.height})
            confs.append(float(cand[0].confidence()))
        saida.append({"n": i + 1, "texto": agrupar_linhas(obs), "confianca": round(sum(confs) / len(confs), 4) if confs else None})
    return saida


MOTORES = {"vision": paginas_vision, "tesseract": paginas_tesseract, "texto": paginas_texto}
