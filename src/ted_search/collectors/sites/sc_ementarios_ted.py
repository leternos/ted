"""OAB/SC - ementários do Tribunal de Ética e Disciplina (acórdãos das Turmas do TED), 1995 a 2026.

Site Next.js (App Router) com o HTML renderizado no servidor: ``GET /ted-ementarios?page=N&yearStart=&yearEnd=&text=``
traz 12 cartões por página (a última, menos), agrupados por ``<h2>`` de ano, em ordem decrescente. O payload RSC embutido
declara ``totalPages``; o total de itens não é declarado, então o ``esperado`` é (N - 1) x cartões da página 1 + cartões
da última página. Cada cartão tem o título ("Processo de Representação nº X"), o número do acórdão ("Acórdão nº N/AAAA")
e o texto integral (``whitespace-pre-wrap``): partes, relator, ementa e, nos anos recentes, o dispositivo "ACORDAM ...".
O texto entra no banco exatamente como o site publica (partes em iniciais, sem abreviar, mascarar ou cortar nada).

Coleta educada (uma conexão, 1 requisição a cada 2 s). Cada página bruta é guardada em ``<raw>/paginas/NNNN.html.gz`` e
reaproveitada se o processo morrer: a coleta é retomável. ``listar`` percorre as páginas e já traz o texto de cada item;
``baixar`` só devolve o item. A leitura dos campos é feita em ``normalizar`` (puro), sem nova rede.
"""

from __future__ import annotations

import asyncio
import gzip
import html as html_lib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from ted_search.collectors.deoab_nacional import eh_materia_disciplinar
from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import DB_PATH, RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "SC", "ementarios-ted"
BASE = "https://www.oab-sc.org.br/ted-ementarios"
PAUSA = ce.PAUSA  # 2 s entre requisições
ESPERAS_RETRY = (10, 30, 90, 240, 600)  # espera crescente em 429/5xx/rede antes de desistir

RE_TOTAL_PAGINAS = re.compile(r'totalPages\\*":(\d+)')
RE_CARTAO = re.compile(r'<div class="[^"]*rounded-xl p-6[^"]*"><div class="space-y-3"><div>(.*?)</div></div></div>(?=<div class="[^"]*rounded-xl p-6|</div></div></div>)', re.S)
RE_ANO_H2 = re.compile(r'<h2 class="text-2xl[^>]*>(\d{4})</h2>')
RE_CARTAO_INICIO = re.compile(r'<div class="[^"]*rounded-xl p-6[^"]*"><div class="space-y-3">')
RE_P_TITULO = re.compile(r'<p class="text-sm font-semibold[^>]*>(.*?)</p>', re.S)
RE_P_TEXTO = re.compile(r'<p class="text-gray-700[^"]*whitespace-pre-wrap[^"]*">(.*?)</p>', re.S)


# ---------------------------------------------------------------------------------------------------------------------
# Rede (uma conexão, cache bruto por página)
# ---------------------------------------------------------------------------------------------------------------------

def url_pagina(n: int) -> str:
    return f"{BASE}?page={n}&yearStart=&yearEnd=&text="


def _cache_dir() -> Path:
    d = raw_dir_fonte(UF, SLUG, RAW_DIR) / "paginas"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _get(client: Any, sem: asyncio.Semaphore, url: str) -> str:
    """GET educado. 401/403/captcha/desafio: ``BloqueioSite`` na hora (sem insistir). 429/5xx/rede: espera crescente."""
    erro: Exception | None = None
    for n in range(len(ESPERAS_RETRY) + 1):
        if ce._bloqueio:
            raise ce.BloqueioSite(ce._bloqueio[0])
        try:
            async with sem:
                resp = await client.get(url, headers=ce.HEADERS)
                await asyncio.sleep(PAUSA)
            status = getattr(resp, "status_code", 200)
            if status in (401, 403):
                ce._bloqueio.append(f"HTTP {status} em {url}")
                raise ce.BloqueioSite(ce._bloqueio[0])
            if status == 429 or status >= 500:
                erro = RuntimeError(f"HTTP {status}")
                retry = resp.headers.get("retry-after", "") if hasattr(resp, "headers") else ""
                espera = max(ESPERAS_RETRY[min(n, len(ESPERAS_RETRY) - 1)], int(retry) if retry.isdigit() else 0)
            else:
                resp.raise_for_status()
                corpo = resp.content.decode("utf-8", "replace")
                if any(m in corpo[:200000] for m in ce.MARCAS_CAPTCHA) and "totalPages" not in corpo:
                    ce._bloqueio.append(f"captcha/desafio em {url}")
                    raise ce.BloqueioSite(ce._bloqueio[0])
                if "totalPages" in corpo:
                    return corpo
                erro = ValueError("resposta fora do padrão do site")
                espera = ESPERAS_RETRY[min(n, len(ESPERAS_RETRY) - 1)]
        except ce.BloqueioSite:
            raise
        except Exception as exc:  # noqa: BLE001
            erro = exc
            espera = ESPERAS_RETRY[min(n, len(ESPERAS_RETRY) - 1)]
        if n < len(ESPERAS_RETRY):
            await asyncio.sleep(espera)
    raise RuntimeError(f"falha em {url}: {erro}")


