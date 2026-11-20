"""OAB/GO - ementários do Tribunal de Ética e Disciplina (Turmas/Câmaras do TED), 2003 a 2025.

Site WordPress com o CPT ``ementarios-ted``. Lista: ``GET /ementarios-ted/page/N/`` (12 itens por página, a última com
menos; a página 1 é a raiz, pois ``/page/1/`` redireciona). O site declara "Página 1 de N" mas não o total de itens: o
``esperado`` é (N - 1) x itens da primeira página + itens da última. Detalhe: ``/ementarios-ted/<slug>/`` com o texto em
``div.post-body``. Quase todos os slugs são numéricos (IDs com buracos) e 3 não: por isso ``listar`` usa a paginação e
nunca varre IDs. O sitemap ``wp-sitemap-posts-ementarios-ted-{1..5}.xml`` lista menos 3 URLs que a paginação; ``listar``
marca cada item com ``no_sitemap`` para a divergência ficar registrada nos dados.

Só há ementa + dispositivo, em texto livre cujo formato muda por época; processo, turma/câmara, relator e data vêm do
texto (``extrair``). Sem inteiro teor e sem PDF. ``baixar`` guarda o texto cru; a leitura dos campos é feita em
``normalizar`` (puro), para poder ser refeita sem nova rede.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Any

from ted_search.collectors.deoab_nacional import eh_materia_disciplinar
from ted_search.fontes import montar_registro

UF, SLUG = "GO", "ementarios-ted"
SITE = "https://www.oabgo.org.br"
BASE = f"{SITE}/ementarios-ted"
SITEMAPS = [f"{SITE}/wp-sitemap-posts-ementarios-ted-{n}.xml" for n in range(1, 6)]
PAUSA = 0.2  # segundos após cada requisição, em cada uma das (no máximo 4) conexões
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

RE_PAGINAS = re.compile(r"P[áa]gina\s+\d+\s+de\s+(\d+)")
RE_PAGINA_N = re.compile(r"/page/(\d+)/")
RE_ITEM = re.compile(r'<a class="item" href="[^"]*?/ementarios-ted/([^/"]+)/"')
RE_POST_BODY = re.compile(r'(?s)<div class="post-body">(.*?)</div><!-- post-body -->')

_info: dict[str, Any] = {}


# ---------------------------------------------------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------------------------------------------------

async def _get(client: Any, sem: asyncio.Semaphore, url: str, params: dict[str, Any] | None = None, marca: str = "</html>") -> str:
    """GET com repetição; só devolve página que traz ``marca`` (resposta cortada ou de erro é refeita, nunca aceita)."""
    erro: Exception | None = None
    for n in range(5):
        try:
            async with sem:
                resp = await client.get(url, params=params, headers=HEADERS)
                await asyncio.sleep(PAUSA)
            resp.raise_for_status()
            h = resp.content.decode("utf-8", "replace")
            if marca in h:
                return h
            erro = ValueError("resposta fora do padrão do site")
        except Exception as exc:
            erro = exc
        await asyncio.sleep(2.0 * (n + 1))
    raise RuntimeError(f"falha em {url}: {erro}")


def url_pagina(base: str, n: int) -> str:
    return f"{base}/" if n == 1 else f"{base}/page/{n}/"


def itens_da_pagina(h: str, base_slug: str = "ementarios-ted") -> list[str]:
    """Slugs dos itens de uma página de listagem, na ordem em que aparecem."""
    return re.findall(r'<a class="item" href="[^"]*?/' + re.escape(base_slug) + r'/([^/"]+)/"', h)


async def _primeira_e_ultima(client: Any, sem: asyncio.Semaphore, base: str, slug: str) -> dict[str, Any]:
    if base in _info:
        return _info[base]
    p1 = await _get(client, sem, url_pagina(base, 1), marca="lista-ementarios")
    m = RE_PAGINAS.search(p1)
    if not m:
        raise RuntimeError(f"{base}: o site não declarou o número de páginas")
    n = int(m.group(1))
    ultima = p1 if n == 1 else await _get(client, sem, url_pagina(base, n), marca="lista-ementarios")
    por_pagina = len(itens_da_pagina(p1, slug))
    _info[base] = {"paginas": n, "por_pagina": por_pagina, "na_ultima": len(itens_da_pagina(ultima, slug)),
                   "pagina1": p1, "ultima": ultima}
    return _info[base]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    i = await _primeira_e_ultima(client, sem, BASE, "ementarios-ted")
    return (i["paginas"] - 1) * i["por_pagina"] + i["na_ultima"]


async def _sitemap(client: Any, sem: asyncio.Semaphore) -> set[str]:
    slugs: set[str] = set()
    for u in SITEMAPS:
        x = await _get(client, sem, u, marca="</urlset>")
        slugs.update(re.findall(r"<loc>[^<]*?/ementarios-ted/([^/<]+)/</loc>", x))
    return slugs


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    i = await _primeira_e_ultima(client, sem, BASE, "ementarios-ted")
    paginas: dict[int, str] = {1: i["pagina1"], i["paginas"]: i["ultima"]}

    async def _pg(n: int) -> None:
        paginas[n] = await _get(client, sem, url_pagina(BASE, n), marca="lista-ementarios")

    await asyncio.gather(*(_pg(n) for n in range(2, i["paginas"])))
    no_sitemap = await _sitemap(client, sem)
    itens: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for n in range(1, i["paginas"] + 1):
        for slug in itens_da_pagina(paginas[n]):
            if slug in vistos:
                continue
            vistos.add(slug)
            itens.append({"id": slug, "pagina": n, "no_sitemap": slug in no_sitemap})
    return itens


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    slug = str(item["id"])
    h = await _get(client, sem, f"{BASE}/{slug}/", marca="<!-- post-body -->")
    return {"id": slug, "url": f"{BASE}/{slug}/", "pagina": item.get("pagina"), "no_sitemap": item.get("no_sitemap"),
            "paragrafos": paragrafos(h), "texto": "\n".join(paragrafos(h))}


# ---------------------------------------------------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------------------------------------------------

def _c1(s: str) -> str:
    """O site tem aspas e travessões do Word gravados como controles C1 (aspas e travessões): lê-os como cp1252."""
    return re.sub(r"[\x80-\x9f]", lambda m: bytes([ord(m.group())]).decode("cp1252", "replace"), s)


def _limpar(s: str) -> str:
    return re.sub(r"[ \t\r\f\v\u00a0\u200b]+", " ", _c1(s)).strip()


def paragrafos(h: str) -> list[str]:
    """Parágrafos de texto do ``div.post-body`` (sem scripts/estilos; <br> e fim de bloco quebram linha)."""
    m = RE_POST_BODY.search(h)
    corpo = m.group(1) if m else ""
    corpo = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", corpo)
    corpo = re.sub(r"(?i)<br\s*/?>|</(?:p|div|h[1-6]|li|article)>", "\n", corpo)
    corpo = html_lib.unescape(re.sub(r"<[^>]+>", "", corpo))
    return [ln for ln in (_limpar(x) for x in corpo.split("\n")) if ln]


MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
ANO_MIN, ANO_MAX = 1990, 2026  # fora disso, a data lida é erro de digitação do texto: fica nula
_DATA_NUM = r"(\d{1,2})\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{4}|\d{2})(?!\d)"  # ano de 3 dígitos ('10/11/216') é erro do site: não casa
_DATA_EXT = r"(\d{1,2})\s*(?:º|°|o)?\s*(?:de\s+)?(" + "|".join(MESES) + r")\s*(?:de\s+|,\s*)?(\d{4})"
RE_DATA_NUM = re.compile(r"(?<![\d/,-])(?<!\d\.)(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})(?![\d/])")
RE_DATA_EXT = re.compile(r"(?i)" + _DATA_EXT)
# do rótulo mais forte ao mais fraco: a sessão de julgamento vale mais que a publicação
ROTULOS_DATA = {"sess[ãa]o": "sessão", "julgamento": "julgamento", "decis[ãa]o": "decisão", "publica[çc][ãa]o": "publicação"}


def _iso(d: str, m: str, a: str) -> str | None:
    d, m = int(d), int(m)
    a = int(a) + (2000 if len(a) == 2 and int(a) < 70 else 1900 if len(a) == 2 else 0)
    if ANO_MIN <= a <= ANO_MAX and 1 <= m <= 12 and 1 <= d <= 31:
        return f"{a:04d}-{m:02d}-{d:02d}"
    return None


def _iso_ext(g: tuple[str, ...]) -> str | None:
    return _iso(g[0], str(MESES[g[1].lower()]), g[2])


def extrair_data(texto: str) -> tuple[str | None, str | None]:
    """(data ISO, origem). Origem: o rótulo que a trouxe ('sessão', 'publicação'...) ou 'final' (data solta que fecha o texto)."""
    for rot, nome in ROTULOS_DATA.items():
        prefixo = r"(?i)(?:Data\s+(?:d[aeo]s?\s+)?" + rot + r"|\b" + rot + r")[^\d\n]{0,30}?" if nome == "sessão" else r"(?i)Data\s+d[aeo]s?\s+" + rot + r"\s*:?\s*"
        for m in re.finditer(prefixo + r"(?:" + _DATA_NUM + "|" + _DATA_EXT + ")", texto):
            g = m.groups()
            iso = _iso(*g[:3]) if g[0] else (_iso_ext(g[3:6]) if g[3] else None)
            if iso:
                return iso, nome
    final = texto.rstrip(" .;")[-90:]
    achadas = [(m.end(), _iso(*m.groups())) for m in RE_DATA_NUM.finditer(final)]
    achadas += [(m.end(), _iso_ext(m.groups())) for m in RE_DATA_EXT.finditer(final)]
    achadas = [a for a in achadas if a[1]]
    return (max(achadas)[1], "final") if achadas else (None, None)


RE_PROC = [
    re.compile(r"(?i)\bProcesso(?:\s+[ÉE]tico[\s-]*Disciplinar|\s+Disciplinar|\s+Consulta)?\s*(?:n\s*[ºo°.]{0,3}\s*)?:?\s*(?:n\s*[ºo°.]{0,3}\s*)?:?\s*(\d[\d./-]*\d)"),
    re.compile(r"(?i)\bP\.?\s?(?:E\.?\s?)?D\.?\s*(?:n\s*[ºo°.]{0,3}\s*)?:?\s*(\d[\d./-]*\d)"),
    re.compile(r"(?i)\bProc\.\s*(?:n\s*[ºo°.]{0,3}\s*)?:?\s*(\d[\d./-]*\d)"),
    re.compile(r"(?i)\bP\.\s?Consulta\s*(?:n\s*[ºo°.]{0,3}\s*)?:?\s*(\d[\d./-]*\d)"),
]


def extrair_processo(texto: str) -> str | None:
    for r in RE_PROC:
        m = r.search(texto)
        if m:
            return m.group(1).strip(".,;/-")
    return None


RE_RELATOR = re.compile(r"(?i)\bRel[a-zç]{0,3}tora?\b\s*(?:\(\s*a\s*\)\s*)?(?:[:\u2013\u2014-]+|\s(?=Ju[ií]z|Dr))\s*(.{3,120}?)(?=\s+(?:Data|Ementa|Voto|Presidente|Revisor|Processo|Ac[óo]rd[ãa]o|Decis[ãa]o|Sess[ãa]o)\b|\.?\s*\d{1,2}\s*[./]\s*\d{1,2}\s*[./]\s*\d{2,4}|$)", re.M)
# 'Presidente e Relator da 3ª Turma do TED/OAB/GO – Dr. Fulano.' e 'PRESIDENTE. X RELATOR. Y VOTO. V.U' (rótulo com ponto)
RE_RELATOR_PRES = re.compile(r"(?i)\b(?:Presidente\s+e\s+Relator[a]?|Relator[a]?\s+e\s+Presidente)\s+d[aoe]\s+\d{1,2}\s*[ªº°]?\s*Turma[^\u2013\u2014-]{0,40}[\u2013\u2014-]\s*(.{3,100}?)(?=\.\s*\d|\.?\s*$)")
RE_RELATOR_PONTO = re.compile(r"\bRELATOR[A]?\.\s+(.{3,100}?)(?=\s+(?:VOTO|DATA|EMENTA|PRESIDENTE|PROCESSO)\b|$)")
RE_TITULO = re.compile(r"(?i)^(?:(?:ju[ií]z[a]?(?:\(a\))?|dr[a]?|des|conselheir[oa](?:\(a\))?|advogad[oa])\b\.?\s*)+")


def _nome_valido(bruto: str) -> str | None:
    nome = RE_TITULO.sub("", bruto.strip(" .;:,-\u2013\u2014")).strip(" .;:,-\u2013\u2014")
    palavras = nome.split()
    ok = len(nome) >= 5 and len(palavras) >= 2 and re.match(r"[A-Za-zÀ-ÿ]{3,}", palavras[0]) and not re.search(r"\d", nome)  # aceita 'Abrahão C. P. Viana'
    return nome.upper() if ok else None


def extrair_relator(texto: str) -> str | None:
    for rx in (RE_RELATOR, RE_RELATOR_PRES, RE_RELATOR_PONTO):
        for m in rx.finditer(texto):
            nome = _nome_valido(m.group(1))
            if nome:
                return nome
    return None


_UNIDADES = {"PRIMEIRA": 1, "SEGUNDA": 2, "TERCEIRA": 3, "QUARTA": 4, "QUINTA": 5, "SEXTA": 6, "SETIMA": 7, "OITAVA": 8, "NONA": 9}
_DEZENAS = {"DECIMA": 10, "VIGESIMA": 20, "TRIGESIMA": 30}


def _sem_acento(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.upper()) if unicodedata.category(c) != "Mn")


def ordinal_antes(texto: str, pos: int) -> int | None:
    """Número do ordinal que precede ``pos`` ('7ª', '9.ª', '__5___ª', 'Quarta', 'Décima Primeira')."""
    pre = texto[max(0, pos - 40):pos]
    m = re.search(r"(\d{1,2})[\s_.ªº°a\u2032'\u2019]*$", pre)
    if m:
        return int(m.group(1))
    palavras = re.findall(r"[A-Za-zÀ-ÿ]+", _sem_acento(pre))[-2:]
    if palavras and palavras[-1] in _UNIDADES:
        base = _DEZENAS.get(palavras[-2], 0) if len(palavras) == 2 else 0
        return base + _UNIDADES[palavras[-1]]
    if palavras and palavras[-1] in _DEZENAS:
        return _DEZENAS[palavras[-1]]
    return None


RE_COLEGIADO = re.compile(r"(?i)(?P<oe>[óo]rg[ãa]o\s+especial)|(?P<cam>c[âa]mara)\b|(?P<tur>turma)\b")
RE_PRESIDENTE_TURMA = re.compile(r"(?i)(?:Presidente(?:\s+e\s+Relator[a]?|\s+em\s+exerc[ií]cio)?|Relator[a]?\s+e\s+Presidente)\s+d[aoe]\s+(\d{1,2})\s*[ªº°]?\s*Turma")
RE_ORIGEM = re.compile(r"(?i)(?:exarad|prolatad|proferid|oriund|origin|recorrid|anterior|decis[ãa]o|ac[óo]rd[ãa]o)\w*\s+(?:d[aoe]s?|pel[ao]s?)?\s*(?:\w+\s+){0,2}$")


def extrair_orgao(texto: str) -> tuple[str, int | None, str | None]:
    """(tipo, número, nome como o texto o dá). Tipo: 'turma', 'camara', 'oe' ou 'ted' (o texto não nomeia o colegiado).

    Só vale o colegiado que o dispositivo (o trecho do ACÓRDÃO) nomeia como quem decide: 'acordam os integrantes da <ordinal>
    Câmara/Turma ...', 'a 6ª Câmara do Tribunal de Ética ... decidiu', 'o Órgão Especial ...', ou 'Presidente da <n>ª Turma'. A palavra
    'Câmara'/'Turma' solta na ementa ou citada como origem ('acórdão exarado pela 7ª Câmara') nunca decide.
    """
    m_a = RE_ACORDAO.search(texto)
    sec = texto[m_a.end():m_a.end() + 450] if m_a else texto
    sem_numero: tuple[str, int | None, str | None] | None = None
    for m in RE_COLEGIADO.finditer(sec):
        if RE_ORIGEM.search(sec[max(0, m.start() - 60):m.start()]):
            continue
        if m.group("oe"):
            return "oe", None, "Órgão Especial"
        n = ordinal_antes(sec, m.start())
        antes = [t for t in re.findall(r"[\w_.ªº°]+", sec[max(0, m.start() - 30):m.start()])[-2:]
                 if re.match(r"[\d_]", t) or _sem_acento(t) in _UNIDADES or _sem_acento(t) in _DEZENAS]
        julgadora = " Julgadora" if re.match(r"(?i)\s+julgadora", sec[m.end():m.end() + 12]) else ""
        achado = ("camara" if m.group("cam") else "turma", n, " ".join([*antes, m.group(0)]) + julgadora)
        if n:
            return achado
        sem_numero = sem_numero or achado  # 'acorda, em Turma, a 4ª CÂMARA': segue procurando o que traz o número
    if sem_numero:
        return sem_numero
    m = RE_PRESIDENTE_TURMA.search(texto)
    if m:
        return "turma", int(m.group(1)), f"{m.group(1)}ª Turma"
    return "ted", None, None


def orgao_go(tipo: str, n: int | None) -> tuple[str, str]:
    """(sigla, nome normalizado) no padrão do DEOAB_GO (OABGO-TED-T<n>), com Câmara do TED como OABGO-TED-C<n>."""
    if tipo == "turma" and n:
        return f"OABGO-TED-T{n}", f"Tribunal de Ética e Disciplina da OAB/GO/{n}ª Turma Disciplinar"
    if tipo == "camara" and n:
        return f"OABGO-TED-C{n}", f"Tribunal de Ética e Disciplina da OAB/GO/{n}ª Câmara Julgadora"
    if tipo == "oe":
        return "OABGO-TED-OE", "Tribunal de Ética e Disciplina da OAB/GO/Órgão Especial"
    return "OABGO-TED", "Tribunal de Ética e Disciplina da OAB/GO"


# o site cola rótulos sem espaço ('...Por unanimidadeData da sessão', '...GonçalvesData da Sessão'): separa antes do rótulo
RE_COLADO = re.compile(r"(?<=[a-zçãõáéíóúâêôà0-9.:])(?=(?:Data d[aeo]s?\s+(?:sess|publ|julg|decis)|Voto:|Juiz\(a\)|Presidente d|Relator|EMENTA|Ementa))")
RE_EMENTA = re.compile(r"(?i)\bEMENT[AE]\s*:*\s*")
RE_ACORDAO = re.compile(r"\b(?:AC[ÓO]RD[ÃA]O|Ac[óo]rd[ãa]o)\b(?:\s*[:.]\s*|\s*(?=Vistos\b))")  # só o rótulo (caixa alta ou inicial), não 'acórdão' no meio da frase
RE_PREFIXO_NUM = re.compile(r"^\s*\d{1,4}\s*/\s*\d{1,3}\s*\)?\s*(?=EMENT)", re.I)
RE_CONSULTA = re.compile(r"(?i)\bconsultas?\b")


def eh_consulta(ementa: str) -> bool:
    """Consulta em tese: a ementa abre com 'CONSULTA' (ou 'TED-GO - Órgão Especial. Consulta.') na primeira frase, sem ser representação."""
    cabeca = re.split(r"(?<=[A-Za-zÀ-ÿ\d])\.\s", ementa.strip(), maxsplit=2)
    cabeca = " ".join(cabeca[:2])[:130]
    return bool(RE_CONSULTA.search(cabeca)) and not re.search(r"(?i)representa|infra[çc][ãa]o|processo\s+[ée]tico", cabeca)


def separar(texto: str) -> tuple[str, str]:
    """(ementa, acórdão). O acórdão é o que vem depois de 'ACÓRDÃO:'; sem esse rótulo, tudo é ementa (formato de 2003-05)."""
    t = RE_PREFIXO_NUM.sub("", texto)
    m_a = RE_ACORDAO.search(t)
    antes, depois = (t[:m_a.start()], t[m_a.end():]) if m_a else (t, "")
    m_e = RE_EMENTA.search(antes)
    ementa = antes[m_e.end():] if m_e else antes
    return ementa.strip(), depois.strip()


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = "\n".join(p for p in (_limpar(x) for x in item.get("paragrafos") or []) if p)
    if len(texto) < 40 or texto.startswith("Parece que não encontramos"):
        return None  # sem texto de julgado
    plano = re.sub(r"\s+", " ", re.sub(RE_COLADO, " ", texto))
    ementa, acordao = separar(plano)
    if not ementa and not acordao:
        return None
    data, origem = extrair_data(plano)
    tipo, n, nome = extrair_orgao(plano)
    sigla, norm = orgao_go(tipo, n)
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa or plano,
        acordao=acordao,
        url=item["url"],
        orgao_raw=nome or "Tribunal de Ética e Disciplina",
        sigla_orgao=sigla,
        orgao_norm=norm,
        data_decisao=data,
        numero_processo=extrair_processo(plano),
        relator=extrair_relator(plano),
        consulta=eh_consulta(ementa),
        publicacao="Site da OAB/GO - data da publicação" if origem == "publicação" else None,
    )
    rec["is_ted_disciplinar"] = 1  # ementário do TED: matéria ético-disciplinar (consulta em tese também fica 1)
    rec["tem_inteiro_teor"] = 0  # só ementa + dispositivo, sem relatório e voto
    return rec
