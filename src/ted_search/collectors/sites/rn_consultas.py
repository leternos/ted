"""OAB/RN - Consultas Ético-Disciplinares respondidas pelo TED (Pleno), 9 PDFs ligados na página do Tribunal de Ética e Disciplina.

https://www.oabrn.org.br/pagina/tribunal-de-etica-disciplina lista, na seção 'Consulta Ético-Disciplinar', um link por consulta
(``../../2017/arquivos/Consultas/CONSULTA-<nº><AAAA>-<TITULO>.pdf``; o diretório não é listável, só se chega pelos links da página).
Cada PDF traz o cabeçalho (nº da consulta, consulente com OAB, relator), o ACÓRDÃO (ementa), o RELATÓRIO e o VOTO.

O site não declara total. O ``esperado`` é a contagem independente dos números de consulta distintos nos links
``/Consultas/CONSULTA-<nº>-`` da página; ``listar`` monta os itens a partir dos ``<a>`` da lista e as duas contagens têm de bater.

``baixar`` guarda o PDF em ``data/raw/site_rn_consultas/pdf/`` e lê o texto como o BA (``ler_pdf``): camada de texto página a
página (``pdftotext -layout``) e, onde a página é só imagem, OCR (``tesseract -l por``). O texto integral do PDF vai em
``inteiro_teor_texto`` sem alteração (consulente e relator nomeados como o tribunal publica).

Consulta em tese respondida pelo TED: marca disciplinar 1 (órgão = TED, Pleno).
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Any
from urllib.parse import unquote, urljoin

from ted_search.collectors.sites import ba_consultivo as ba
from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "RN", "consultas"
URL = "https://www.oabrn.org.br/pagina/tribunal-de-etica-disciplina"
SIGLA = "OABRN-TED"
ORGAO_GENERICO = "Tribunal de Ética e Disciplina da OAB/RN"
MOTIVOS: dict[str, str] = {}

RE_LINK = re.compile(r'(?is)<a\s+[^>]*href="([^"]*?/Consultas/CONSULTA-(\d+)-[^"]*\.pdf)"[^>]*>(.*?)</a>')
RE_CABECA = re.compile(r"(?is)Consulta\s+n[ºo°]\s*(\d+)-(\d)\.?\s*Consulente:\s*(.+?)\s*Relator(?:a)?\s*:\s*(.+?)\s*\.?\s*(?:\n\s*\n|ACÓRDÃO|$)")
MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
         "novembro": 11, "dezembro": 12}

_cache: dict[str, str] = {}


async def _pagina(client: Any, sem: asyncio.Semaphore) -> str:
    if "h" not in _cache:
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        _cache["h"] = await ce.baixar_texto(client, sem, URL, marca="Consultas/")
        (pasta / "pagina.html").write_text(_cache["h"], encoding="utf-8")  # cache bruto
    return _cache["h"]


def numeros_citados(h: str) -> set[str]:
    return set(re.findall(r"/Consultas/CONSULTA-(\d+)-", h))


def itens_da_pagina(h: str) -> list[dict[str, Any]]:
    por: dict[str, dict[str, Any]] = {}
    for href, num, rotulo in RE_LINK.findall(h):
        titulo = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", rotulo))).strip()
        por.setdefault(num, {"id": num, "href": href, "pdf_url": urljoin(URL, href), "titulo_link": titulo,
                             "numero": f"{num[:-4]}/{num[-4:]}", "ano": int(num[-4:])})
    return sorted(por.values(), key=lambda i: (i["ano"], i["id"]))


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return len(numeros_citados(await _pagina(client, sem)))


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return itens_da_pagina(await _pagina(client, sem))


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    pasta = raw_dir_fonte(UF, SLUG, RAW_DIR) / "pdf"
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / unquote(item["pdf_url"].rsplit("/", 1)[1])
    if not arq.exists() or arq.stat().st_size < 1000:
        arq.write_bytes(await ce.baixar_bytes(client, sem, item["pdf_url"], marca=b"%PDF"))
    out = dict(item)
    out.update(pdf_arquivo=arq.name, pdf_bytes=arq.stat().st_size, **await asyncio.to_thread(ba.ler_pdf, arq))
    return out


def cabecalho(t: str) -> dict[str, str] | None:
    m = RE_CABECA.search(t[:1500])
    if not m:
        return None
    flat = lambda s: re.sub(r"\s+", " ", s).strip(" .")  # noqa: E731
    return {"dv": m.group(2), "consulente": flat(m.group(3)), "relator": flat(m.group(4))}


def relator_nome(rel: str) -> str:
    """'Juiz Pedro Avelino Neto' e 'Juiz Revisor José Héldison...' -> o nome (o tratamento sai do campo; o texto do PDF fica intacto)."""
    return re.sub(r"^(?:(?:Juiz[a]?|Revisor[a]?|Dr[a]?\.?|Desembargador[a]?)\s+)+", "", rel.strip(), flags=re.I)


def ementa_do_texto(t: str) -> str:
    m = re.search(r"ACÓRDÃO\s*:?(.*?)(?=\n\s*RELATÓRIO\s*:?\s*\n)", t, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def data_do_texto(t: str) -> str | None:
    """Data do acórdão: a primeira 'Natal, dd de mês de aaaa' (o ACÓRDÃO assinado). Sem ela, nada se inventa."""
    for m in re.finditer(r"(?i)\bNatal\s*,\s*(\d{1,2})\s*(?:º|°)?\s*de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})", t):
        mes = MESES.get(m.group(2).lower())
        if mes and 2015 <= int(m.group(3)) <= 2030:
            return f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(1)):02d}"
    return None


def orgao_do_texto(t: str) -> tuple[str, str, str]:
    if re.search(r"(?i)\bpleno\b", t):
        return "Pleno do Tribunal de Ética e Disciplina da OAB/RN", f"{SIGLA}-PLENO", f"{ORGAO_GENERICO}/Pleno"
    return "Tribunal de Ética e Disciplina da OAB/RN", SIGLA, ORGAO_GENERICO


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = (item.get("texto_pdf") or "").strip()
    if len(texto) < 200:
        MOTIVOS[str(item["id"])] = "PDF sem texto legível (nem camada de texto nem OCR)"
        return None
    cab = cabecalho(texto)
    orgao, sigla, norm = orgao_do_texto(texto)
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa_do_texto(texto) or texto[:2000],
        url=URL,
        orgao_raw=orgao,
        sigla_orgao=sigla,
        orgao_norm=norm,
        data_decisao=data_do_texto(texto),
        inteiro_teor=texto,
        numero_processo=item.get("numero"),
        numero_ementa=f"{item['id']}-{cab['dv']}" if cab else item["id"],
        relator=relator_nome(cab["relator"]) if cab else None,
        consulta=True,
        publicacao="Site da OAB/RN - Consultas do TED (PDF)",
    )
    if not cab:
        rec["relator_principal"] = None
    rec["pdf_url"] = item.get("pdf_url")
    rec["is_ted_disciplinar"] = 1  # consulta respondida pelo TED
    from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(rec["inteiro_teor_texto"]) else 0
    return rec
