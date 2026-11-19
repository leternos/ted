"""OAB/SP - acórdãos das Turmas Disciplinares do TED no sistema legado (abril de 2010 a novembro de 2014).

Detalhe: ``GET https://www2.oabsp.org.br/asp/tribunal_etica/textoEmentas.asp?idEmenta=N`` (HTML em UTF-8). Id sem conteúdo
devolve só a "Nota da Corregedoria do TED". O site não declara total e a busca mensal (``ementasTribunais.asp``, POST
``proc=1&anoreq=AAAA/M``) é falha: meses 10, 11, 12 e 2014/6 vêm vazios. Por isso ``listar`` enumera por id (de 1 até
o maior id com texto mais uma sequência de ids vazios que prova o fim) e usa a busca mensal só para trazer a data dos
ids que aparecem nela (``data_listagem``).

``baixar`` devolve sempre ``vazio`` (True/False): id vazio é resposta válida do site, gravada como vazia para a conta
fechar. Resposta que não é a página esperada (erro, corte, HTTP != 200) é falha e é refeita, nunca gravada como vazia.

A data da decisão vem da linha "Sala das Sessões, 16 de setembro de 2014" do próprio acórdão (``data_origem`` = "texto");
quando ela não existe, da listagem mensal ("listagem"). A turma vem de "acordam os membros da <Nª> Turma Disciplinar".
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Any

from ted_search.fontes import montar_registro
from ted_search.parser import detect_orgao_julgador_oabsp

UF, SLUG = "SP", "legado-disciplinar"
BASE = "https://www2.oabsp.org.br/asp/tribunal_etica"
URL_DETALHE = f"{BASE}/textoEmentas.asp"
URL_LISTA = f"{BASE}/ementasTribunais.asp"
ULTIMO_ID_CONHECIDO = 5342  # medido no mapeamento; a sondagem continua além dele
VAZIOS_PARA_PARAR = 60  # ids vazios seguidos além do último com texto para dar a enumeração por encerrada
MESES_LISTAGEM = [(a, m) for a in range(2010, 2015) for m in range(1, 13)]
PAUSA = 0.15  # segundos entre requisições de cada uma das (no máximo 4) conexões
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RE_DATA_EXT = re.compile(r"(?i)(\d{1,2})\s*(?:º|°)?\s*de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})")
RE_DATA_NUM = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
RE_ITEM_LISTA = re.compile(r"<b>\s*(\d{2}/\d{2}/\d{4})\s*-\s*</b>.*?textoEmentas\.asp\?idEmenta=(\d+)", re.S)
RE_BLOCO = re.compile(r'(?is)<p\s+align="justify"[^>]*>(.*?)</p>')
RE_PROCESSO = re.compile(
    r"(?i)\bestes\s+autos\b[^,]{0,80}?(?<![A-Za-z])(?:n\s*[ºo°.]{0,2}|Disciplinar|PD|Reabilita\w+|Revis[ãa]o)\s+(?:n\s*[ºo°.]{0,2}\s+)?"
    r"(\d{1,2}[Rr]\s?\d+|\d[0-9A-Za-z./-]*)"
)
RE_PROC_PD = re.compile(r"(?i)\b(PD\s?\d{1,2}R\s?\d+(?:/\d+)?)")
RE_PROC_ACORDAM = re.compile(r"(?i)\bacordam\b")
ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50}
RE_REL_PAPEL = re.compile(r"(?i)\bRel(?:atora|ator|\.)?(?=[\s.:(\u201c\"]|$)")
RE_TITULO = re.compile(r"(?i)^(?:(?:dra?|des|profa?|sra?)\b\.?\s*)+")

_cache: dict[int, str] = {}  # HTML já lido na sondagem do teto, reaproveitado por baixar()
_listagem: dict[str, Any] = {}


# ---------------------------------------------------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------------------------------------------------

def _decodificar(corpo: bytes) -> str:
    try:
        return corpo.decode("utf-8")
    except UnicodeDecodeError:
        return corpo.decode("cp1252", "replace")  # páginas ASP antigas


def pagina_valida(h: str) -> bool:
    """A página do site, com conteúdo ou vazia: ambas trazem o rodapé 'Nota da Corregedoria do TED'."""
    return "Nota da Corregedoria" in h and "</html>" in h.lower()


async def _get(client: Any, sem: asyncio.Semaphore, url: str, params: dict[str, Any] | None = None) -> str:
    erro: Exception | None = None
    for n in range(5):
        try:
            async with sem:
                resp = await client.get(url, params=params, headers=HEADERS)
                await asyncio.sleep(PAUSA)
            resp.raise_for_status()
            h = _decodificar(resp.content)
            if pagina_valida(h):
                return h
            erro = ValueError("resposta fora do padrão do site")
        except Exception as exc:
            erro = exc
        await asyncio.sleep(2.0 * (n + 1))
    raise RuntimeError(f"falha em {url} {params}: {erro}")


async def _html_id(client: Any, sem: asyncio.Semaphore, n: int) -> str:
    if n in _cache:
        return _cache[n]
    return await _get(client, sem, URL_DETALHE, {"idEmenta": str(n)})


def _vazio(h: str) -> bool:
    return RE_BLOCO.search(h) is None


async def _datas_listagem(client: Any, sem: asyncio.Semaphore) -> dict[int, str]:
    """id -> data ISO, da busca mensal (falha em alguns meses; serve só para as datas que ela traz)."""
    datas: dict[int, str] = {}
    for ano, mes in MESES_LISTAGEM:
        erro: Exception | None = None
        for n in range(4):
            try:
                async with sem:
                    resp = await client.post(URL_LISTA, data={"proc": "1", "anoreq": f"{ano}/{mes}", "texto1": "", "texto2": ""}, headers=HEADERS)
                    await asyncio.sleep(PAUSA)
                resp.raise_for_status()
                for dd, mm, aa, i in ((*d.split("/"), i) for d, i in RE_ITEM_LISTA.findall(_decodificar(resp.content))):
                    datas.setdefault(int(i), f"{aa}-{mm}-{dd}")
                erro = None
                break
            except Exception as exc:
                erro = exc
                await asyncio.sleep(2.0 * (n + 1))
        if erro:
            raise RuntimeError(f"falha na listagem de {ano}/{mes}: {erro}")
    return datas


async def _enumerar(client: Any, sem: asyncio.Semaphore) -> dict[str, Any]:
    """Descobre o teto sondando além do último id conhecido até VAZIOS_PARA_PARAR ids vazios seguidos; faz a conta uma vez."""
    if _listagem:
        return _listagem
    ultimo = ULTIMO_ID_CONHECIDO
    prox = ULTIMO_ID_CONHECIDO + 1
    seguidos = 0
    while seguidos < VAZIOS_PARA_PARAR:
        lote = list(range(prox, prox + 20))
        paginas = await asyncio.gather(*(_get(client, sem, URL_DETALHE, {"idEmenta": str(i)}) for i in lote))
        for i, h in zip(lote, paginas):
            _cache[i] = h
            if _vazio(h):
                seguidos += 1
            else:
                ultimo, seguidos = i, 0
        prox += len(lote)
    teto = ultimo + VAZIOS_PARA_PARAR  # ids acima do último com texto, já provados vazios pela sondagem
    _listagem.update({"teto": teto, "maior_id_com_texto": ultimo, "datas": await _datas_listagem(client, sem)})
    return _listagem


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return None  # o site não declara total


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    info = await _enumerar(client, sem)
    datas = info["datas"]
    itens = []
    for n in range(1, info["teto"] + 1):
        it: dict[str, Any] = {"id": n}
        if n in datas:
            it["data_listagem"] = datas[n]
        itens.append(it)
    return itens


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    n = int(item["id"])
    h = await _html_id(client, sem, n)
    out: dict[str, Any] = {"id": n, "url": f"{URL_DETALHE}?idEmenta={n}", "vazio": _vazio(h)}
    if item.get("data_listagem"):
        out["data_listagem"] = item["data_listagem"]
    if not out["vazio"]:
        out.update(extrair(h))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------------------------------------------------

def _limpar(s: str) -> str:
    return re.sub(r"[ \t\r\f\v ​]+", " ", s).strip()


def _texto(h: str) -> str:
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = html_lib.unescape(re.sub(r"<[^>]+>", "", h))
    return "\n".join(ln for ln in (_limpar(x) for x in h.split("\n")) if ln)


def extrair_data(texto: str) -> str | None:
    """Data da sessão na linha 'Sala das Sessões, 16 de setembro de 2014' ('1o de março de 2011', '21 de fevereiro 2014')."""
    linha = next((ln for ln in reversed(texto.split("\n")) if re.search(r"(?i)sala\s+das\s+sess|^S[ãa]o\s+Paulo,", ln)), "")
    m = re.search(r"(?i)(\d{1,2})\s*(?:º|°|o|a)?\s*(?:de\s+)?(" + "|".join(MESES) + r")\s*(?:de\s+)?(\d{4})", linha)
    if m:
        return f"{int(m.group(3)):04d}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = RE_DATA_NUM.search(linha)
    return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None


def extrair_relator(texto: str) -> str | None:
    """Relator da linha de assinatura ('Rel. Dr. X - Presidente...', 'Relator e Presidente de sala Dr. X', 'Rel. (voto vista) Dr. X')."""
    linha = next((ln for ln in reversed(texto.split("\n")) if re.search(r"(?i)sala\s+das\s+sess|^S[ãa]o\s+Paulo,", ln)), "")
    m = RE_REL_PAPEL.search(linha)
    if not m:
        return None
    resto = linha[m.end():]
    resto = re.sub(r"\([^)]*\)|[\u201c\"][^\u201d\"]*[\u201d\"]", " ", resto)
    resto = re.sub(r"(?i)^[\s.:,-]*(?:divergente|origin[áa]ri[oa]|vencid[oa])\b", " ", resto)
    resto = re.sub(r"(?i)^[\s.:,-]*(?:e\s+)?Presidente(?:\s+de\s+sala)?\s*:?", " ", resto)
    resto = re.sub(r"(?i)^[\s.:,-]*(?:ad\s+hoc|designad[oa])\b[\s.:,-]*", " ", resto)
    nome = re.split(r"\s[-–—]\s|(?i:\bPresidente\b|\bRel(?:atora|ator|\.)\b)", resto)[0]
    nome = RE_TITULO.sub("", nome.strip(" -–—,.;:")).strip(" -–—,.;:")
    return nome.upper() if len(nome) >= 5 and re.search(r"[A-Za-zÀ-ÿ]{2,}\s+[A-Za-zÀ-ÿ]{2,}", nome) else None


def _romano(s: str) -> int | None:
    if not s or not re.fullmatch(r"[IVXL]{1,6}", s.upper()):
        return None
    total = 0
    v = [ROMANOS[c] for c in s.upper()]
    for i, x in enumerate(v):
        total += -x if i + 1 < len(v) and x < v[i + 1] else x
    return total if 1 <= total <= 30 else None


def extrair_turma(acordao: str) -> tuple[str | None, int | None]:
    """Turma como o acórdão a nomeia (trecho após 'acordam os membros da') e o seu número (1 a 30), se for possível dizê-lo.

    O número vem do próprio nome ('Décima Quinta Turma', 'NONA TURMA do TED', 'XXII Turma', '3a Seção da Décima Sétima
    Turma', 'Tribunal de Ética e Disciplina XI'); nunca de palavra solta no resto do texto.
    """
    plano = re.sub(r"\s+", " ", acordao)
    m = RE_PROC_ACORDAM.search(plano)
    seg = plano[m.end():m.end() + 160] if m else ""
    seg = re.sub(r"(?i)^[\s,:]*(?:os\s+)?(?:membros|integrantes|componentes)?\s*(?:d[aoe]s?)?\s*", "", seg)
    m_t = re.match(r"(?i)((?:\d+\s*a\s+Se[çc][ãa]o\s+d[ae]\s+)?([^,;]{2,45}?)\s+Turma\b(?:\s+Disciplinar)?(?:\s+d[oa]\s+(?:TED|E\.\s*TED/SP|Tribunal\s+de\s+[ÉE]tica(?:\s+e\s+Disciplina)?))?)", seg)
    m_r = re.match(r"(?i)(Tribunal\s+de\s+[ÉE]tica\s+e\s+Disciplina\s*[–-]?\s*([IVXL]{1,6})(?:\s+Turma)?)\b", seg)
    if m_r and (not m_t or m_r.end() >= m_t.end() - 6):
        return m_r.group(1).strip(" ,"), _romano(m_r.group(2))  # 'Tribunal de Ética e Disciplina – XIII Turma'
    if m_t:
        raw, pref = m_t.group(1), m_t.group(2)
        pref = re.sub(r"(?i)^.*Se[çc][ãa]o\s+d[ae]\s+", "", pref).strip()
        alvo = pref.upper()
        n = int(re.match(r"\d+", alvo).group()) if re.fullmatch(r"\d{1,2}\s*[ªºOA]?", alvo) else None
        n = n or _romano(alvo)
        if not n:
            sigla, _ = detect_orgao_julgador_oabsp("", f"{pref} Turma Disciplinar", "")
            mm = re.fullmatch(r"OABSP-(\d+)TD", sigla)
            n = int(mm.group(1)) if mm else None
        return raw.strip(" ,"), n
    if m_r:
        return m_r.group(1).strip(" ,"), _romano(m_r.group(2))
    return None, None


def campos_do_acordao(acordao: str) -> dict[str, Any]:
    m_p = RE_PROCESSO.search(acordao) or RE_PROC_PD.search(acordao)
    processo = m_p.group(1).strip(".,;") .upper() if m_p else None
    turma, n = extrair_turma(acordao)
    return {"numero_processo": processo, "turma": turma, "turma_n": n, "data_texto": extrair_data(acordao), "relator": extrair_relator(acordao)}


def extrair(h: str) -> dict[str, Any]:
    """Campos do detalhe com conteúdo: número do acórdão, ementa, texto do acórdão, processo, turma, data e relator."""
    bloco = RE_BLOCO.search(h)
    texto = _texto(bloco.group(1)) if bloco else ""
    numero = None
    ementa_linhas: list[str] = []
    resto: list[str] = []
    em_ementa = False
    for ln in texto.split("\n"):
        m_n = re.match(r"(?i)^Ac[óo]rd[ãa]o\s*N\s*[ºo°]?\s*:?\s*(.*)$", ln)
        if m_n and numero is None:
            numero = m_n.group(1).strip() or None
            continue
        if re.match(r"(?i)^EMENTA\s*:", ln) and not ementa_linhas and not resto:
            em_ementa = True
            ementa_linhas.append(re.sub(r"(?i)^EMENTA\s*:\s*", "", ln))
            continue
        if em_ementa and not resto and not re.match(r"(?i)^Vistos\b", ln):
            ementa_linhas.append(ln)  # ementa em mais de um parágrafo, até o 'Vistos, relatados...'
            continue
        resto.append(ln)
    acordao = "\n".join(resto)
    return {"numero_acordao": numero, "ementa": "\n".join(ementa_linhas).strip(), "acordao": acordao, **campos_do_acordao(acordao)}


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("vazio") or not (item.get("ementa") or item.get("acordao")):
        return None  # id sem conteúdo no site: só a Nota da Corregedoria
    turma = item.get("turma")
    n = item.get("turma_n")
    # o número da turma vem do nome que o acórdão dá a ela; sem número, o órgão é o TED em geral
    sigla, norm = detect_orgao_julgador_oabsp("", f"{n}ª Turma Disciplinar" if n else "", "")
    data = item.get("data_texto") or item.get("data_listagem")
    return montar_registro(
        UF, SLUG, item["id"],
        ementa=item.get("ementa") or "",
        acordao=item.get("acordao") or "",
        url=item["url"],
        orgao_raw=turma or "Tribunal de Ética e Disciplina",
        sigla_orgao=sigla,
        orgao_norm=norm,
        data_decisao=data,
        numero_processo=item.get("numero_processo"),
        numero_ementa=item.get("numero_acordao"),
        relator=item.get("relator"),
        consulta=False,
    )
