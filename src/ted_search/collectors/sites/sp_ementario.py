"""OAB/SP - Ementário da Turma de Ética Profissional (1ª Turma do TED/SP, a "Turma Deontológica", TED-I).

Consultas em tese respondidas pela Turma: ementa e, de 1999/2000 em diante, relatório e parecer.

Listagem: ``POST /_ajax/ementario.php`` com ``card=Turma de Ética Profissional``, ``action=load-more``, ``ano=AAAA``,
``keyword=``, ``start=N`` (12 por vez). O total do ano vem em ``data.origget.cachemode`` (``>220:220``); ``btnMore=0``
marca a última página. Detalhe: ``POST /_ajax/load-modal.php`` com ``bracket=5&slug=<data-slug>``.

O id estável é o ``data-guid``: há números de consulta repetidos dentro do mesmo ano. A listagem é feita ano a ano
("Todos os anos" devolve itens fora de ordem e o ``load-more`` dele derruba a conexão).

O "ano" é a etiqueta do site e nem sempre é o ano do processo. A data da decisão vem, nesta ordem:
1. da linha de votação do próprio item ("Proc. E-2.465/01 - v.u. em 21/02/02 ...");
2. da sessão em que a consulta aparece na Coletânea de Ementas por Ano da mesma Turma (sistema legado da OAB/SP,
   1994 a 2018: "SESSÃO DE 10 DE FEVEREIRO DE 1994" e, abaixo, as ementas daquela sessão), casada pelo número;
3. em último caso, ``AAAA-01-01`` do ano-etiqueta.
O item baixado diz de onde a data saiu em ``data_origem`` ("texto", "coletanea" ou "ano-etiqueta").
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Any

from ted_search.fontes import montar_registro

UF, SLUG = "SP", "ementario"
BASE = "https://www.oabsp.org.br"
URL_LISTA = f"{BASE}/_ajax/ementario.php"
URL_DETALHE = f"{BASE}/_ajax/load-modal.php"
URL_PAGINA = f"{BASE}/tribunal-de-etica-e-disciplina/ementario"
URL_COLETANEA = "https://www2.oabsp.org.br/asp/tribunal_etica/pop_ementasano.asp"  # ?ano=AAAA, 1994 a 2018
ANOS_COLETANEA = range(1994, 2019)
CARD = "Turma de Ética Profissional"  # é assim que o site nomeia o órgão no seletor do ementário
BRACKET = "5"
POR_PAGINA = 12
PAUSA = 0.2  # segundos entre requisições de cada uma das (no máximo 4) conexões
SIGLA_ORGAO = "OABSP-TDEONT"
ORGAO_NORM = "OAB/SP - Turma Deontológica (TED-I)"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": URL_PAGINA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

RE_ITEM = re.compile(r'data-guid="(\d+)"\s+data-slug="([^"]*)"\s*>(.*?)</a>', re.S)
RE_TOTAL = re.compile(r">(\d+):(\d+)\[")
RE_ANO_OPCAO = re.compile(r'<option value="((?:19|20)\d\d)"')

_primeiras: dict[int, dict[str, Any]] = {}  # primeira página de cada ano, reaproveitada entre esperado() e listar()
_anos: list[int] = []
_listagem: dict[str, Any] = {}  # {"itens", "declarado", "linhas_repetidas"}: a listagem completa, feita uma vez por execução
_coletanea: dict[int, dict[str, list[str]]] = {}  # ano -> {dígitos do número: [datas ISO das sessões]}
_coletanea_travas: dict[int, asyncio.Lock] = {}


# ---------------------------------------------------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------------------------------------------------

async def _post(client: Any, sem: asyncio.Semaphore, url: str, data: dict[str, Any], tentativas: int = 4, timeout: float | None = None) -> dict[str, Any]:
    erro: Exception | None = None
    extra = {"timeout": timeout} if timeout else {}
    for n in range(tentativas):
        try:
            async with sem:
                resp = await client.post(url, data=data, headers=HEADERS, **extra)
                await asyncio.sleep(PAUSA)  # site pequeno: não martelar
            resp.raise_for_status()
            corpo = resp.json()
            if isinstance(corpo, dict):
                return corpo
            erro = ValueError("resposta não é um objeto JSON")
        except Exception as exc:  # rede, HTTP ou JSON: tenta de novo com espera crescente
            erro = exc
        await asyncio.sleep(1.5 * (n + 1))
    raise RuntimeError(f"falha em {url} {data}: {erro}")


async def _listar_anos(client: Any, sem: asyncio.Semaphore) -> list[int]:
    if not _anos:
        corpo = await _post(client, sem, URL_LISTA, {"card": CARD, "action": "card"})
        _anos.extend(sorted({int(a) for a in RE_ANO_OPCAO.findall(corpo.get("msg") or "")}))
        if not _anos:
            raise RuntimeError("o site não devolveu a lista de anos do ementário")
    return _anos


async def _pagina(client: Any, sem: asyncio.Semaphore, ano: int, start: int, total: int | None = None) -> dict[str, Any]:
    """Uma página da listagem. Sob carga o site devolve ``err=1`` para páginas que existem: nesse caso, insiste."""
    if start == 0 and ano in _primeiras:
        return _primeiras[ano]
    dados = {"card": CARD, "action": "load-more", "ano": str(ano), "keyword": "", "start": str(start)}
    corpo: dict[str, Any] = {}
    for n in range(6):
        corpo = await _post(client, sem, URL_LISTA, dados)
        vazia = bool(corpo.get("err")) or not RE_ITEM.search(corpo.get("msg") or "")
        deve_existir = start == 0 or (total is not None and start < total)
        if not vazia or not deve_existir:
            break
        await asyncio.sleep(3.0 * (n + 1))
    if start == 0 and not corpo.get("err"):
        _primeiras[ano] = corpo
    return corpo


async def _datas_coletanea(client: Any, sem: asyncio.Semaphore, ano: int) -> dict[str, list[str]]:
    """Datas de sessão por número de consulta, da página do ano na coletânea legada (uma requisição por ano)."""
    if ano not in ANOS_COLETANEA:
        return {}
    async with _coletanea_travas.setdefault(ano, asyncio.Lock()):
        if ano in _coletanea:
            return _coletanea[ano]
        erro: Exception | None = None
        for n in range(4):
            try:
                async with sem:
                    resp = await client.get(URL_COLETANEA, params={"ano": str(ano)}, headers={"User-Agent": HEADERS["User-Agent"]})
                    await asyncio.sleep(PAUSA)
                resp.raise_for_status()
                datas = datas_da_coletanea(html_para_texto(resp.content.decode("utf-8", "replace")))
                if datas:
                    _coletanea[ano] = datas
                    return datas
                erro = ValueError("página da coletânea sem sessões")
            except Exception as exc:
                erro = exc
            await asyncio.sleep(2.0 * (n + 1))
        raise RuntimeError(f"falha na coletânea de {ano}: {erro}")


def total_declarado(corpo: dict[str, Any]) -> int | None:
    """Total do ano que o backend declara em ``data.origget.cachemode`` (``...>220:220[...``)."""
    try:
        cachemode = corpo["data"]["origget"]["cachemode"]
    except (KeyError, TypeError):
        return None
    m = RE_TOTAL.search(cachemode or "")
    return int(m.group(2)) if m else None


def itens_da_pagina(corpo: dict[str, Any], ano: int) -> list[dict[str, Any]]:
    """Itens de uma página da listagem. Cada item aparece duas vezes no HTML (título e descrição)."""
    vistos: dict[str, dict[str, Any]] = {}
    for guid, slug, rotulo in RE_ITEM.findall(corpo.get("msg") or ""):
        rotulo = _limpar_linha(html_lib.unescape(re.sub(r"<[^>]+>", "", rotulo)))
        if guid not in vistos:
            vistos[guid] = {"id": guid, "slug": slug, "titulo": rotulo, "descricao": "", "ano_etiqueta": ano}
        elif not vistos[guid]["descricao"]:
            vistos[guid]["descricao"] = rotulo
    return list(vistos.values())


async def _listar_ano(client: Any, sem: asyncio.Semaphore, ano: int) -> tuple[list[dict[str, Any]], int | None, int]:
    """Itens distintos do ano, total de linhas que o site declara e quantas dessas linhas repetem um item já listado.

    O total declarado conta linhas do índice do site, e o índice pode trazer o mesmo item duas vezes (em 2000 o
    data-guid 978188400, E-2.262/00, é a 12ª e a 13ª linha). Linha repetida só é aceita como tal quando todas as
    linhas declaradas foram lidas; se faltar linha, o ano é relido.
    """
    itens: dict[str, dict[str, Any]] = {}
    total: int | None = None
    linhas = 0
    for _ in range(3):
        primeira = await _pagina(client, sem, ano, 0)
        total = total_declarado(primeira)
        paginas = [primeira]
        if total is not None and total > POR_PAGINA:
            paginas += await asyncio.gather(*(_pagina(client, sem, ano, s, total) for s in range(POR_PAGINA, total, POR_PAGINA)))
        # Sem total declarado (ou se a última página ainda oferecer "mais"), segue até o site dizer que acabou.
        start = len(paginas) * POR_PAGINA
        while paginas[-1].get("btnMore") in (1, "1") and not paginas[-1].get("err") and start < 20000:
            paginas.append(await _pagina(client, sem, ano, start))
            start += POR_PAGINA
        itens, linhas = {}, 0
        for corpo in paginas:
            da_pagina = itens_da_pagina(corpo, ano)
            linhas += len(da_pagina)
            for it in da_pagina:
                itens.setdefault(it["id"], it)
        if total is None or linhas == total:
            return list(itens.values()), total, linhas - len(itens)
        _primeiras.pop(ano, None)  # faltou linha: relê o ano inteiro
        await asyncio.sleep(2.0)
    return list(itens.values()), total, 0  # incompleto: sem desconto, para o livro não fechar


async def _listar_tudo(client: Any, sem: asyncio.Semaphore) -> dict[str, Any]:
    """Listagem completa, feita uma vez e reaproveitada por esperado() e listar()."""
    if _listagem:
        return _listagem
    anos = await _listar_anos(client, sem)
    itens: dict[str, dict[str, Any]] = {}
    declarado: int | None = 0
    repetidas = 0
    for ano in anos:  # um ano de cada vez; as páginas do ano vão em paralelo, dentro do semáforo
        do_ano, total, rep = await _listar_ano(client, sem, ano)
        declarado = None if (declarado is None or total is None) else declarado + total
        repetidas += rep
        for it in do_ano:
            if it["id"] in itens:  # mesmo guid etiquetado em dois anos: guarda as duas etiquetas, não desconta do esperado
                itens[it["id"]].setdefault("outras_etiquetas", []).append(it["ano_etiqueta"])
            else:
                itens[it["id"]] = it
    _listagem.update({"itens": list(itens.values()), "declarado": declarado, "linhas_repetidas": repetidas})
    return _listagem


# ---------------------------------------------------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------------------------------------------------

def _limpar_linha(s: str) -> str:
    return re.sub(r"[ \t\r\f\v ​]+", " ", s).strip()


def _sem_entidades(s: str) -> str:
    """Desfaz entidades HTML, inclusive as escapadas duas vezes ('&amp;#8211;') que o site traz em itens migrados."""
    for _ in range(2):
        if not re.search(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,8});", s):
            break
        s = html_lib.unescape(s)
    return s


def html_para_texto(h: str) -> str:
    """HTML do detalhe -> texto com um parágrafo por linha."""
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", h or "")
    h = re.sub(r"(?s)<!--.*?-->", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|tr|blockquote|table|ul|ol)>", "\n", h)
    h = _sem_entidades(re.sub(r"<[^>]+>", "", h))
    linhas = [_limpar_linha(ln) for ln in h.split("\n")]
    return "\n".join(ln for ln in linhas if ln)


def _so_alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def normalizar_numero(titulo: str) -> str:
    """'E - 1.201' -> 'E-1.201'; 'E- 2.465/2001' -> 'E-2.465/2001'; 'E-l.O61' (erro de digitação do site) -> 'E-1.061'."""
    t = _limpar_linha(titulo).rstrip(". ").strip()
    t = re.sub(r"(?i)^procs?\.?\s*(?=E\b|\d)", "", t)
    t = re.sub(r"^E\s*[-–—.]?\s*(?=[\dlIO])", "E-", t, flags=re.I)
    t = re.sub(r"\s*/\s*", "/", t)
    if re.fullmatch(r"E-[\dlIO.]+(?:/\d{2,4})?", t):  # 'l' por '1' e 'O' por '0' na numeração antiga
        t = "E-" + t[2:].replace("l", "1").replace("I", "1").replace("O", "0")
    return t


RE_NUMERO_NO_TEXTO = re.compile(r"(?i)\bproc\.?\s*((?:E\s*[-–—.]?\s*)?[\dlO][\dlO.]*(?:\s*/\s*\d{2,4})?)")


RE_PROC_LINHA = re.compile(r"(?i)\bproc(?:esso)?s?\b\.?\s*(?:n[º°o]?\.?\s*)?(?:E\s*[-–—.]?\s*)?\d")
RE_VOTACAO = re.compile(r"(?i)\bv\.\s*[um]\b|\bunanim|\bmaioria\b|\bRel(?:\.|ator|atora|ª)|\bparecer e ementa\b")
RE_PROC_UNIFICADO = re.compile(r"\b(\d{2}\.\d{4}\.\d{4}\.\d{6}-\d)\b")
RE_DATA_NUM = re.compile(r"(?<![\d/.])(\d{1,2})\s*[/.]\s*(\d{1,2})\s*[/.]\s*(\d{4}|\d{2})(?![\d/])")
MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RE_DATA_EXT = re.compile(r"(?i)(\d{1,2})\s*(?:º|°)?\s*de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})")
RE_SECAO_RELATORIO = re.compile(r"(?i)^\W{0,3}(?:consulta\s+e\s+relat[óo]rio|relat[óo]rio(?:\s+e\s+(?:voto|parecer))?|s[íi]ntese\s+da\s+consulta|da\s+consulta|consulta)\b\W{0,3}(?:$|[-–—:.])")
RE_SECAO_PARECER = re.compile(r"(?i)^\W{0,3}(?:parecer(?:\s+e\s+voto)?|voto(?:\s+e\s+parecer)?|voto\s+do\s+relator|fundamenta[çc][ãa]o|an[áa]lise)\b\W{0,3}(?:$|[-–—:.])")
RE_TITULO_PESSOA = re.compile(r"(?i)^(?:(?:o|a|os|as|do|da)\s+)?(?:(?:il(?:ustre|mo|ma)\.?|em(?:inente|\.)|exm[oa]\.?|sr\.?|sra\.?|"
                              r"d(?:r|ra|outor|outora)\.?\s*[ªa]?\.?|prof(?:essor|essora|\.)?\s*[ªa]?\.?|des(?:embargador|\.)?|conselheir[oa])\s*)+")


def _data_iso(dia: int, mes: int, ano: int) -> str | None:
    if ano < 100:
        ano += 1900 if ano >= 90 else 2000
    if not (1 <= dia <= 31 and 1 <= mes <= 12 and 1990 <= ano <= 2100):
        return None
    return f"{ano:04d}-{mes:02d}-{dia:02d}"


def extrair_data(linha: str) -> str | None:
    """Data do julgamento na linha de votação: 'v.u., em 20/07/2023', 'v.u. em 21/02/02', 'em 16 de março de 2000'."""
    achados: list[tuple[int, str]] = []
    for m in RE_DATA_NUM.finditer(linha):
        iso = _data_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            achados.append((m.start(), iso))
    for m in RE_DATA_EXT.finditer(linha):
        iso = _data_iso(int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3)))
        if iso:
            achados.append((m.start(), iso))
    return min(achados)[1] if achados else None


def _nome(trecho: str) -> str | None:
    nome = _limpar_linha(trecho).strip(" -–—,.;:")
    for _ in range(3):
        nome = RE_TITULO_PESSOA.sub("", nome).strip(" -–—,.;:ªº")
    nome = re.split(r"(?i)\s+[-–—]\s+|,|;|\s+com\s+|\s+e\s+(?:do|da)\s+|\s+ac(?:ompanhad|rescid)|\s+tendo\s+|\s+vencid|\s+que\s+|\(|\s+contra\s+|\s*\be\s+(?:complementa|ementa|voto|pel[oa]|[oa]\s+julgador)|\s+aderiu|\s*-\s*rev|\s+\?", nome)[0]
    nome = _limpar_linha(nome).strip(" -–—,.;:")
    if len(nome) < 5 or len(nome) > 80 or not re.search(r"[A-Za-zÀ-ÿ]{2,}\s+[A-Za-zÀ-ÿ]{2,}", nome):
        return None
    return nome.upper()


RE_REL = re.compile(r"(?i)\bRel(?:atora|ator|\.)?\s*ª?\.?\s*(?:designad[oa]\s*)?(?:com\s+voto\s+vencid[oa]\s*)?[:\-–—]?\s*(.+)")
RE_REV = re.compile(r"(?i)\bRev(?:isora|isor|\.)?\s*ª?\.?\s*[:\-–—]?\s*(.+)")
RE_CORTE_PAPEL = re.compile(r"(?i)\s*[-–—,;.]?\s*\b(?:Rev(?:isora|isor|\.)|Pres(?:idente|\.)|Rel(?:atora|ator|\.))")


def extrair_relator_revisor(linha: str) -> tuple[str | None, str | None]:
    relator = revisor = None
    m = RE_REL.search(linha)
    if m:
        relator = _nome(RE_CORTE_PAPEL.split(m.group(1), maxsplit=1)[0])
    m = RE_REV.search(linha)
    if m:
        revisor = _nome(RE_CORTE_PAPEL.split(m.group(1), maxsplit=1)[0])
    return relator, revisor


def estruturar(texto: str, titulo: str, descricao: str = "", ano: int | None = None) -> dict[str, Any]:
    """Separa o texto do detalhe em ementa, linha de votação, relatório e parecer, e tira número, data e nomes.

    ``descricao`` é o título da ementa na listagem; só é passado para os itens do editor novo (2024 em diante), em
    que às vezes o corpo do detalhe começa direto no texto, sem o título.
    """
    numero = normalizar_numero(titulo)
    linhas = [_limpar_linha(ln) for ln in _sem_entidades(texto or "").split("\n") if ln.strip()]
    if not re.match(r"(?i)^(?:E-|D\s*-|\d)", numero):
        # título que não é número (lixo da migração do site, ex.: 'São Paulo, 20 de'): usa o 'Proc. ...' do texto, se houver
        m_num = next((m for m in (RE_NUMERO_NO_TEXTO.search(ln) for ln in linhas[:4]) if m), None) or RE_NUMERO_NO_TEXTO.search(texto or "")
        if m_num:
            numero = normalizar_numero(m_num.group(1))
    chave_titulo = _so_alnum(numero)
    if linhas and chave_titulo and _so_alnum(linhas[0]) == chave_titulo:
        linhas = linhas[1:]  # o detalhe antigo repete o número como cabeçalho

    # A linha de votação fecha a ementa: prefere a que cita o número da própria consulta.
    digitos = re.sub(r"\D", "", numero.split("/")[0])
    candidatas = [i for i, ln in enumerate(linhas) if RE_PROC_LINHA.search(ln) and RE_VOTACAO.search(ln[RE_PROC_LINHA.search(ln).start():])]
    i_proc = None
    pos_proc = 0      # onde, na linha, começa o 'Proc.' que vale
    propria = False   # a linha de votação cita o número desta consulta?
    for i in candidatas:
        for m_p in RE_PROC_LINHA.finditer(linhas[i]):  # a linha pode citar um precedente antes ('Proc. E-2.443/01 ... Proc. E-2.845/03 ...')
            if digitos and digitos in re.sub(r"\D", "", linhas[i][m_p.start():m_p.start() + 40]):
                i_proc, pos_proc, propria = i, m_p.start(), True
                break
        if propria:
            break
    if i_proc is None and candidatas:
        i_proc = candidatas[0]
        pos_proc = RE_PROC_LINHA.search(linhas[i_proc]).start()

    i_secao = next((i for i, ln in enumerate(linhas) if i > 0 and (RE_SECAO_RELATORIO.match(ln) or RE_SECAO_PARECER.match(ln))), None)
    if i_proc is not None and i_secao is not None and i_secao <= i_proc:
        # linha de votação depois do começo do relatório é citação de precedente, não o fecho da ementa
        i_proc = None
    if i_proc is not None:
        fim_ementa = i_proc + 1
        while fim_ementa < len(linhas) and len(linhas[fim_ementa]) < 200 and re.match(r"(?i)^\W*(Rev|Pres|Rel)", linhas[fim_ementa]) \
                and not re.search(r"(?i)pres", " ".join(linhas[i_proc:fim_ementa])):
            fim_ementa += 1
    elif i_secao is not None:
        fim_ementa = i_secao
    else:
        fim_ementa = len(linhas)

    ementa = " ".join(linhas[:fim_ementa]).strip()
    if descricao:
        d, e = _so_alnum(descricao), _so_alnum(ementa[:6000])
        if len(d) >= 12 and d[:40] not in e and d[1:41] not in e:
            ementa = f"{_limpar_linha(descricao).rstrip('. ')}. {ementa}"
    corpo = linhas[fim_ementa:]
    votacao = ""
    if i_proc is not None:
        votacao = " ".join(linhas[i_proc:fim_ementa])[pos_proc:]

    i_par = next((i for i, ln in enumerate(corpo) if RE_SECAO_PARECER.match(ln)), None)
    if i_par is None:
        relatorio, parecer = "", "\n".join(corpo)
        if corpo and RE_SECAO_RELATORIO.match(corpo[0]):
            relatorio, parecer = parecer, ""
    else:
        relatorio, parecer = "\n".join(corpo[:i_par]), "\n".join(corpo[i_par:])

    m_proc = RE_PROC_UNIFICADO.search(votacao) or RE_PROC_UNIFICADO.search(ementa)
    relator, revisor = extrair_relator_revisor(votacao) if votacao else (None, None)
    data = extrair_data(votacao) if votacao else None
    if votacao and not propria and data and ano and abs(int(data[:4]) - int(ano)) > 1:
        # A ementa fecha com a linha de votação de OUTRA consulta, de anos antes (o site repete a ementa do precedente):
        # nem a data nem o relator dessa linha são desta consulta.
        data = relator = revisor = None
    return {
        "numero": numero,
        "numero_processo": m_proc.group(1) if m_proc else numero,
        "ementa": ementa,
        "votacao": votacao,
        "relatorio": relatorio,
        "parecer": parecer,
        "relator": relator,
        "revisor": revisor,
        "data_julgamento": data,
        "votacao_propria": propria,
    }


RE_SESSAO = re.compile(r"(?i)^\W*(?:\d+\s*[ªa]\.?\s*)?SESS[ÃA]O\s+DE\s+(\d{1,2})\s*[º°]?\s+DE\s+(" + "|".join(MESES) + r")\s+DE\s+(\d{4})\W*$")
RE_PROC_INICIO = re.compile(r"(?i)^\W*Proc(?:esso)?s?\.?\s*(?:n[º°o]?\.?\s*)?(?:E\s*[-–—.]?\s*)?([\dlO][\dlO.]*)")


def chave_numero(numero: str) -> str | None:
    """'E-2.465/2001' -> '2465'. Só para a numeração antiga (E-x.xxx); a de 2024 em diante não está na coletânea."""
    base = numero.split("/")[0]
    if re.search(r"\d-\d", base) or not re.match(r"(?i)^(?:E-|\d)", base):
        return None
    m = re.search(r"\d[\d.]*", base)  # em 'E-1.187 e E-1.188' vale o primeiro
    digitos = re.sub(r"\D", "", m.group(0)).lstrip("0") if m else ""
    return digitos or None


def datas_da_coletanea(texto: str) -> dict[str, list[str]]:
    """Texto da página de um ano da coletânea -> {dígitos do número da consulta: [datas das sessões em que aparece]}."""
    datas: dict[str, list[str]] = {}
    sessao: str | None = None
    for linha in texto.split("\n"):
        m = RE_SESSAO.match(linha)
        if m:
            sessao = _data_iso(int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3)))
            continue
        m = RE_PROC_INICIO.match(linha)
        if m and sessao:
            chave = re.sub(r"\D", "", m.group(1).replace("l", "1").replace("O", "0")).lstrip("0")
            if chave and sessao not in datas.setdefault(chave, []):
                datas[chave].append(sessao)
    return datas


def _editor_novo(ano: Any) -> bool:
    return bool(ano) and int(ano) >= 2024


def detalhe_com_erro(texto: str) -> bool:
    """Sob carga o site responde 200 com 'Ocorreu um erro, feche a janela e tente novamente.' no lugar do item."""
    return len(texto) < 200 and "ocorreu um erro" in texto.lower()


def url_publica(slug: str) -> str:
    """Endereço que abre o item na página do ementário (mesmo formato de âncora que o próprio site usa)."""
    return f"{URL_PAGINA}#modal={slug}%5b%21%5d{BRACKET}" if slug else URL_PAGINA


# ---------------------------------------------------------------------------------------------------------------------
# Contrato do adaptador
# ---------------------------------------------------------------------------------------------------------------------

async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    """Soma dos totais que o site declara por ano, menos as linhas em que o próprio índice do site repete um item.

    Em 18/11/2026: 5.541 linhas declaradas, 1 repetida (ano 2000), 5.540 itens distintos.
    """
    tudo = await _listar_tudo(client, sem)
    if tudo["declarado"] is None:
        return None
    return tudo["declarado"] - tudo["linhas_repetidas"]


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return list((await _listar_tudo(client, sem))["itens"])


def _guid_do_detalhe(corpo: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    posts = (corpo.get("post") or {}).get("posts") if isinstance(corpo.get("post"), dict) else None
    post = posts[0] if posts and isinstance(posts[0], dict) else {}
    return (str(post["guid"]) if post.get("guid") else None), post


async def _detalhe(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> tuple[dict[str, Any], str, bool] | None:
    """Detalhe do item: (resposta, texto, o link pelo slug abre o item certo?).

    O site acha o item pelo slug, e o slug não é único: para algumas dezenas de itens (número repetido no ano, ou
    slug 'AA-01-01-0000-...') ele devolve OUTRO item, às vezes uma notícia. Por isso o guid devolvido é conferido, e,
    se não bater, o pedido é refeito com o parâmetro ``guid`` (previsto no scripts.js do próprio site), que acerta
    o item mas é bem mais lento. Sob carga o site também responde 200 com uma página de erro: insiste.
    """
    dados = {"bracket": BRACKET, "slug": item["slug"]}
    for n in range(3):
        try:
            corpo = await _post(client, sem, URL_DETALHE, dados, tentativas=2)
        except RuntimeError:
            break  # o pedido pelo slug nem responde (acontece em item do editor novo): vai direto pelo guid
        guid, _ = _guid_do_detalhe(corpo)
        texto = html_para_texto(corpo.get("html") or "")
        if texto and not detalhe_com_erro(texto):
            if guid == str(item["id"]):
                return corpo, texto, True
            break  # veio outro item: não adianta repetir, só o guid resolve
        await asyncio.sleep(3.0 * (n + 1))
    for n in range(3):
        try:
            corpo = await _post(client, sem, URL_DETALHE, {**dados, "guid": str(item["id"])}, tentativas=2, timeout=150.0)
        except RuntimeError:
            corpo = {}
        guid, _ = _guid_do_detalhe(corpo)
        texto = html_para_texto(corpo.get("html") or "")
        if texto and not detalhe_com_erro(texto) and guid == str(item["id"]):
            return corpo, texto, False
        await asyncio.sleep(5.0 * (n + 1))
    return None


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    achado = await _detalhe(client, sem, item)
    if not achado:
        return None
    corpo, texto, link_ok = achado
    _, post = _guid_do_detalhe(corpo)
    titulo = _limpar_linha(corpo.get("title") or item.get("titulo") or "")
    ano = item.get("ano_etiqueta")
    descricao = item.get("descricao") or _limpar_linha(post.get("description") or "")
    partes = estruturar(texto, titulo, descricao if _editor_novo(ano) else "", ano)
    data_sessao = None
    chave = chave_numero(partes["numero"])
    if not partes["data_julgamento"] and chave and ano:
        for a in (ano, ano + 1, ano - 1):  # a etiqueta do site nem sempre é o ano da sessão
            achadas = (await _datas_coletanea(client, sem, a)).get(chave) or []
            if len(achadas) == 1:  # número em duas sessões diferentes: não dá para saber qual é esta ementa
                data_sessao = achadas[0]
                break
            if achadas:
                break
    return {
        "id": item["id"],
        "slug": item["slug"],
        "titulo": titulo,
        "descricao": descricao,
        "ano_etiqueta": item.get("ano_etiqueta"),
        "orgao": (post.get("cards") or [CARD])[0] if isinstance(post.get("cards"), list) else CARD,
        "publicado_no_site": post.get("humanpostdate") or "",
        # onde o slug abre outro item, o link com âncora enganaria: fica a página do ementário
        "url": url_publica(item["slug"]) if link_ok else URL_PAGINA,
        "link_direto": link_ok,
        "texto": texto,
        **partes,
        "data_sessao_coletanea": data_sessao,
        "data_origem": "texto" if partes["data_julgamento"] else ("coletanea" if data_sessao else "ano-etiqueta"),
    }


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = (item.get("texto") or "").strip()
    if not texto or detalhe_com_erro(texto):
        return None
    ano = item.get("ano_etiqueta")
    # refaz a partir do texto bruto: ajuste de regra não exige rebaixar
    partes = estruturar(texto, item.get("titulo") or "", (item.get("descricao") or "") if _editor_novo(ano) else "", ano)
    if len(partes["ementa"]) < 20:
        return None
    data = partes["data_julgamento"] or item.get("data_sessao_coletanea") or (f"{int(ano):04d}-01-01" if ano else None)
    tem_teor = bool(partes["relatorio"] or partes["parecer"])
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=partes["ementa"],
        url=item.get("url") or url_publica(item.get("slug") or ""),
        orgao_raw=item.get("orgao") or CARD,
        sigla_orgao=SIGLA_ORGAO,
        orgao_norm=ORGAO_NORM,
        data_decisao=data,
        inteiro_teor=_sem_entidades(texto) if tem_teor else "",
        numero_processo=partes["numero_processo"] or None,
        numero_ementa=partes["numero"] or None,
        relator=partes["relator"],
        consulta=True,
        publicacao="Site da OAB/SP - Ementário da Turma de Ética Profissional",
    )
    if not partes["relator"]:
        rec["relator_principal"] = None  # sem linha de votação não há relator confiável; não adivinhar pelo texto
    return rec
