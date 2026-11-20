"""OAB/RO - Ementário do TED 2019-2023 (um único PDF de 180 páginas, publicado em 09/09/2024).

https://www.oab-ro.org.br/ementarios/ementario-do-tribunal-de-etica-e-disciplina/ aponta para
https://www.oab-ro.org.br/gerenciador/data/uploads/2024/09/Ementario-OAB.pdf (texto nativo; baixado uma vez, ~43 MB, e lido com
``pdftotext -layout``). Não se usa /gerenciador/data/uploads/json/ (robots.txt).

Estrutura do documento: ementa (cabeçalho em maiúsculas + itens numerados, o último é o dispositivo) seguida de um rodapé de
citação ``(Proc. 22.0000.AAAA.NNNNNN-D/TED/OAB/RO. Julgado em ..., parecer e ementa do Rel. ... (DEOAB, a. IV, n. ..., p. ...)``.
Os rodapés variam (``Proc.``, ``Processo nº:``, ``Consulta n°:``, parênteses sem fechar, quebra de página no meio). Cada rodapé
fecha uma entrada; o cabeçalho de tema (``LOCUPLETAMENTO``) que antecede a primeira ementa do tema vai em ``tema``, fora do texto.
Cabeçalho corrido da página e número de página são descartados; o resto é o texto do PDF, só com as quebras de linha viradas
em espaço (ligaturas ``fi``/``fl`` gravadas como caracteres de uso privado voltam a ser ``fi``/``fl``).

O site não declara total. O ``esperado`` é a contagem independente das citações ``Proc.``/``Processo nº:``/``Consulta n°:``
seguidas de número 22.0000 no texto corrido; ``listar`` parte as entradas pelos rodapés e as duas contagens têm de bater.
Id: ``Ementario-OAB.pdf#<n>``.

Só ementa + dispositivo (sem relatório e voto). Sobreposição: o PDF cita o próprio DEOAB e ~metade dos processos está em DEOAB_RO.
``normalizar`` descarta o item que tem o mesmo processo (só dígitos; o Diário traz espaços espúrios) E cuja ementa normalizada
(sem acento, só letras, 150 primeiras) aparece no texto do registro do Diário; os demais carregam.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import DB_PATH, RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "RO", "ementario-pdf"
PDF_URL = "https://www.oab-ro.org.br/gerenciador/data/uploads/2024/09/Ementario-OAB.pdf"
ARQUIVO = PDF_URL.rsplit("/", 1)[1]
SIGLA = "OABRO-TED"
ORGAO_GENERICO = "Tribunal de Ética e Disciplina da OAB/RO"
MOTIVOS: dict[str, str] = {}

RE_CABECALHO = re.compile(r"^\s*Ementário Tribunal de ética e disciplina OAB/RO")
RE_NUM_PAGINA = re.compile(r"^\s*\d{1,3}\s*$")
RE_RODAPE = re.compile(r"(?m)^[ \t]*\(?[ \t]*(?:Proc\.|Processo nº:|Consulta n°:)")
RE_CITACAO = re.compile(r"(?:Proc\.|Processo nº:|Consulta n°:)\s*2{1,2}\.0000")  # contagem independente do texto corrido
RE_PROCESSO = re.compile(r"(2{1,2}\.0000\.\s*\d{4}\.\s*\d{5,7}-\d)")  # como publicado (há erros de digitação no PDF)
RE_TURMA = re.compile(r"(\d)\s*ª\s*Turma", re.I)
RE_ABERTURA_TEMA = re.compile(r"^[^a-zà-ÿ]{3,80}$")  # só maiúsculas/símbolos, curto

LIGATURAS = {"": "fi", "": "fl", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬀ": "ff"}

_cache: dict[str, Any] = {}


def texto_do_pdf(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, timeout=300).stdout.decode("utf-8", "replace")


def contar_citacoes(texto: str) -> int:
    return len(RE_CITACAO.findall(texto))


def limpar_paginas(bruto: str) -> str:
    """Tira cabeçalho corrido e número de página; as páginas ficam separadas por uma linha com ``\\f``."""
    paginas = []
    for p in bruto.split("\f"):
        ls = [ln.rstrip() for ln in p.split("\n") if not RE_CABECALHO.match(ln) and not RE_NUM_PAGINA.match(ln)]
        while ls and not ls[0].strip():
            ls.pop(0)
        while ls and not ls[-1].strip():
            ls.pop()
        paginas.append("\n".join(ls))
    return "\n\f\n".join(paginas)


def _fim_rodape(txt: str, ini: int, lim: int) -> int:
    seg = txt[ini:lim]
    d = seg.find("DEOAB")
    if 0 <= d < 700:
        c = seg.find(")", d)
        if c >= 0:
            fim = ini + c + 1
            return fim + 1 if txt[fim : fim + 1] == "." else fim
    k = seg.find("(")
    if k >= 0 and not seg[:k].strip():  # '(Proc. ... )' sem DEOAB: o parêntese de abertura fecha
        prof = 0
        for j, ch in enumerate(seg[:900]):
            prof += (ch == "(") - (ch == ")")
            if prof == 0 and j >= k:
                fim = ini + j + 1
                return fim + 1 if txt[fim : fim + 1] == "." else fim
    e = re.search(r"\n[ \t]*\n|\n\f\n", seg)  # rodapé truncado no original: vai até o fim do parágrafo
    return ini + (e.start() if e else len(seg))


def _paragrafos(trecho: str) -> list[str]:
    linhas = trecho.split("\n")
    paras: list[list[str]] = []
    cur: list[str] = []
    for k, ln in enumerate(linhas):
        if ln.strip() == "\f":  # quebra de página: só separa parágrafo se a frase fechou e a seguinte começa em maiúscula
            prev = cur[-1].rstrip() if cur else ""
            prox = next((x.strip() for x in linhas[k + 1 :] if x.strip() and x.strip() != "\f"), "")
            if prev.endswith((".", ")", ":")) and prox[:1].isupper():
                paras.append(cur)
                cur = []
            continue
        if not ln.strip():
            if cur:
                paras.append(cur)
                cur = []
        else:
            cur.append(ln.strip())
    if cur:
        paras.append(cur)
    return [" ".join(p) for p in paras]


def _ligaduras(t: str) -> str:
    for k, v in LIGATURAS.items():
        t = t.replace(k, v)
    return t


def indice(bruto: str) -> str:
    """Letras do índice temático (da palavra 'Índice' à 'APRESENTAÇÃO'), para reconhecer o cabeçalho de tema no corpo."""
    a = bruto.find("Índice")
    b = bruto.find("APRESENTAÇÃO", a)
    if a < 0 or b < 0:
        raise ValueError("PDF fora do padrão: sem índice temático")
    return sem_acento_letras(bruto[a:b])


def _eh_tema(p: str, idx: str) -> bool:
    """Cabeçalho de tema: parágrafo curto, em maiúsculas, sem ponto final, que consta do índice."""
    letras = [c for c in p if c.isalpha()]
    return (bool(letras) and len(p) <= 80 and RE_ABERTURA_TEMA.match(p) is not None and not p.rstrip().endswith((".", ",", ":", ";"))
            and len(sem_acento_letras(p)) >= 5 and sem_acento_letras(p) in idx)


EXT = {"um": 1, "dois": 2, "três": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
       "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19,
       "vinte": 20, "trinta": 30}
MES_NOME = {"janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
            "novembro": 11, "dezembro": 12}


def _iso(d: str, m: str, a: str) -> str | None:
    d, m, a = int(d), int(m), int(a)
    return f"{a:04d}-{m:02d}-{d:02d}" if 2015 <= a <= 2024 and 1 <= m <= 12 and 1 <= d <= 31 else None


def data_do_rodape(rod: str) -> str | None:
    """Data do julgamento dita no rodapé: 'Julgado em dd/mm/aaaa', 'Porto-Velho-RO, dd/mm/aaaa', 'Data julgamento: dd.mm.aaaa'
    ou por extenso ('aos vinte e nove dias do mês de maio do ano de dois mil e vinte'). A data do DEOAB (publicação) não conta."""
    for rx in (r"Julgado\s+em\s+(\d{1,2})[/.](\d{1,2})[/.](\d{4})", r"Data\s+julgamento:\s*(\d{1,2})[/.](\d{1,2})[/.](\d{4})",
               r"Porto[\s-]*Velho[\s-]*RO,?\s*(\d{1,2})[/.](\d{1,2})[/.](\d{4})"):
        m = re.search(rx, rod, re.I)
        if m:
            return _iso(m.group(1), m.group(2), m.group(3))
    m = re.search(r"Julgado\s+aos\s+(.+?)\s+dias\s+do\s+m[êe]s\s+de\s+(\w+)\s+do\s+ano\s+de\s+dois\s+mil\s*(?:e\s+)?(\w+)?", rod, re.I)
    if m and m.group(2).lower() in MES_NOME:
        dia = sum(EXT.get(w, 0) for w in re.split(r"\s+e\s+|\s+", m.group(1).lower()) if w != "e")
        ano = 2000 + (EXT.get((m.group(3) or "").lower(), 0))
        return _iso(str(dia), str(MES_NOME[m.group(2).lower()]), str(ano))
    return None


RE_RELATOR = re.compile(
    r"Rel(?:ator|atora|\.)?\s*(?:\(a\))?\s*:?\s*(?:Dr[a]?\.?\s*)*(.+?)"
    r"(?=\s*(?:[-–—]\s|–|\bOAB/RO|\bOAB\b|,|\.\s+(?:Relator|Presidente|\()|\.\s*$|\s+d[ao]\s+\d|\s+d[ao]\s+Conselho|\s+Relator|\s+-\s*Relator|\(|$))",
    re.S,
)


def relator_do_rodape(rod: str) -> str | None:
    m = RE_RELATOR.search(rod) or re.search(r"parecer\s+e\s+ementa\s+d[oa]\s+(?:Dr[a]?\.?\s*)?([A-ZÀ-Ý][^,.(–]{4,70}?)\s*(?:,|–|OAB|\()", rod)
    if not m:
        return None
    nome = re.sub(r"\s+", " ", m.group(1)).strip(" .,-–")
    return nome if len(nome.split()) >= 2 else None


def orgao_do_rodape(rod: str) -> tuple[str, str, str]:
    """(texto do órgão como o rodapé informa, sigla, nome normalizado): nª Turma, Conselho Pleno ou o TED em geral."""
    m = RE_TURMA.search(rod)
    if m:
        return f"{m.group(1)}ª Turma do TED/OAB/RO", f"{SIGLA}-T{m.group(1)}", f"{ORGAO_GENERICO}/{m.group(1)}ª Turma"
    if re.search(r"Pleno", rod, re.I):
        return "Conselho Pleno do TED/OAB/RO", f"{SIGLA}-PLENO", f"{ORGAO_GENERICO}/Pleno"
    return "TED/OAB/RO", SIGLA, ORGAO_GENERICO


def entradas(bruto: str) -> list[dict[str, Any]]:
    idx = indice(bruto)
    txt = limpar_paginas(bruto)
    ini = [m for m in RE_RODAPE.finditer(txt) if not txt[m.start() : m.start() + 30].lstrip().startswith("(Processo de")]
    saida: list[dict[str, Any]] = []
    anterior, tema = 0, None
    for i, m in enumerate(ini):
        lim = ini[i + 1].start() if i + 1 < len(ini) else len(txt)
        fim = _fim_rodape(txt, m.start(), lim)
        paras = _paragrafos(txt[anterior : m.start()])
        if i == 0:  # a primeira entrada vem depois do manual e das súmulas: começa depois do último cabeçalho de tema
            ult = max((k for k, p in enumerate(paras) if _eh_tema(p, idx)), default=-1)
            tema, paras = (paras[ult], paras[ult + 1 :]) if ult >= 0 else (None, paras)
        else:
            while len(paras) > 1 and _eh_tema(paras[0], idx):
                tema, paras = paras[0], paras[1:]
        rodape = " ".join(x.strip() for x in txt[m.start() : fim].split("\n") if x.strip() and x.strip() != "\f")
        ementa = _ligaduras("\n\n".join(paras)).strip()
        rodape = _ligaduras(rodape)
        proc = RE_PROCESSO.search(rodape)
        orgao, sigla, norm = orgao_do_rodape(rodape)
        saida.append({
            "n": i + 1, "tema": _ligaduras(tema) if tema else None, "ementa": ementa, "rodape": rodape,
            "processo": re.sub(r"\s+", "", proc.group(1)) if proc else None, "relator": relator_do_rodape(rodape), "data": data_do_rodape(rodape),
            "orgao": orgao, "sigla": sigla, "orgao_norm": norm, "consulta": rodape.lstrip("( ").startswith("Consulta") or (tema or "").strip().upper() == "CONSULTAS",
        })
        anterior = fim
        # texto que sobra depois do rodapé até o próximo rodapé é a entrada seguinte (ou cabeçalho de tema), tratado no próximo passo
    return saida


async def _pdf(client: Any, sem: asyncio.Semaphore) -> str:
    if "texto" not in _cache:
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / ARQUIVO
        if not arq.exists() or arq.stat().st_size < 100000:
            arq.write_bytes(await ce.baixar_bytes(client, sem, PDF_URL, marca=b"%PDF"))
        _cache["texto"] = texto_do_pdf(arq)
    return _cache["texto"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return contar_citacoes(await _pdf(client, sem))


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return [{"id": f"{ARQUIVO}#{e['n']}", **e} for e in entradas(await _pdf(client, sem))]


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return {**item, "pdf_url": PDF_URL}  # o item já é a entrada inteira, extraída do PDF único


# ---------------------------------------------------------------------------------------------------------------------
# Sobreposição com o Diário
# ---------------------------------------------------------------------------------------------------------------------

def sem_acento_letras(t: str, n: int | None = None) -> str:
    """Sem acento, só letras minúsculas. ``fi``/``fl``/``ff`` somem dos dois lados da comparação: o PDF perde essas ligaturas
    em algumas fontes ('prossional') e o Diário não."""
    t = unicodedata.normalize("NFD", t.lower())
    t = re.sub(r"[^a-z]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))
    t = re.sub(r"f[ilf]", "", t)
    return t[:n] if n else t


def digitos(numero: str | None) -> str | None:
    d = re.sub(r"\D", "", numero or "")
    return d[-16:] if len(d) >= 16 else (d or None)  # '22.0000.AAAA.NNNNNN-D' sem o prefixo: aguenta '2.0000...' digitado errado


def carregar_diario(db_path: Path = DB_PATH) -> dict[str, list[str]]:
    """{processo (dígitos): [texto normalizado de cada registro do DEOAB_RO]}. O Diário traz espaços espúrios nos números."""
    saida: dict[str, list[str]] = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=120) as conn:
        for num, ementa, inteiro in conn.execute(
            "SELECT numero_processo, descricao_ementa, inteiro_teor_texto FROM julgados WHERE fonte = 'DEOAB_RO'"
        ):
            k = digitos(num)
            if k:
                saida.setdefault(k, []).append(sem_acento_letras(f"{ementa or ''} {inteiro or ''}"))
    return saida


_DIARIO: dict[str, list[str]] | None = None


def preparar(db_path: Path = DB_PATH) -> None:
    global _DIARIO
    _DIARIO = carregar_diario(db_path)
    MOTIVOS.clear()


def sobrepoe(numero: str | None, ementa: str, diario: dict[str, list[str]]) -> bool:
    k = digitos(numero)
    chave = sem_acento_letras(ementa, 150)
    return bool(k and chave and any(chave in t for t in diario.get(k, [])))


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    ementa = (item.get("ementa") or "").strip()
    if len(ementa) < 40:
        MOTIVOS[str(item["id"])] = "entrada sem texto de ementa"
        return None
    if _DIARIO is not None and sobrepoe(item.get("processo"), ementa, _DIARIO):
        MOTIVOS[str(item["id"])] = f"sobreposição com DEOAB_RO (mesmo processo {item.get('processo')} e mesma ementa)"
        return None
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa,
        acordao=item.get("rodape") or "",
        url=PDF_URL,
        orgao_raw=item.get("orgao") or "TED/OAB/RO",
        sigla_orgao=item.get("sigla") or SIGLA,
        orgao_norm=item.get("orgao_norm") or ORGAO_GENERICO,
        data_decisao=item.get("data"),
        numero_processo=item.get("processo"),
        numero_ementa=str(item["id"]).rsplit("#", 1)[-1],
        relator=item.get("relator"),
        consulta=bool(item.get("consulta")),
        publicacao="Site da OAB/RO - Ementário do TED 2019-2023 (PDF)",
    )
    if not item.get("relator"):
        rec["relator_principal"] = None
    rec["pdf_url"] = PDF_URL
    rec["is_ted_disciplinar"] = 1  # órgão julgador: TED (turmas e Conselho Pleno do TED)
    from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(rec["inteiro_teor_texto"]) else 0
    return rec
