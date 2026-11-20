"""OAB/GO - ementários do Conselho Seccional (recursos e procedimentos do Conselho Pleno).

Mesmo site WordPress do TED (CPT ``ementarios-conselho-seccional``). Lista: ``/ementarios-conselho-seccional/page/N/``
(12 por página; "Página 1 de N"). Detalhe: ``/ementarios-conselho-seccional/processo-no-<nº>/`` com h4 "Processo nº ...",
a data da sessão e o corpo (partes por iniciais, EMENTA e ACÓRDÃO assinado pelo Presidente e pelo Relator) em
``div.post-body``. Inclui matéria não disciplinar (inscrição, exame de ordem, eleitoral...): a marca
``is_ted_disciplinar`` vem de ``eh_materia_disciplinar`` aplicada ao texto. Sem inteiro teor e sem PDF.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ted_search.collectors.deoab_nacional import RE_ADMINISTRATIVO, _sem_acento_upper, eh_eleitoral, eh_materia_disciplinar
from ted_search.collectors.sites import go_ementarios_ted as ted
from ted_search.fontes import montar_registro

UF, SLUG = "GO", "ementarios-conselho"
SLUG_SITE = "ementarios-conselho-seccional"
BASE = f"{ted.SITE}/{SLUG_SITE}"
SITEMAP = f"{ted.SITE}/wp-sitemap-posts-ementarios-conselho-1.xml"


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    i = await ted._primeira_e_ultima(client, sem, BASE, SLUG_SITE)
    return (i["paginas"] - 1) * i["por_pagina"] + i["na_ultima"]


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    i = await ted._primeira_e_ultima(client, sem, BASE, SLUG_SITE)
    paginas: dict[int, str] = {1: i["pagina1"], i["paginas"]: i["ultima"]}

    async def _pg(n: int) -> None:
        paginas[n] = await ted._get(client, sem, ted.url_pagina(BASE, n), marca="lista-ementarios")

    await asyncio.gather(*(_pg(n) for n in range(2, i["paginas"])))
    x = await ted._get(client, sem, SITEMAP, marca="</urlset>")
    no_sitemap = set(re.findall(r"<loc>[^<]*?/" + SLUG_SITE + r"/([^/<]+)/</loc>", x))
    itens: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for n in range(1, i["paginas"] + 1):
        for slug in ted.itens_da_pagina(paginas[n], SLUG_SITE):
            if slug not in vistos:
                vistos.add(slug)
                itens.append({"id": slug, "pagina": n, "no_sitemap": slug in no_sitemap})
    return itens


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    slug = str(item["id"])
    h = await ted._get(client, sem, f"{BASE}/{slug}/", marca="<!-- post-body -->")
    par = ted.paragrafos(h)
    return {"id": slug, "url": f"{BASE}/{slug}/", "pagina": item.get("pagina"), "no_sitemap": item.get("no_sitemap"),
            "paragrafos": par, "texto": "\n".join(par)}


# ---------------------------------------------------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------------------------------------------------

RE_DATA_BR = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
RE_RELATOR_CAB = re.compile(r"(?i)\bRelator[a]?\s*:\s*(?:Conselheir[oa]\s+)?(.{3,100}?)(?=\s+EMENTA\b|\s+Interessad|\s+Assunto|\.\s|$)")
PARTICULAS = {"de", "da", "do", "dos", "das", "e"}
PARAR = {"presidente", "câmara", "camara", "exercício", "exercicio", "seccional", "pleno", "ass", "oab", "oab-go", "cons", "conselheiro", "conselheira",
         "redator", "redatora", "relator", "relatora", "goiás", "goias", "goiânia", "goiania", "ordem"}


def relator_da_assinatura(texto: str) -> str | None:
    """Nome que antecede 'Relator/Relatora' na assinatura final ('X Presidente da 1ª Câmara e Fulano de Tal – Relator (07/11/2018)').

    Retrocede pelas palavras capitalizadas (e partículas 'de/da/e') até um marcador que não é nome (Presidente, Câmara, Ass:, número).
    'Redator' não conta: é quem redige o voto vencedor, não o relator.
    """
    cauda = texto[-400:]
    achados = list(re.finditer(r"(?i)\b(?:Cons\.\s*)?Relator[a]?\b(?!\s*:)", cauda))
    if not achados:
        return None
    pre = cauda[:achados[-1].start()]
    toks = re.findall(r"[^\s,;–—-]+", re.sub(r"[–—]", " ", pre))
    nome: list[str] = []
    for t in reversed(toks):
        base = t.strip(".:()").lower()
        if base in PARAR or re.search(r"\d", t) or (t.endswith(".") and len(base) > 3):
            break  # fim da frase anterior ('...Ambos Os Recursos. Neliana Fraga de Souza – Relatora')
        if base in PARTICULAS or t[:1].isupper():
            nome.append(t.strip("."))
        else:
            break
    nome.reverse()
    while nome and nome[0].lower() in PARTICULAS:
        nome.pop(0)
    nome = [n for n in nome if n]
    return " ".join(nome).upper() if len(nome) >= 2 else None


def extrair_relator(texto: str, cabecalho: str = "") -> str | None:
    """'Relator: Conselheira X' só vale no cabeçalho (antes da EMENTA); senão, a assinatura final."""
    m = RE_RELATOR_CAB.search(cabecalho or texto[:250])
    if m:
        nome = re.split(r"\s[–—-]\s|\s*\(", m.group(1))[0]
        nome = ted.RE_TITULO.sub("", nome.strip(" .;:,-")).strip(" .;:,-")
        if len(nome.split()) >= 2:
            return nome.upper()
    return relator_da_assinatura(texto)


def extrair_orgao(texto: str) -> tuple[str, str, str | None]:
    """(sigla, nome normalizado, nome como o texto o dá): Câmara pela fórmula 'acordam ... da <n>ª Câmara' ou pela assinatura; senão o Pleno."""
    tipo, n, nome = ted.extrair_orgao(texto)
    if tipo != "camara" or not n:
        m = re.search(r"(?i)Presidente\s+d[aoe]\s+(\d{1,2})\s*[ªº°]?\s*C[âa]mara", texto)
        if m:
            tipo, n, nome = "camara", int(m.group(1)), f"{m.group(1)}ª Câmara"
    if tipo == "camara" and n:
        return f"OABGO-{n}CR", f"Conselho Seccional - GO/{n}ª Câmara Recursal/Disciplinar", nome
    return "OABGO-PLENO", "Conselho Seccional - GO/Conselho Pleno", "Conselho Seccional"


RE_DISCIPLINAR_EXTRA = re.compile(
    r"REPRESENTA[CÇ][AÃ]O|REPRESENTAD[OA]|PENA\s+DE\s+(?:SUSPENS|EXCLUS|CENSUR|ADVERT)|SANC[AÃ]O\s+DISCIPL|"
    r"PRESCRI[CÇ][AÃ]O\s+DA\s+PRETENS|(?:PEDIDO\s+DE\s+)?REVIS[AÃ]O\s+(?:DE\s+(?:PROCESSO|PENA)|JULGAD)|[EÉ]TICO"
)


def eh_disciplinar(titulo: str, cabecalho: str, ementa: str, acordao: str) -> bool:
    """Marca do banco: ético-disciplinar (1) ou administrativa (0). Usa a regra do DEOAB e a completa com os termos
    próprios do ementário do Conselho (representação, pena de suspensão/exclusão, revisão) quando o assunto não é administrativo."""
    texto = f"{cabecalho} {ementa} {acordao}".strip()
    if eh_materia_disciplinar("", titulo, texto):
        return True
    if eh_eleitoral("", texto) or RE_ADMINISTRATIVO.search(_sem_acento_upper(f"{titulo} {texto[:600]}")):
        return False
    return bool(RE_DISCIPLINAR_EXTRA.search(_sem_acento_upper(f"{cabecalho} {ementa}").replace("Ç", "C")) or RE_DISCIPLINAR_EXTRA.search(f"{cabecalho} {ementa}".upper()))


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    par = [p for p in (ted._limpar(x) for x in item.get("paragrafos") or []) if p]
    if not par:
        return None
    m_d = RE_DATA_BR.match(par[1]) if len(par) > 2 else None  # título do site (h4, com erros de digitação), data da sessão, corpo
    titulo = par[0] if m_d or (len(par) > 1 and re.match(r"(?i)pro\w*\s", par[0])) else ""
    corpo = " ".join(par[2:] if m_d else par[1:] if titulo else par)
    corpo = re.sub(r"\s+", " ", corpo).strip()
    if len(corpo) < 40:
        return None  # sem texto de julgado
    data = ted._iso(m_d.group(1), m_d.group(2), m_d.group(3)) if m_d else None
    m_a = ted.RE_ACORDAO.search(corpo) or re.search(r"(?i)\bAC[ÓO]RD[ÃA]O\b\s*[:.]?\s+", corpo)
    ementa, acordao = (corpo[:m_a.start()], corpo[m_a.end():]) if m_a else (corpo, "")
    m_e = ted.RE_EMENTA.search(ementa)
    cabecalho = ementa[:m_e.start()].strip() if m_e else ""
    ementa = ementa[m_e.end():].strip() if m_e else ementa.strip()
    m_p = re.search(r"(\d{4}\s*/+\s*\d{1,6}|\d[\d./-]*\d)", titulo) or re.search(r"(?i)processo\s*n\s*[ºo°.]*\s*:?\s*(\d[\d./-]*\d)", corpo)
    sigla, norm, nome = extrair_orgao(corpo)
    disciplinar = eh_disciplinar(titulo, cabecalho, ementa, acordao)
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa or corpo,
        acordao=acordao,
        url=item["url"],
        orgao_raw=nome or "Conselho Seccional",
        sigla_orgao=sigla,
        orgao_norm=norm,
        data_decisao=data,
        numero_processo=m_p.group(1).strip(".,;/-") if m_p else None,
        relator=extrair_relator(corpo, cabecalho),
        consulta=False,
    )
    rec["is_ted_disciplinar"] = 1 if disciplinar else 0
    rec["tem_inteiro_teor"] = 0  # só ementa + acórdão curto, sem relatório e voto
    return rec
