"""OAB/SE - Ementário do TED (Primeira e Segunda Turma, julgamentos de 2016), https://oabsergipe.org.br/ementario/.

Página única (WordPress + GenerateBlocks) com abas por tema; cada ementa é um ou mais parágrafos/itens de lista que terminam
no rodapé ``(TED OAB/SE, Primeira|Segunda Turma; Proc. TED nº. NNN/AAAA[, ...]; Relator(a): FULANO; Julgamento: dd/mm/aaaa;
Publicação: dd/mm/aaaa)``. Uma ementa pode cobrir vários processos (inadimplência de anuidade em lote): o primeiro vai em
``numero_processo`` e todos ficam no texto, que é guardado como o site o publica (o rodapé inclusive).

O site não declara total. O ``esperado`` é a contagem independente de ``Julgamento: dd/mm/aaaa`` no HTML; ``listar`` parte
as ementas pelos rodapés e as duas contagens têm de bater. Id estável: ``<T1|T2>-<primeiro processo>-<AAAAMMDD>``
(com sufixo ``-b``, ``-c`` se repetir). Só ementa: sem inteiro teor.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "SE", "ementario"
URL = "https://oabsergipe.org.br/ementario/"

RE_JULGAMENTO = re.compile(r"Julgamento:\s*\d{2}/\d{2}/\d{4}")
RE_ABA = re.compile(r'<div class="gb-container gb-container-\w+ gb-tabs__item(?: gb-tabs__item-open)?">')
RE_BOTAO = re.compile(r'<button class="[^"]*gb-tabs__button[^"]*">(.*?)</button>', re.S)
RE_RODAPE = re.compile(
    r"\((?:TED\s+)?OAB/SE,\s*(Primeira|Segunda)\s+Turma;\s*(.*?)\bJulgamento:\s*(\d{2})/(\d{2})/(\d{4});\s*Publica[çc][ãa]o:\s*(\d{2}/\d{2}/\d{4})\s*\)",
    re.S,
)
RE_RELATOR = re.compile(r"Relator(?:\(a\))?:\s*(.+?)\s*;?\s*$", re.S)
RE_PROC = re.compile(r"\b(\d{1,4}/\d{4})\b")
RE_TURMA_TITULO = re.compile(r"^(PRIMEIRA|SEGUNDA)\s+TURMA$")


def texto_da_aba(h: str) -> list[str]:
    """Linhas de texto de um trecho de HTML (itens de lista viram '- ...'; entidades resolvidas; espaços colapsados)."""
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h)
    h = re.sub(r"(?i)<li[^>]*>", "\n- ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|ol|ul|button)>", "\n", h)
    h = html_lib.unescape(re.sub(r"<[^>]+>", "", h)).replace("\xa0", " ")
    return [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in h.split("\n") if ln.strip()]


def abas(h: str) -> list[tuple[str, str]]:
    """[(tema, html da aba)] na ordem do site. A última aba é cortada logo depois do último rodapé."""
    a = h.find("gb-tabs__items")
    if a < 0:
        raise ValueError("página fora do padrão: sem 'gb-tabs__items'")
    temas = [re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", t))).strip() for t in RE_BOTAO.findall(h[:a])]
    partes = RE_ABA.split(h[a:])[1:]
    if partes:
        ult = list(RE_JULGAMENTO.finditer(partes[-1]))
        if ult:
            fim = partes[-1].find(")", ult[-1].end())
            partes[-1] = partes[-1][: fim + 1]
    return list(zip(temas + [""] * (len(partes) - len(temas)), partes))


def esperado_da_pagina(h: str) -> int:
    return len(RE_JULGAMENTO.findall(h))


def ementas(h: str) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    usados: dict[str, int] = {}
    for tema, trecho in abas(h):
        atual: list[str] = []
        antes = len(saida)
        for ln in texto_da_aba(trecho):
            if RE_TURMA_TITULO.match(ln) and not atual:
                continue  # título de seção ('PRIMEIRA TURMA'), não é texto de ementa
            atual.append(ln)
            texto = "\n".join(atual)
            m = RE_RODAPE.search(texto)
            if not m:
                continue
            turma = "T1" if m.group(1) == "Primeira" else "T2"
            meio = m.group(2)
            rel = RE_RELATOR.search(meio.rstrip().rstrip(";"))
            relator = rel.group(1).strip() if rel else None
            procs = RE_PROC.findall(meio[: rel.start()] if rel else meio)
            data = f"{m.group(5)}-{m.group(4)}-{m.group(3)}"
            base = f"{turma}-{(procs[0] if procs else 'sem-processo').replace('/', '-')}-{m.group(5)}{m.group(4)}{m.group(3)}"
            usados[base] = usados.get(base, 0) + 1
            sufixo = "" if usados[base] == 1 else "-" + chr(ord("a") + usados[base] - 1)
            saida.append({
                "id": base + sufixo, "turma": m.group(1), "tema": tema, "texto": texto, "processos": procs, "relator": relator,
                "data": data, "publicacao": m.group(6),
            })
            atual = []
        if atual and len(saida) > antes:
            # parágrafo solto depois do rodapé ('A falta de procuração pode ser suprida...'): é a tese da ementa anterior
            saida[-1]["texto"] += "\n" + "\n".join(atual)
        elif atual:
            raise ValueError(f"aba '{tema}': texto sem rodapé de julgamento: {atual[0][:80]!r}")
    return saida


_cache: dict[str, str] = {}


async def _pagina(client: Any, sem: asyncio.Semaphore) -> str:
    if "h" not in _cache:
        _cache["h"] = await ce.baixar_texto(client, sem, URL, marca="gb-tabs__items")
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "pagina.html").write_text(_cache["h"], encoding="utf-8")  # cache bruto da página única
    return _cache["h"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return esperado_da_pagina(await _pagina(client, sem))


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return ementas(await _pagina(client, sem))


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return dict(item)  # a ementa inteira já está na página única


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = (item.get("texto") or "").strip()
    if len(texto) < 40:
        return None
    n = 1 if item.get("turma") == "Primeira" else 2
    procs = item.get("processos") or []
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=texto,
        url=URL,
        orgao_raw=f"TED OAB/SE, {item.get('turma')} Turma",
        sigla_orgao=f"OABSE-TED-T{n}",
        orgao_norm=f"Tribunal de Ética e Disciplina da OAB/SE/{n}ª Turma Disciplinar",
        data_decisao=item.get("data"),
        numero_processo=procs[0] if procs else None,
        relator=item.get("relator"),
        consulta=False,
        publicacao=f"Site da OAB/SE - Ementário do TED (publicação {item['publicacao']})" if item.get("publicacao") else None,
    )
    rec["is_ted_disciplinar"] = 1
    rec["tem_inteiro_teor"] = 0  # só ementa
    return rec
