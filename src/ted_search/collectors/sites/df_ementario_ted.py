"""OAB/DF - Ementário do TED (um único PDF, 2013-2014), em https://oabdf.org.br/tribunal-de-etica-e-disciplina/ (aba Ementários).

O site não declara total. O ``esperado`` é a contagem independente dos rótulos ``EMENTA:`` no texto do PDF; ``listar`` parte
cada ementa pelo padrão do documento (``EMENTA: “...”. (Processo nº N/AAAA, Relator(a) Fulano, DD.MM.AAAA, nª Turma do TED/OAB-DF)``)
e as duas contagens têm de bater. O PDF é baixado uma vez (cache em ``data/raw/site_df_ementario_ted/``) e lido com
``pdftotext -layout``. Cada ementa vira um item com ``id`` ``<arquivo>#<n>``. O texto fica como o PDF o publica (só se juntam
as quebras de linha); o cabeçalho de tema (``A     BANDONO DE CAUSA``) vai em ``tema``, fora do texto do julgado.

Só ementa + dispositivo ("Acordam os membros da Turma..."): sem relatório e sem voto, sem inteiro teor.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "DF", "ementario-ted"
PDF_URL = "https://oabdf.org.br/wp-content/uploads/2021/06/TED_Ementario_Rev04.pdf"
ARQUIVO = PDF_URL.rsplit("/", 1)[1]

RE_ROTULO = re.compile(r"\bEMENTA:")
RE_RODAPE = re.compile(
    r"\(Processo\s+n[ºo°]\s*([\d./]+),\s*Relatora?\s+(.+?),\s*(\d{2})\.(\d{2})\.(\d{4}),\s*(.+?TED/OAB-\s?DF)\)"
)
RE_ACORDAM = re.compile(r"\bAcordam\b")
RE_ORGAO = re.compile(r"(?i)\b(\d{1,2})\s*ª\s*Turma\b")

_cache: dict[str, Any] = {}


def texto_do_pdf(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, timeout=120).stdout.decode("utf-8", "replace")


async def _pdf(client: Any, sem: asyncio.Semaphore) -> str:
    if "texto" not in _cache:
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / ARQUIVO
        if not arq.exists() or arq.stat().st_size < 1000:
            arq.write_bytes(await ce.baixar_bytes(client, sem, PDF_URL, marca=b"%PDF"))
        _cache["texto"] = texto_do_pdf(arq)
    return _cache["texto"]


def contar_rotulos(texto: str) -> int:
    return len(RE_ROTULO.findall(texto))


def _tema(h: str) -> str | None:
    """'A     BANDONO DE CAUSA' -> 'ABANDONO DE CAUSA' (a letra capitular vem separada no PDF)."""
    h = re.sub(r"\s+", " ", h).strip()
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", h):
        return None
    return re.sub(r"^(\S) (?=\S)", r"\1", h)


def blocos(texto: str) -> list[dict[str, Any]]:
    """Ementas do PDF, na ordem. Cada uma: texto (da aspa inicial ao fim do rodapé), processo, relator, data ISO, órgão e tema."""
    plano = re.sub(r"\s+", " ", texto)
    partes = RE_ROTULO.split(plano)
    tema = _tema(partes[0])
    saida: list[dict[str, Any]] = []
    for n, p in enumerate(partes[1:], 1):
        m = RE_RODAPE.search(p)
        if not m:
            raise ValueError(f"ementa {n} sem o rodapé '(Processo nº ..., Relator ..., data, Turma)'")
        corpo = p[: m.end()].strip()
        relator, orgao = m.group(2).strip(), re.sub(r"\s+", " ", m.group(6)).replace("OAB- DF", "OAB-DF")
        extra = re.search(r",\s*(\d{1,2}\s*ª\s*Turma)$", relator)  # '..., Relator Fulano, 9ª Turma, 26.11.2013, do TED/OAB-DF'
        if extra:
            relator, orgao = relator[: extra.start()].strip(), f"{extra.group(1)}, {orgao}"
        saida.append({
            "n": n, "tema": tema, "texto": corpo, "processo": m.group(1), "relator": relator,
            "data": f"{m.group(5)}-{m.group(4)}-{m.group(3)}", "orgao": orgao,
        })
        sobra = _tema(p[m.end():])  # o que vem depois do rodapé é o cabeçalho de tema da ementa seguinte
        if sobra:
            tema = sobra
    return saida


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return contar_rotulos(await _pdf(client, sem))


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return [{"id": f"{ARQUIVO}#{b['n']}", **b} for b in blocos(await _pdf(client, sem))]


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return {**item, "pdf_url": PDF_URL}  # o item já é a ementa inteira, extraída do PDF único


def orgao_df(orgao: str) -> tuple[str, str]:
    """(sigla, nome normalizado) pelo que o rodapé informa: 'nª Turma do TED/OAB-DF' ou 'Pleno do TED/OAB-DF'."""
    m = RE_ORGAO.search(orgao)
    if m:
        return f"OABDF-TED-T{int(m.group(1))}", f"Tribunal de Ética e Disciplina da OAB/DF/{int(m.group(1))}ª Turma"
    if re.search(r"(?i)\bPleno\b", orgao):
        return "OABDF-TED-PLENO", "Tribunal de Ética e Disciplina da OAB/DF/Pleno"
    return "OABDF-TED", "Tribunal de Ética e Disciplina da OAB/DF"


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = (item.get("texto") or "").strip()
    if len(texto) < 40:
        return None
    m = RE_ACORDAM.search(texto)
    ementa, acordao = (texto[: m.start()].strip(), texto[m.start():].strip()) if m else (texto, "")
    sigla, norm = orgao_df(item.get("orgao") or "")
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa,
        acordao=acordao,
        url=PDF_URL,
        orgao_raw=item.get("orgao") or "TED/OAB-DF",
        sigla_orgao=sigla,
        orgao_norm=norm,
        data_decisao=item.get("data"),
        numero_processo=item.get("processo"),
        numero_ementa=item["id"].rsplit("#", 1)[-1],
        relator=item.get("relator"),
        consulta=False,
        publicacao="Site da OAB/DF - Ementário do TED (PDF)",
    )
    rec["pdf_url"] = PDF_URL
    rec["is_ted_disciplinar"] = 1
    rec["tem_inteiro_teor"] = 0  # só ementa + dispositivo
    return rec