async def _pagina(client: Any, sem: asyncio.Semaphore, n: int) -> str:
    arq = _cache_dir() / f"{n:04d}.html.gz"
    if arq.exists():
        try:
            return gzip.decompress(arq.read_bytes()).decode("utf-8")
        except Exception:  # noqa: BLE001 - cache truncado: refaz
            arq.unlink(missing_ok=True)
    h = await _get(client, sem, url_pagina(n))
    tmp = arq.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(h.encode("utf-8"), 6))
    tmp.replace(arq)
    return h


# ---------------------------------------------------------------------------------------------------------------------
# Leitura da página
# ---------------------------------------------------------------------------------------------------------------------

def total_paginas(h: str) -> int | None:
    m = RE_TOTAL_PAGINAS.search(h)
    return int(m.group(1)) if m else None


def cartoes(h: str) -> list[dict[str, Any]]:
    """Cartões de uma página, na ordem: {ano_secao, titulo, acordao_rotulo, texto} (texto exatamente como publicado)."""
    marcas = [(m.start(), "ano", m.group(1)) for m in RE_ANO_H2.finditer(h)]
    marcas += [(m.start(), "cartao", None) for m in RE_CARTAO_INICIO.finditer(h)]
    marcas.sort()
    saida: list[dict[str, Any]] = []
    ano = None
    for k, (pos, tipo, val) in enumerate(marcas):
        if tipo == "ano":
            ano = val
            continue
        fim = marcas[k + 1][0] if k + 1 < len(marcas) else len(h)
        bloco = h[pos:fim]
        titulos = [re.sub(r"<[^>]+>", "", t) for t in RE_P_TITULO.findall(bloco)]
        textos = RE_P_TEXTO.findall(bloco)
        saida.append({  # cartão sem texto (o site publica alguns só com "-" e o número do acórdão) também é item
            "ano_secao": ano,
            "titulo": html_lib.unescape(titulos[0]).strip() if titulos else "",
            "acordao_rotulo": html_lib.unescape(titulos[1]).strip() if len(titulos) > 1 else "",
            "texto": html_lib.unescape(textos[0]) if textos else "",
        })
    return saida


_info: dict[str, Any] = {}


async def _extremos(client: Any, sem: asyncio.Semaphore) -> dict[str, Any]:
    if _info:
        return _info
    p1 = await _pagina(client, sem, 1)
    n = total_paginas(p1)
    if not n:
        raise RuntimeError("o site não declarou o número de páginas")
    ultima = p1 if n == 1 else await _pagina(client, sem, n)
    por_pagina, na_ultima = len(cartoes(p1)), len(cartoes(ultima))
    _info.update({"paginas": n, "por_pagina": por_pagina, "na_ultima": na_ultima})
    return _info


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    i = await _extremos(client, sem)
    return (i["paginas"] - 1) * i["por_pagina"] + i["na_ultima"]


def _num_acordao(rotulo: str) -> str | None:
    m = re.search(r"(\d+)\s*/\s*(\d{4})", re.sub(r"\s+", "", rotulo).replace("nº", "nº "))
    return f"{int(m.group(1))}/{m.group(2)}" if m else None


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    i = await _extremos(client, sem)
    itens: list[dict[str, Any]] = []
    usados: dict[str, int] = {}
    for n in range(1, i["paginas"] + 1):
        cs = cartoes(await _pagina(client, sem, n))
        if n < i["paginas"] and len(cs) != i["por_pagina"]:
            raise RuntimeError(f"página {n} trouxe {len(cs)} itens (esperava {i['por_pagina']})")
        for c in cs:
            base = _num_acordao(c["acordao_rotulo"]) or f"p{n}-{len(itens)}"
            usados[base] = usados.get(base, 0) + 1
            iid = base if usados[base] == 1 else f"{base}#{usados[base]}"  # mesmo nº de acórdão repetido no site: não perde item
            itens.append({"id": iid, "pagina": n, **c})
        if n % 50 == 0:
            print(f"[site] SC: página {n}/{i['paginas']}, {len(itens)} itens", flush=True)
    depois = cartoes(await _pagina(client, sem, i["paginas"] + 1))  # confere que não há nada além da última página
    if depois:
        raise RuntimeError(f"a página {i['paginas'] + 1} ainda traz {len(depois)} itens: o total declarado não vale")
    return itens


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return dict(item)  # a listagem já traz o texto integral do item; nada mais a buscar


