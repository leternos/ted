"""OAB/PB - Banco de Jurisprudências do TED (site Wix), https://www.oabpb.org.br/banco-de-jurisprudencia.

A seção "NOSSOS ARQUIVOS" lista os PDFs disponíveis; hoje há um único: "19/04/2018 - Processo - Consulta nº 150000.2017.011869-3"
(Consulta TED nº 004-2017, acórdão de 13/04/2018). ``esperado`` = itens (``<li>`` com link para PDF) da seção; ``listar`` lê as
âncoras de PDF cujo texto começa por data e " - Processo"; as duas contagens têm de bater. Id: nome do arquivo no Wix.

O PDF (1 página, texto nativo) é baixado uma vez, lido com ``pdftotext -layout`` e entra inteiro em ``inteiro_teor_texto``,
como publicado; só se juntam as linhas de cada parágrafo.
Consulta em tese: ``consulta=True``. Os demais PDFs da página (regimentos, códigos, tabela de honorários) são legislação.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
import subprocess
from pathlib import Path
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte
from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

UF, SLUG = "PB", "jurisprudencia"
URL = "https://www.oabpb.org.br/banco-de-jurisprudencia"
MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}

RE_ARQUIVOS = re.compile(r"NOSSOS ARQUIVOS(.*)", re.S)
RE_LI = re.compile(r"<li\b.*?</li>", re.S)
RE_ANCORA = re.compile(r'<a\b[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.S)
RE_ROTULO = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s*[-–]\s*Processo\b")

_cache: dict[str, str] = {}


def _txt(s: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")).strip()


def itens_da_secao(h: str) -> list[dict[str, Any]]:
    m = RE_ARQUIVOS.search(h)
    if not m:
        raise ValueError("página fora do padrão: sem 'NOSSOS ARQUIVOS'")
    saida: list[dict[str, Any]] = []
    for li in RE_LI.findall(m.group(1)):
        for href, rotulo in RE_ANCORA.findall(li):
            r = _txt(rotulo)
            d = RE_ROTULO.match(r)
            if d:
                saida.append({"id": href.rsplit("/", 1)[-1], "rotulo": r, "pdf_url": href, "publicacao": f"{d.group(3)}-{d.group(2)}-{d.group(1)}"})
    return saida


def esperado_da_pagina(h: str) -> int:
    m = RE_ARQUIVOS.search(h)
    return sum(1 for li in RE_LI.findall(m.group(1)) if RE_ANCORA.search(li)) if m else 0


def texto_do_pdf(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, timeout=120).stdout.decode("utf-8", "replace")


def paragrafos(texto: str) -> str:
    paras: list[str] = []
    atual = ""
    for ln in texto.replace("\f", "\n").split("\n"):
        s = re.sub(r"[ \t]+", " ", ln).strip()
        if not s:
            if atual:
                paras.append(atual)
                atual = ""
            continue
        atual = f"{atual} {s}" if atual else s
    if atual:
        paras.append(atual)
    return "\n\n".join(paras)


async def _pagina(client: Any, sem: asyncio.Semaphore) -> str:
    if "h" not in _cache:
        _cache["h"] = await ce.baixar_texto(client, sem, URL, marca="NOSSOS ARQUIVOS")
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "pagina.html").write_text(_cache["h"], encoding="utf-8")
    return _cache["h"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return esperado_da_pagina(await _pagina(client, sem))


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return itens_da_secao(await _pagina(client, sem))


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / item["id"]
    if not arq.exists() or arq.stat().st_size < 1000:
        arq.write_bytes(await ce.baixar_bytes(client, sem, item["pdf_url"], marca=b"%PDF"))
    texto = paragrafos(texto_do_pdf(arq))
    if len(texto) < 200:
        return None
    return {**item, "texto": texto}


def _dados(texto: str) -> dict[str, Any]:
    """Número da consulta, relator, data do acórdão e ementa (parágrafo em caixa alta que abre a decisão)."""
    d: dict[str, Any] = {}
    m = re.search(r"Consulta\s+n[ºo]\s*([\d.]+-\d)", texto)
    d["processo"] = m.group(1) if m else None
    m = re.search(r"Consulta TED\s+n[ºo]\s*(\d+)\s*[-/]\s*(\d{4})", texto)
    d["numero_ementa"] = f"{m.group(1)}/{m.group(2)}" if m else None
    m = re.search(r"Relator(?:a)?(?: Designad[oa])?:\s*([^\n.]+)", texto)
    d["relator"] = m.group(1).strip() if m else None
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})\.?\s*(?:\n|$)", texto)
    mes = MESES.get(m.group(2).lower()) if m else None
    d["data"] = f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(1)):02d}" if m and mes else None
    paras = texto.split("\n\n")
    caixa = [p for p in paras if len(p) > 200 and p == p.upper()]
    d["ementa"] = caixa[0] if caixa else ""
    m = re.search(r"\n\n(Acordam .*?)(?:\n\n|$)", texto, re.S)
    d["acordao"] = m.group(1) if m else ""
    return d


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = (item.get("texto") or "").strip()
    if len(texto) < 200:
        return None
    d = _dados(texto)
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=d["ementa"] or texto,
        acordao=d["acordao"],
        inteiro_teor=texto,
        url=item["pdf_url"],
        orgao_raw="Tribunal de Ética e Disciplina da OAB/PB",
        sigla_orgao="OABPB-TED",
        orgao_norm="Tribunal de Ética e Disciplina da OAB/PB",
        data_decisao=d["data"],
        numero_processo=d["processo"],
        numero_ementa=d["numero_ementa"],
        relator=d["relator"],
        consulta=True,
        publicacao=f"Site da OAB/PB - Banco de Jurisprudência ({item['rotulo']})",
    )
    rec["pdf_url"] = item["pdf_url"]
    rec["is_ted_disciplinar"] = 1
    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(texto) else 0
    return rec
