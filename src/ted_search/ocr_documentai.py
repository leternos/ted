"""OCR de PDFs de inteiro teor com o Document AI (processador OCR), via API REST.

Regra do dono do produto: o texto sai exatamente como o OCR lê. Nada é mascarado,
abreviado, anonimizado, corrigido ou cortado aqui. Onde a fonte tem tarja ou espaço
em branco, fica o que o OCR devolver.

O endpoint síncrono aceita até 15 páginas por chamada; por isso o PDF é dividido em
blocos e os textos por página são recolocados na ordem original.
"""

from __future__ import annotations

import base64
import io
import subprocess
import time
from typing import Any

import httpx
from pypdf import PdfReader, PdfWriter

HOST = "https://documentai.googleapis.com"
PAGINAS_POR_CHAMADA = 15


def dividir_paginas(total: int, tamanho: int = PAGINAS_POR_CHAMADA) -> list[tuple[int, int]]:
    """Intervalos (inicio, fim) base 0, fim exclusivo, cobrindo `total` páginas."""
    if total <= 0:
        return []
    return [(i, min(i + tamanho, total)) for i in range(0, total, tamanho)]


def texto_por_pagina(documento: dict[str, Any]) -> list[str]:
    """Texto de cada página de uma resposta do Document AI, recortado de `document.text`."""
    texto = documento.get("text") or ""
    saida: list[str] = []
    for pagina in documento.get("pages") or []:
        ancora = (pagina.get("layout") or {}).get("textAnchor") or {}
        partes = [
            texto[int(seg.get("startIndex") or 0) : int(seg.get("endIndex") or 0)]
            for seg in ancora.get("textSegments") or []
        ]
        saida.append("".join(partes))
    return saida


def token_gcloud() -> str:
    """Token de acesso da conta autenticada no gcloud (o passo google-github-actions/auth)."""
    r = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True)
    return r.stdout.strip()


class DocumentAI:
    def __init__(self, projeto: str, local: str = "us", token: str | None = None):
        self.projeto, self.local = projeto, local
        self.token = token or token_gcloud()
        self.cliente = httpx.Client(timeout=180, headers={"Authorization": f"Bearer {self.token}"})
        self.base = f"{HOST}/v1/projects/{projeto}/locations/{local}"

    def listar_processadores(self) -> list[dict[str, Any]]:
        r = self.cliente.get(f"{self.base}/processors")
        r.raise_for_status()
        return r.json().get("processors", [])

    def obter_ou_criar_ocr(self, nome: str = "ted-ocr") -> str:
        """Id do processador OCR `nome`; cria se não existir."""
        for p in self.listar_processadores():
            if p.get("displayName") == nome and p.get("type") == "OCR_PROCESSOR":
                return p["name"].rsplit("/", 1)[-1]
        r = self.cliente.post(f"{self.base}/processors", json={"type": "OCR_PROCESSOR", "displayName": nome})
        r.raise_for_status()
        return r.json()["name"].rsplit("/", 1)[-1]

    def _processar(self, processador: str, pdf: bytes) -> dict[str, Any]:
        corpo = {
            "rawDocument": {"content": base64.b64encode(pdf).decode(), "mimeType": "application/pdf"},
            "skipHumanReview": True,
        }
        for tentativa in range(4):
            r = self.cliente.post(f"{self.base}/processors/{processador}:process", json=corpo)
            if r.status_code in (429, 500, 503) and tentativa < 3:
                time.sleep(2**tentativa * 3)
                continue
            r.raise_for_status()
            return r.json()["document"]
        raise RuntimeError("Document AI: tentativas esgotadas")

    def ocr_pdf(self, processador: str, pdf: bytes) -> list[str]:
        """Texto de cada página do PDF, na ordem original, sem alterar nada."""
        leitor = PdfReader(io.BytesIO(pdf))
        paginas: list[str] = []
        for ini, fim in dividir_paginas(len(leitor.pages)):
            escritor = PdfWriter()
            for i in range(ini, fim):
                escritor.add_page(leitor.pages[i])
            buf = io.BytesIO()
            escritor.write(buf)
            doc = self._processar(processador, buf.getvalue())
            blocos = texto_por_pagina(doc)
            if len(blocos) != fim - ini:
                raise RuntimeError(f"Document AI devolveu {len(blocos)} páginas para um bloco de {fim - ini}")
            paginas.extend(blocos)
        return paginas