# ---------------------------------------------------------------------------------------------------------------------
# Texto -> registro
# ---------------------------------------------------------------------------------------------------------------------

def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def chave_ementa(s: str) -> str:
    """Ementa normalizada para comparar com o Diário: sem acento, só letras, minúsculas, 200 primeiras letras."""
    return re.sub(r"[^a-z]", "", _sem_acento(s).lower())[:200]


_PREFIXO_EMENTA = re.compile(r"(?is)\bEmenta\s*:\s*")
MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RE_JULGADO = re.compile(r"(?i)Julgad[oa]s?\s+em\s+(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\b")
RE_DATA_EXT = re.compile(r"(?i)(\d{1,2})\s*(?:º|°)?\s*(?:de\s+)?(" + "|".join(MESES) + r")\s*(?:de\s+|,\s*)?(\d{4})")
RE_ORGAO = re.compile(r"(?is)ACORDAM\b.{0,120}?\b(\d{1,2})\s*[ªº°]?\s*(Turma|C[âa]mara)\b")
INICIO_DIARIO = "2018-10-01"  # a partir daqui o Diário (DEOAB_SC) pode ter publicado o mesmo acórdão


def _iso(d: str, m: str, a: str) -> str | None:
    d, m = int(d), int(m)
    a = int(a) + (2000 if len(a) == 2 and int(a) < 70 else 1900 if len(a) == 2 else 0)
    return f"{a:04d}-{m:02d}-{d:02d}" if 1993 <= a <= 2026 and 1 <= m <= 12 and 1 <= d <= 31 else None


def extrair_data(texto: str) -> str | None:
    """Data da sessão: 'Julgado em dd.mm.aa' (anos antigos) ou a data por extenso da assinatura que fecha o acórdão (últimos 300
    caracteres, para não pegar data citada na ementa). Sem uma das duas, None: o registro fica só com o ano."""
    m = RE_JULGADO.search(texto[:160])  # só o cabeçalho: 'Julgado em' dentro da ementa cita outro julgamento
    if m:
        return _iso(*m.groups())
    achadas = [_iso(d, str(MESES[mes.lower()]), a) for d, mes, a in RE_DATA_EXT.findall(texto[-300:])]
    achadas = [a for a in achadas if a]
    return achadas[-1] if achadas else None


_RE_LINHA_CABECALHO = re.compile(r"(?i)^\s*(?:Ac[óo]rd[ãa]o|Processo|Representantes?\b|Representad[oa]s?\b|Repte|Reptes|Repdos?|Repdas?|Reqtes?|Reqdos?|Relat|Presidente|Revisor)")
_RE_ACORDAO_FIM_CABECALHO = re.compile(r"(?i)\bAc[óo]rd[ãa]o\s*n[ºo°.]{0,3}\s*\d+\s*/\s*\d{2,4}\s*\.\s*")


def _corpo_sem_rotulo(texto: str) -> str:
    """Anos antigos não rotulam a ementa ('Ementa:'): ela é o que vem depois do cabeçalho (linhas Acórdão/Processo/Representante/
    Relator/Presidente) ou, no formato corrido, depois de 'Acórdão nº N/AAAA.'."""
    linhas = texto.split("\n")
    ult = -1
    for k, ln in enumerate(linhas[:14]):
        if len(ln) <= 200 and _RE_LINHA_CABECALHO.match(ln) and (not re.match(r"(?i)\s*Processo", ln) or re.search(r"\d", ln[:90])):
            ult = k
    if ult >= 0 and ult + 1 < len(linhas) and any(ln.strip() for ln in linhas[ult + 1:]):
        return "\n".join(linhas[ult + 1:])
    m = _RE_ACORDAO_FIM_CABECALHO.search(texto[:800])
    if m:
        return texto[m.end():]
    m = re.search(r"(?i)Presidente\s*:\s*(?:Dra?\.\s*)?[^.]{3,80}\.\s+", texto[:900])  # cabeçalho corrido que termina na linha do Presidente
    return texto[m.end():] if m else ""


