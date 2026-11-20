"""OAB/BA - Órgão Consultivo de Ética Profissional (OCEP) do TED: consultas em tese, 2016-2026.

https://www.oab-ba.org.br/publicacoes/ementas-consultivo é Next.js (SSG): tudo está no ``<script id="__NEXT_DATA__">`` da
própria página (``props.pageProps.institucional.TEXTO_INSTITUCIONAL``, um HTML). Não se usa /api/, /adm nem '/?*' (robots.txt).

A página traz as consultas de três jeitos, que ``listar`` junta pelo número ``NNN/AAAA``:
  * ``N) CONSULTA Nº NNN/AAAA`` + ementa inline (itens 1 a 16);
  * ``Processo Consulta nº NNN/AAAA`` com link para o PDF do processo e a linha ``ASSUNTO:`` (2016-2024);
  * ``Processo Consulta n° NNNNN/2026`` com o acórdão inline, sem PDF (2026).
Ficam de fora o que não é consulta: orientações técnicas, propostas de súmula, Súmulas 01 e 02 e o ementário disciplinar
2007-2009 que a mesma página reproduz.

O site não declara total. O ``esperado`` é a contagem independente dos números distintos de consulta citados na página
(``CONSULTA Nº`` / ``Processo Consulta``); ``listar`` monta os itens pela estrutura e as duas contagens têm de bater.

``baixar`` guarda o PDF em ``data/raw/site_ba_consultivo/pdf/`` e lê o texto: camada de texto (``pdftotext -layout``) quando
a página a tem; senão OCR (``pdftoppm -r 300`` + ``tesseract -l por``), página a página. O texto fica como saiu do PDF ou do
OCR, sem correção. Procura-se CPF em cada página (a capa do SISDOC dos PDFs escaneados traz o do consulente): o registro cujo
PDF tem CPF não é carregado até o dono liberar o id em ``data/raw/site_ba_consultivo/liberados.txt``; o texto não é alterado.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "BA", "consultivo"
SITE = "https://www.oab-ba.org.br"
URL = f"{SITE}/publicacoes/ementas-consultivo"
SIGLA = "OABBA-TED-OCEP"
ORGAO_RAW = "Órgão Consultivo de Ética Profissional (OCEP) do TED/OAB-BA"
ORGAO_NORM = "Tribunal de Ética e Disciplina da OAB/BA/Órgão Consultivo de Ética Profissional"
MOTIVOS: dict[str, str] = {}  # preenchido por normalizar: id -> motivo de não ter virado registro

MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}

RE_NUM = r"(\d{1,6})\s*[/-]\s*(\d{4})"
RE_CABECA_LISTA = re.compile(r"(?is)^\s*Processo:?\s+Consulta\b[^\d]{0,12}?" + RE_NUM)
RE_CABECA_INLINE = re.compile(r"(?is)^\s*(\d{1,2})\)\s*CONSULTA\s*N[º°o]?\.?\s*" + RE_NUM)
RE_CITADA = re.compile(r"(?i)(?:\bProcesso:?\s+Consulta|\bCONSULTA)\s*(?:N[º°o]\.?\s*)?:?\s*" + RE_NUM)
RE_FIM_LISTA = re.compile(r"(?i)^\s*(?:Ementas\s+Tribunal\s+de\s+[ÉE]tica|Exerc[ií]cio\s+\d{4}|Processo\s+Disciplinar\s+n[º°o])")
RE_BLOCO = re.compile(r"(?is)<(p|li|h[1-6])\b[^>]*>(.*?)</\1>")
RE_HREF_PDF = re.compile(r'(?i)href="([^"]+\.pdf)"')


def so_texto(h: str) -> str:
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    t = html_lib.unescape(re.sub(r"<[^>]+>", "", h)).replace("\xa0", " ")
    return "\n".join(ln for ln in (re.sub(r"[ \t\r\f\v]+", " ", x).strip() for x in t.split("\n")) if ln)


def blocos(texto_html: str) -> list[dict[str, Any]]:
    return [{"texto": so_texto(m.group(2)), "href": (RE_HREF_PDF.search(m.group(2)) or [None, None])[1]} for m in RE_BLOCO.finditer(texto_html)
            if so_texto(m.group(2))]


def chave(num: str, ano: str) -> str:
    return f"{int(num):05d}-{ano}"


def numero(id_: str) -> str:
    n, a = id_.split("-")
    return f"{n}/{a}"


def texto_institucional(pagina: str) -> str:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pagina, re.S)
    if not m:
        raise ValueError("página fora do padrão: sem __NEXT_DATA__")
    return json.loads(m.group(1))["props"]["pageProps"]["institucional"]["TEXTO_INSTITUCIONAL"]


def consultas_citadas(texto_html: str) -> set[str]:
    """Números distintos de consulta que a página apresenta (linha que ABRE com 'N) CONSULTA Nº' ou 'Processo Consulta'),
    em qualquer lugar do HTML: contagem independente de ``listar``. A citação de outra consulta no meio de uma ementa
    ('conforme a Consulta nº 236/2019') não é item da página e não conta."""
    linhas = [ln for b in blocos(texto_html) for ln in b["texto"].split("\n")]
    return {chave(*m.groups()[-2:]) for ln in linhas for m in [RE_CABECA_INLINE.match(ln) or RE_CABECA_LISTA.match(ln)] if m}


def data_do_texto(t: str) -> str | None:
    """Data da sessão/decisão dita no próprio texto: 'Sessão 08.8.2019', 'Sessão Ordinária realizada em 28/4/2026',
    'Salvador, 29 de setembro de 2023.' (a última do texto vence quando só há assinatura)."""
    for m in re.finditer(r"(?i)Sess[ãa]o[^\d\n]{0,40}?(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})", t):
        d = _iso(m.group(3), m.group(2), m.group(1))
        if d:
            return d
    for m in re.finditer(r"(?i)\bSalvador\s*,?\s*(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})", t):
        d = _iso(m.group(3), m.group(2), m.group(1))
        if d:
            return d
    ult = None
    for m in re.finditer(r"(?i)(?:Salvador|Sala\s+das\s+Sess[õo]es|Bahia)[^\n\d]{0,25}?,?\s*(\d{1,2})\s*(?:º|°)?\s*de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})", t):
        mes = MESES.get(m.group(2).lower())
        d = _iso(m.group(3), str(mes), m.group(1)) if mes else None
        ult = d or ult
    return ult


CABECALHO_PDF = 3000  # o relator vem na capa/cabeçalho
JANELA_FINAL = 6000  # caracteres do fim do PDF onde se procura a data do voto/acórdão (o começo é a capa e o protocolo)


def data_do_pdf(t: str, ano: int) -> str | None:
    """Data do voto/acórdão no PDF: 'Sessão ... dd/mm/aaaa' e, na falta, a última 'Salvador, d de mês de aaaa' do fim do texto.
    Só vale ano entre o do processo e quatro anos depois (descarta citação de outra consulta e erro de OCR: '20283')."""
    fim = t[-JANELA_FINAL:]

    def _ok(d: str | None) -> str | None:
        return d if d and ano <= int(d[:4]) <= ano + 4 else None

    achadas = [(mm.start(), _ok(_iso(mm.group(3), mm.group(2), mm.group(1)))) for mm in
               re.finditer(r"(?i)Sess[ãa]o[^\d\n]{0,40}?(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})", fim)]
    achadas += [(mm.start(), _ok(_iso(mm.group(3), str(MESES.get(mm.group(2).lower(), 0)), mm.group(1)))) for mm in
                re.finditer(r"(?i)Sess[ãa]o[^\d\n]{0,40}?(\d{1,2})\s*(?:º|°)?\s*de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})", fim)]
    achadas = [a for a in achadas if a[1]]
    if achadas:
        return max(achadas)[1]
    salvador = [(mm.start(), _ok(_iso(mm.group(3), str(MESES.get(mm.group(2).lower(), 0)), mm.group(1)))) for mm in
                re.finditer(r"(?i)\bSalvador[^\n\d]{0,12}?,?\s*(?:em\s+)?(\d{1,2})\s*(?:º|°)?\s*de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})", fim)]
    salvador = [a for a in salvador if a[1]]
    return max(salvador)[1] if salvador else None


def _iso(a: str, m: str, d: str) -> str | None:
    a, m, d = int(a), int(m), int(d)
    return f"{a:04d}-{m:02d}-{d:02d}" if 2000 <= a <= 2030 and 1 <= m <= 12 and 1 <= d <= 31 else None


RE_RELATOR = re.compile(r"(?i)\b(?:[úu]ltimo\s+)?relator(?:\s*\(a\)|a)?\s*:\s*[”\"“']?\s*([^\n,;(\-–—\d]{5,80})")


def relator_do_texto(t: str) -> str | None:
    """Nome depois de 'RELATOR:' / 'Relator(a):' / 'Último Relator:' (até vírgula, travessão, parêntese, número ou fim de linha)."""
    m = RE_RELATOR.search(t)
    if not m:
        return None
    nome = re.sub(r"\s+", " ", m.group(1)).strip(" .:")
    nome = re.sub(r"(?i)\s+(?:OAB|N[º°o]\s*Registro|Registro)\b.*$", "", nome).strip(" .:")
    return nome if len(nome.split()) >= 2 else None


def itens_da_pagina(texto_html: str) -> list[dict[str, Any]]:
    bl = blocos(texto_html)
    por: dict[str, dict[str, Any]] = {}

    def _item(k: str) -> dict[str, Any]:
        return por.setdefault(k, {"id": k, "numero": numero(k), "ano": int(k[-4:]), "ementa_inline": None, "texto_lista": None, "href": None})

    atual: dict[str, Any] | None = None
    modo = None
    corpo: list[str] = []

    def _fecha() -> None:
        if atual is not None and corpo:
            atual["ementa_inline" if modo == "inline" else "texto_lista"] = "\n".join(corpo)

    for b in bl:
        t = b["texto"]
        primeira, _, resto = t.partition("\n")  # 'Processo Consulta nº X<br />ASSUNTO: ...' vem num só parágrafo
        mi, ml = RE_CABECA_INLINE.match(primeira), RE_CABECA_LISTA.match(primeira)
        if mi or ml:
            _fecha()
            corpo = [resto] if resto else []
            m = mi or ml
            atual, modo = _item(chave(*m.groups()[-2:])), "inline" if mi else "lista"
            if ml and b["href"]:
                atual["href"] = b["href"]
            continue
        if re.match(r"^\s*\d{1,2}\)\s", t) or RE_FIM_LISTA.match(t) or re.match(r"(?i)^\s*S[úu]mula\s+0", t):
            _fecha()
            atual, corpo = None, []  # item 17-21 (não é consulta), ementário disciplinar 2007-2009, súmulas
            continue
        if atual is not None:
            corpo.append(t)
    _fecha()
    for it in por.values():
        it["ementa_pagina"] = it["ementa_inline"] or it["texto_lista"] or ""
        it["data_pagina"] = data_do_texto(it["ementa_pagina"])
        it["relator_pagina"] = relator_do_texto(it["ementa_pagina"])
    return sorted(por.values(), key=lambda i: (i["ano"], i["id"]))


# ---------------------------------------------------------------------------------------------------------------------
# PDF: camada de texto, OCR e CPF
# ---------------------------------------------------------------------------------------------------------------------

MIN_CHARS_PAGINA = 80  # abaixo disso a página é só imagem (a camada de texto vem vazia ou só com o carimbo)
OCR_DPI = 300
RE_CPF_FORMATADO = re.compile(r"(?<!\d)(\d{3})\s?[.,]\s?(\d{3})\s?[.,]\s?(\d{3})\s?[-–—~_]\s?(\d{2})(?!\d)")
RE_CPF_SEM_PONTO = re.compile(r"(?i)\bCPF\b[^\d\n]{0,25}(\d{3})\s?\.?\s?(\d{3})\s?\.?\s?(\d{3})\s?-?\s?(\d{2})(?!\d)")
RE_PALAVRA_CPF = re.compile(r"(?i)\bC\.?P\.?F\b")


def achar_cpf(paginas: list[str]) -> dict[str, list[int]]:
    """Páginas (1-based) em que o texto tem CPF. ``cpf``: número no padrão (ou depois da palavra CPF) com dígitos reais;
    ``placeholder``: o número fictício do SISDOC (111.111.111-11, todos os dígitos iguais), que não identifica ninguém;
    ``palavra``: a palavra CPF aparece sem número legível (OCR ruim), para conferência manual."""
    achado: dict[str, list[int]] = {"cpf": [], "placeholder": [], "palavra": []}
    for n, t in enumerate(paginas, 1):
        reais, falsos = False, False
        for rx in (RE_CPF_FORMATADO, RE_CPF_SEM_PONTO):
            for m in rx.finditer(t):
                dig = "".join(m.groups())
                if len(set(dig)) == 1:
                    falsos = True
                else:
                    reais = True
        if reais:
            achado["cpf"].append(n)
        elif falsos:
            achado["placeholder"].append(n)
        elif RE_PALAVRA_CPF.search(t):
            achado["palavra"].append(n)
    return achado


def _paginas_pdf(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"Pages:\s*(\d+)", out)
    if not m:
        raise ValueError(f"pdfinfo não leu {pdf.name}")
    return int(m.group(1))


def _camada_texto(pdf: Path, n: int) -> str:
    return subprocess.run(["pdftotext", "-layout", "-f", str(n), "-l", str(n), str(pdf), "-"], capture_output=True, timeout=120).stdout.decode("utf-8", "replace").replace("\f", "")


def _ocr(pdf: Path, n: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-png", "-r", str(OCR_DPI), "-gray", "-f", str(n), "-l", str(n), str(pdf), f"{tmp}/p"], capture_output=True, timeout=300)
        png = next(iter(sorted(Path(tmp).glob("p-*.png"))), None)
        if png is None:
            return ""
        r = subprocess.run(["tesseract", str(png), "stdout", "-l", "por", "--psm", "6"], capture_output=True, timeout=300)
        return r.stdout.decode("utf-8", "replace").replace("\f", "")


def ler_pdf(pdf: Path) -> dict[str, Any]:
    """Texto página a página: camada de texto onde há; OCR onde a página é só imagem."""
    paginas, ocr = [], []
    for n in range(1, _paginas_pdf(pdf) + 1):
        t = _camada_texto(pdf, n)
        if len(t.strip()) < MIN_CHARS_PAGINA:
            t = _ocr(pdf, n)
            ocr.append(n)
        paginas.append(t.strip("\n"))
    cpf = achar_cpf(paginas)
    return {"paginas": len(paginas), "paginas_ocr": ocr, "texto_pdf": "\n\n".join(paginas), "cpf_paginas": cpf["cpf"],
            "cpf_placeholder_paginas": cpf["placeholder"], "cpf_palavra_paginas": cpf["palavra"]}


# ---------------------------------------------------------------------------------------------------------------------
# Contrato do motor
# ---------------------------------------------------------------------------------------------------------------------

_cache: dict[str, Any] = {}


async def _pagina(client: Any, sem: asyncio.Semaphore) -> str:
    if "texto" not in _cache:
        h = await ce.baixar_texto(client, sem, URL, marca="__NEXT_DATA__")
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "pagina.html").write_text(h, encoding="utf-8")  # cache bruto da página
        _cache["texto"] = texto_institucional(h)
    return _cache["texto"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return len(consultas_citadas(await _pagina(client, sem)))


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return itens_da_pagina(await _pagina(client, sem))


def url_pdf(href: str) -> str:
    return href if href.startswith("http") else SITE + quote(unquote(href), safe="/%")


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    out = dict(item)
    if not item.get("href"):
        return out
    pasta = raw_dir_fonte(UF, SLUG, RAW_DIR) / "pdf"
    pasta.mkdir(parents=True, exist_ok=True)
    url = url_pdf(item["href"])
    arq = pasta / unquote(url.rsplit("/", 1)[1])
    if not arq.exists() or arq.stat().st_size < 1000:
        arq.write_bytes(await ce.baixar_bytes(client, sem, url, marca=b"%PDF"))
    out.update(pdf_url=url, pdf_arquivo=arq.name, pdf_bytes=arq.stat().st_size, **await asyncio.to_thread(ler_pdf, arq))
    return out


def liberados(raw_dir: Path = RAW_DIR) -> set[str]:
    arq = raw_dir_fonte(UF, SLUG, raw_dir) / "liberados.txt"
    return {ln.strip() for ln in arq.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")} if arq.exists() else set()


def normalizar(item: dict[str, Any], liberar: set[str] | None = None) -> dict[str, Any] | None:
    id_ = str(item["id"])
    if item.get("cpf_paginas") and id_ not in (liberados() if liberar is None else liberar):
        MOTIVOS[id_] = f"CPF no PDF (pág. {', '.join(map(str, item['cpf_paginas']))}): carga adiada a pedido do dono, texto intacto no cache"
        return None
    pdf_txt = (item.get("texto_pdf") or "").strip()
    ementa = (item.get("ementa_pagina") or "").strip()
    if not ementa and not pdf_txt:
        return None
    data = item.get("data_pagina") or (data_do_pdf(pdf_txt, int(item["ano"])) if pdf_txt else None)
    rec = montar_registro(
        UF, SLUG, id_,
        ementa=ementa or pdf_txt[:2000],
        url=URL,
        orgao_raw=ORGAO_RAW,
        sigla_orgao=SIGLA,
        orgao_norm=ORGAO_NORM,
        data_decisao=data,
        inteiro_teor=pdf_txt,
        numero_processo=item.get("numero"),
        relator=item.get("relator_pagina") or relator_do_texto(pdf_txt[:CABECALHO_PDF]) or None,
        consulta=True,
        publicacao="Site da OAB/BA - Ementas do Órgão Consultivo (OCEP)",
    )
    if not (item.get("relator_pagina") or relator_do_texto(pdf_txt[:CABECALHO_PDF])):
        rec["relator_principal"] = None  # sem 'Relator:' legível no cabeçalho não se adivinha pelo corpo do texto
    rec["pdf_url"] = item.get("pdf_url")
    rec["is_ted_disciplinar"] = 1
    from ted_search.inteiro_teor import texto_tem_relatorio_e_voto
    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(rec["inteiro_teor_texto"]) else 0
    return rec
