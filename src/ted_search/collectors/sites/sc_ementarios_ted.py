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
RE_PROCESSO = re.compile(r"(?i)^\s*(?:Processo\b[^\n]*?)\bn[ºo°.]*\s*:?\s*(\d[\d./-]*\d|\d)")
RE_RELATOR = re.compile(r"(?i)\bRelat(?:or|ora)\s*(?:\([ao]\))?\s*:\s*(.+?)(?=\s*(?:\n|\.\s+[A-ZÀ-Ú][a-zà-ú]+:|\s+Presidente:|\s+Ac[óo]rd[ãa]o\b|\s+Ementa:|$))")
RE_JULGADO = re.compile(r"(?i)Julgad[oa]s?\s+em\s+(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\b")
MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RE_DATA_EXT = re.compile(r"(?i)(\d{1,2})\s*(?:º|°)?\s*de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})")
RE_ORGAO = re.compile(r"(?i)ACORDAM\s+(?:os\s+)?(?:Integrantes|Membros|Componentes|Julgadores)?[^.]{0,60}?\b(?:d[aoe]s?\s+)?(\d{1,2})\s*[ªº°]?\s*(?:\.)?\s*(Turma|C[âa]mara)")


def _iso(d: str, m: str, a: str) -> str | None:
    d, m = int(d), int(m)
    a = int(a) + (2000 if len(a) == 2 and int(a) < 70 else 1900 if len(a) == 2 else 0)
    return f"{a:04d}-{m:02d}-{d:02d}" if 1993 <= a <= 2026 and 1 <= m <= 12 and 1 <= d <= 31 else None


def extrair_data(texto: str) -> str | None:
    """Data da sessão/julgamento: 'Julgado em dd.mm.aa' (anos antigos) ou a data por extenso que fecha o acórdão."""
    m = RE_JULGADO.search(texto)
    if m:
        return _iso(*m.groups())
    cauda = texto[texto.upper().rfind("ACORDAM"):] if "ACORDAM" in texto.upper() else ""
    achadas = [_iso(d, str(MESES[mes.lower()]), a) for d, mes, a in RE_DATA_EXT.findall(cauda)]
    achadas = [a for a in achadas if a]
    return achadas[-1] if achadas else None


def separar(texto: str) -> tuple[str, str]:
    """(ementa, dispositivo). Ementa: o que vem depois de 'Ementa:' (entre aspas ou não) até o dispositivo 'Vistos, relatados...
    ACORDAM'; o dispositivo vai daí ao fim. Sem dispositivo, tudo depois de 'Ementa:' é ementa. Nada é cortado ou reescrito."""
    m = _PREFIXO_EMENTA.search(texto)
    resto = texto[m.end():] if m else texto
    corte = re.search(r"(?s)(?:\n\s*)?\s*(Vistos,\s+relatados|VISTOS,\s+RELATADOS|ACORDAM\b|Acordam\b)", resto)
    ementa, dispositivo = (resto[:corte.start()], resto[corte.start():]) if corte else (resto, "")
    return ementa.strip(), dispositivo.strip()


def orgao_sc(texto: str) -> tuple[str, str, str]:
    """(orgao_raw, sigla, norm): a turma que o dispositivo diz ('ACORDAM os Integrantes da 2ª Turma'); senão o TED/SC."""
    m = RE_ORGAO.search(texto)
    if m:
        n, tipo = int(m.group(1)), m.group(2).capitalize().replace("Camara", "Câmara")
        return f"{n}ª {tipo} do Tribunal de Ética e Disciplina", f"OABSC-TED-{'T' if tipo == 'Turma' else 'C'}{n}", f"Tribunal de Ética e Disciplina da OAB/SC/{n}ª {tipo}"
    return "Tribunal de Ética e Disciplina", "OABSC-TED", "Tribunal de Ética e Disciplina da OAB/SC"


def processo_publicado(item: dict[str, Any]) -> str | None:
    t = (item.get("titulo") or "").strip()
    m = re.search(r"(?i)\bn[ºo°.]*\s*(\d[\d./-]*)", t)
    return m.group(1).strip(".,;/-") if m else None


def eh_consulta(titulo: str, ementa: str) -> bool:
    return bool(re.search(r"(?i)\bconsulta\b", titulo)) and not re.search(r"(?i)representa", titulo)


def extrair_relator(texto: str) -> str | None:
    m = re.search(r"(?i)\bRelat(?:or|ora)\s*(?:\([ao]\))?\s*:\s*([^\n]+?)(?=\s*(?:\n|\bPresidente\b|\bAc[óo]rd[ãa]o\b|\bEmenta\s*:|$))", texto)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip(" .;,") or None


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    """Registro de ``julgados``. Ementa vazia -> None (rejeitado, com motivo). O texto vai como o site publica."""
    texto = item.get("texto") or ""
    ementa, dispositivo = separar(texto)
    if not ementa.strip(" \"“”'.;"):
        MOTIVOS[str(item["id"])] = "ementa vazia"
        return None
    ementa_txt = ementa
    num = _num_acordao(item.get("acordao_rotulo") or "")
    data = extrair_data(texto)
    ano_acordao = int(num.split("/")[1]) if num else (int(item["ano_secao"]) if item.get("ano_secao") else None)
    sigla_raw = orgao_sc(texto)
    consulta = eh_consulta(item.get("titulo") or "", ementa)
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa_txt,
        acordao=dispositivo,
        inteiro_teor="\n\n".join(t for t in (ementa_txt, dispositivo) if t),
        url=url_pagina(int(item.get("pagina") or 1)),
        orgao_raw=sigla_raw[0], sigla_orgao=sigla_raw[1], orgao_norm=sigla_raw[2],
        data_decisao=data,
        numero_processo=processo_publicado(item),
        numero_ementa=num.split("/")[0] if num else None,
        relator=extrair_relator(texto),
        consulta=consulta,
    )
    rec["ano_ementa"] = ano_acordao if not data else int(data[:4])
    if not data and item.get("ano_secao"):
        rec["ano_ementa"] = int(item["ano_secao"])
    rec["pdf_url"] = None
    rec["tem_inteiro_teor"] = 0  # só ementa + dispositivo, sem relatório e voto
    rec["is_ted_disciplinar"] = 1
    rec["publicacao_str"] = "Site da OAB/SC - ementário do TED"
    return rec


MOTIVOS: dict[str, str] = {}