def separar(texto: str) -> tuple[str, str]:
    """(ementa, dispositivo). Ementa: o que vem depois de 'Ementa:' (com as aspas que o site põe) ou, sem rótulo, depois do
    cabeçalho, até o dispositivo 'Vistos, relatados ... ACORDAM'; o dispositivo vai daí ao fim. Nada é reescrito."""
    m = _PREFIXO_EMENTA.search(texto)
    resto = texto[m.end():] if m else _corpo_sem_rotulo(texto)
    corte = re.search(r"(?:Vistos,\s+relatados|VISTOS,\s+RELATADOS|ACORDAM\b|Acordam\b)", resto)
    ementa, dispositivo = (resto[:corte.start()], resto[corte.start():]) if corte else (resto, "")
    if len(_letras(ementa)) < 10 and dispositivo.strip():
        # o site publica só o dispositivo sob 'Ementa:' (ou sem rótulo): o que ele publica como ementa não se descarta
        ementa, dispositivo = resto, ""
    return ementa.strip(), dispositivo.strip()


def orgao_sc(texto: str) -> tuple[str, str, str]:
    """(orgao_raw, sigla, norm): a turma que o dispositivo diz ('ACORDAM os Integrantes da 2ª Turma'); senão o TED/SC."""
    m = RE_ORGAO.search(texto)
    if m:
        n, tipo = int(m.group(1)), m.group(2).capitalize().replace("Camara", "Câmara")
        return (f"{n}ª {tipo} do Tribunal de Ética e Disciplina", f"OABSC-TED-{'T' if tipo == 'Turma' else 'C'}{n}",
                f"Tribunal de Ética e Disciplina da OAB/SC/{n}ª {tipo}")
    return "Tribunal de Ética e Disciplina", "OABSC-TED", "Tribunal de Ética e Disciplina da OAB/SC"


def processo_publicado(item: dict[str, Any]) -> str | None:
    """Número do processo como o título do cartão publica ('Processo de Representação nº 192/93' -> '192/93')."""
    m = re.search(r"(?i)\bn[ºo°.]*\s*(\d[\d./-]*)", item.get("titulo") or "")
    return m.group(1).strip(".,;/-") if m else None


def eh_consulta(titulo: str) -> bool:
    return bool(re.search(r"(?i)\bconsulta\b", titulo)) and not re.search(r"(?i)representa", titulo)


RE_RELATOR = re.compile(r"(?i)\bRelat(?:or|ora)(?:\s+d[oa]s?\s+[^:\n]{0,40}?)?\s*:\s*([^\n]+?)(?=\s*(?:\n|\bPresidente\b|\bAc[óo]rd[ãa]o\b|\bEmenta\s*:|$))")
RE_PARTES = re.compile(r"(?is)\b(Repte|Reptes|Representantes?|Reqtes?|Requerentes?)\b.{0,400}?(?=\bRelat)")


def extrair_relator(texto: str) -> str | None:
    """Relator como o cabeçalho publica ('Relator:', 'Relatora:', 'Relator do voto divergente:'), com título e tudo."""
    m = RE_RELATOR.search(texto[:1200])
    return (re.sub(r"\s+", " ", m.group(1)).strip(" .;,") or None) if m else None


def extrair_partes(texto: str) -> list[str]:
    m = RE_PARTES.search(texto[:1200])
    return [re.sub(r"[ \t]+", " ", m.group(0)).strip()] if m else []


def _letras(s: str) -> str:
    return re.sub(r"[^a-z]", "", _sem_acento(s).lower())


_RE_SE_DIZ_DISCIPLINAR = re.compile(r"(?i)disciplinar|representa[çc][ãa]o|suspens[ãa]o\s+preventiva|consulta")


def eh_disciplinar(titulo: str, texto: str) -> bool:
    """É o TED: tudo é disciplinar, salvo o que ``eh_materia_disciplinar`` marca como não e que o próprio cabeçalho não apresenta
    como processo disciplinar, representação, suspensão preventiva ou consulta (a heurística é do Diário e erra em ementário)."""
    if eh_materia_disciplinar("Tribunal de Ética e Disciplina", titulo, texto):
        return True
    return bool(_RE_SE_DIZ_DISCIPLINAR.search(f"{titulo} {texto[:400]}"))


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    """Registro de ``julgados``; None = rejeitado (o motivo fica em ``MOTIVOS``). O texto vai como o site publica."""
    texto = item.get("texto") or ""
    iid = str(item["id"])
    ementa, dispositivo = separar(texto)
    if len(_letras(ementa)) < 10:
        MOTIVOS[iid] = "ementa vazia"
        return None
    if iid in _DESCARTES:
        MOTIVOS[iid] = _DESCARTES[iid]
        return None
    num = _num_acordao(item.get("acordao_rotulo") or "")
    data = extrair_data(texto)
    orgao_raw, sigla, norm = orgao_sc(texto)
    titulo = item.get("titulo") or ""
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa,
        acordao=dispositivo,
        inteiro_teor="\n\n".join(t for t in (ementa, dispositivo) if t),
        url=url_pagina(int(item.get("pagina") or 1)),
        orgao_raw=orgao_raw, sigla_orgao=sigla, orgao_norm=norm,
        data_decisao=data,
        numero_processo=processo_publicado(item),
        numero_ementa=num.split("/")[0] if num else None,
        relator=extrair_relator(texto),
        consulta=eh_consulta(titulo),
        publicacao="Site da OAB/SC - ementário do TED",
    )
    rec["ano_ementa"] = int(num.split("/")[1]) if num else (int(item["ano_secao"]) if item.get("ano_secao") else None)
    rec["partes_json"] = json.dumps(extrair_partes(texto), ensure_ascii=False)
    rec["pdf_url"] = None
    rec["tem_inteiro_teor"] = 0  # só ementa + dispositivo, sem relatório e voto
    rec["is_ted_disciplinar"] = 1 if eh_disciplinar(titulo, texto) else 0
    return rec


# ---------------------------------------------------------------------------------------------------------------------
# Descartes (feitos em ``preparar``, que o motor chama antes de normalizar): duplicata interna e sobreposição com o Diário
# ---------------------------------------------------------------------------------------------------------------------

MOTIVOS: dict[str, str] = {}
_DESCARTES: dict[str, str] = {}


def _raw_itens() -> Path:
    return raw_dir_fonte(UF, SLUG, RAW_DIR) / "itens.jsonl"


def texto_diario(db_path: Path = DB_PATH) -> str:
    """Todo o inteiro_teor_texto do DEOAB_SC, normalizado (só letras, sem acento), separado por '|'."""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=120) as conn:
        linhas = conn.execute("SELECT COALESCE(inteiro_teor_texto, descricao_ementa, '') FROM julgados WHERE fonte = 'DEOAB_SC'").fetchall()
    return "|".join(_letras(t) for (t,) in linhas)


def janela_diario(item: dict[str, Any]) -> bool:
    """O acórdão foi julgado de 2018-10-01 em diante (sem data: só se o ano do acórdão for 2019 ou mais)."""
    data = extrair_data(item.get("texto") or "")
    if data:
        return data >= INICIO_DIARIO
    num = _num_acordao(item.get("acordao_rotulo") or "")
    ano = int(num.split("/")[1]) if num else int(item.get("ano_secao") or 0)
    return ano >= 2019


def calcular_descartes(itens: list[dict[str, Any]], diario: str) -> dict[str, str]:
    """{id: motivo}. Duplicata interna (mesmo processo + mesma ementa): fica o primeiro da listagem. Sobreposição: a ementa
    normalizada (200 primeiras letras) aparece no texto de algum registro DEOAB_SC."""
    saida: dict[str, str] = {}
    vistos: set[tuple[str | None, str]] = set()
    for it in sorted(itens, key=lambda x: (int(x.get("pagina") or 0), str(x["id"]))):
        ementa, _ = separar(it.get("texto") or "")
        chave = _letras(ementa)
        if len(chave) < 10:
            continue
        par = (processo_publicado(it), chave)
        if par in vistos:
            saida[str(it["id"])] = "duplicata interna (mesmo processo e mesma ementa)"
            continue
        vistos.add(par)
        if diario and janela_diario(it) and chave[:200] in diario:
            saida[str(it["id"])] = "sobreposição com DEOAB_SC (ementa no texto do Diário)"
    return saida


def preparar(db_path: Path = DB_PATH) -> None:
    MOTIVOS.clear()
    _DESCARTES.clear()
    arq = _raw_itens()
    if not arq.exists():
        return
    with arq.open(encoding="utf-8") as fh:
        itens = [json.loads(ln) for ln in fh if ln.strip()]
    _DESCARTES.update(calcular_descartes(itens, texto_diario(db_path)))
