"""OAB/RO - Ementários do TED em HTML (WordPress, CPT 'ementarios'): um post por ementa.

Listagem: https://www.oab-ro.org.br/a-oab-ro/leis-e-normas/jurisprudencia/ementarios/ e .../page/2/ (50 cartões por página; o fim
é a página sem o link 'Próxima'). Cada cartão traz o link do post (``/ementarios/<slug>/``), o título e o rótulo '2019 - <tema>'.
O detalhe tem a data de publicação do post (não é a do julgamento), o título e o corpo: ementa + frase do acórdão ('Vistos,
relatados e examinados... acordam os membros da Terceira Turma Disciplinar... julgar improcedente') + citação
'(OAB, Tribunal de Ética, Processo 22.0000.AAAA.NNNNNN-D, Rel. ...)'. Sem voto e sem PDF por decisão: não é inteiro teor.
Não se usa /wp-json/ (não existe) nem /gerenciador/data/uploads/json/ (robots.txt).

O site não declara total: ``esperado`` = itens por página (cartões da 1ª) x (páginas - 1) + cartões da última página, lido da
paginação. ``listar`` percorre as páginas pelo link 'Próxima' e confere que a página seguinte à última não existe.
O cache bruto de cada listagem e de cada post fica em ``data/raw/site_ro_ementarios/`` (pagina_N.html, detalhe/<slug>.html).

Um dos 86 cartões é o post do PDF do ementário (fonte SITE_RO_EMENTARIO_PDF), que não é decisão: é rejeitado com esse motivo.
Sobreposição: mesmo processo (só dígitos) E mesma ementa normalizada (150 primeiras letras) no texto do registro de DEOAB_RO, ou já
presente na fonte SITE_RO_EMENTARIO_PDF (fica a do PDF), é descartado com o motivo; os demais carregam.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.collectors.sites import ro_ementario_pdf as pdf
from ted_search.config import DB_PATH, RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "RO", "ementarios"
SITE = "https://www.oab-ro.org.br"
URL = f"{SITE}/a-oab-ro/leis-e-normas/jurisprudencia/ementarios/"
SLUG_PDF_POST = "ementario-do-tribunal-de-etica-e-disciplina"
SIGLA = "OABRO-TED"
ORGAO_GENERICO = "Tribunal de Ética e Disciplina da OAB/RO"
MOTIVOS: dict[str, str] = {}

RE_CARTAO = re.compile(r'(?s)<li>\s*<a href="(https://www\.oab-ro\.org\.br/ementarios/([^"/]+)/)"\s+title="([^"]*)".*?'
                       r'<header class="cards-resultados-tipo">(.*?)</header>.*?</li>')
RE_PROXIMA = re.compile(r'class="next page-numbers" href="([^"]+)"')
RE_H1 = re.compile(r'(?s)<h1 class="header-internas__title">(.*?)</h1>')
RE_DATA_POST = re.compile(r'(?s)single-noticias__informacoes">.*?(\d{2})/(\d{2})/(\d{4})')
RE_CORPO = re.compile(r'(?s)<div class="single-noticias__content[^"]*">(.*?)</div>\s*</div>\s*</div>\s*</div>')
RE_PROCESSO = re.compile(r"(?:Processo|Consulta)\s+(?:n[ºo°]\.?:?\s*)?(\d[\d.\-\s]*\d)", re.I)
RE_CONSULTA = re.compile(r"Tribunal de Ética,\s*Consulta\b")
RE_RELATOR = re.compile(r"Rel(?:ator|atora|\.)?\s*(?:\(a\))?\s*:?\s*(?:Dr[a]?\.?\s*)*([^)]+?)\s*\)?\s*\.?\s*$")
ORDINAIS = {"primeira": 1, "segunda": 2, "terceira": 3, "quarta": 4, "quinta": 5, "sexta": 6}
RE_TURMA = re.compile(r"(?i)\b(primeira|segunda|terceira|quarta|quinta|sexta)\s+turma|\b(\d)\s*ª\s*turma")

_cache: dict[str, Any] = {}


def _pasta() -> Path:
    p = raw_dir_fonte(UF, SLUG, RAW_DIR)
    (p / "detalhe").mkdir(parents=True, exist_ok=True)
    return p


def so_texto(h: str) -> str:
    """HTML do corpo -> texto: <p>/<li> viram parágrafos, <br> vira quebra de linha, entidades resolvidas, nbsp vira espaço."""
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(?:p|li|div|h\d|tr)>", "\n\n", h)
    t = html_lib.unescape(re.sub(r"<[^>]+>", "", h)).replace("\xa0", " ")
    linhas = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in t.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


def cartoes(h: str) -> list[dict[str, Any]]:
    i = h.find("cards-resultados resultados-ementarios")
    if i < 0:
        raise ValueError("listagem fora do padrão: sem 'cards-resultados resultados-ementarios'")
    saida = []
    for m in RE_CARTAO.finditer(h[i : h.find("</ul>", i)]):
        rotulo = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(4)))).strip()
        ano, _, tema = rotulo.partition(" - ")
        saida.append({"id": m.group(2), "url": m.group(1), "titulo": html_lib.unescape(m.group(3)), "rotulo": rotulo,
                      "ano_rotulo": int(ano) if ano.isdigit() else None, "tema": tema.strip() or None})
    return saida


def proxima(h: str) -> str | None:
    m = RE_PROXIMA.search(h)
    return html_lib.unescape(m.group(1)) if m else None


async def _pagina(client: Any, sem: asyncio.Semaphore, n: int, tentativas: int = 4) -> str | None:
    arq = _pasta() / f"pagina_{n}.html"
    if arq.exists() and arq.stat().st_size > 5000:
        return arq.read_text(encoding="utf-8")
    url = URL if n == 1 else f"{URL}page/{n}/"
    try:
        h = await ce.baixar_texto(client, sem, url, marca="cards-resultados", tentativas=tentativas)
    except ce.BloqueioSite:
        raise
    except Exception:  # noqa: BLE001
        return None
    arq.write_text(h, encoding="utf-8")
    return h


async def _listagem(client: Any, sem: asyncio.Semaphore) -> dict[str, Any]:
    if "lista" in _cache:
        return _cache["lista"]
    paginas: list[list[dict[str, Any]]] = []
    n = 1
    while True:
        h = await _pagina(client, sem, n)
        if h is None:
            raise RuntimeError(f"página {n} da listagem não baixou")
        paginas.append(cartoes(h))
        if not proxima(h):
            break
        n += 1
    seguinte = await _pagina(client, sem, n + 1, tentativas=1)  # a página depois da última não pode existir
    if seguinte is not None and cartoes(seguinte):
        raise RuntimeError(f"a página {n + 1} existe e tem cartões: a paginação não foi até o fim")
    _cache["lista"] = {"paginas": paginas, "esperado": len(paginas[0]) * (len(paginas) - 1) + len(paginas[-1])}
    return _cache["lista"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return (await _listagem(client, sem))["esperado"]


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    vistos: dict[str, dict[str, Any]] = {}
    for pag, cards in enumerate((await _listagem(client, sem))["paginas"], 1):
        for c in cards:
            vistos.setdefault(c["id"], {**c, "pagina": pag})
    return list(vistos.values())


def detalhe(h: str) -> dict[str, Any]:
    t = RE_H1.search(h)
    d = RE_DATA_POST.search(h)
    c = RE_CORPO.search(h)
    if not (t and c):
        raise ValueError("post fora do padrão: sem título ou sem corpo")
    return {"titulo_post": so_texto(t.group(1)), "data_post": f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else None, "texto": so_texto(c.group(1))}


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    arq = _pasta() / "detalhe" / f"{item['id']}.html"
    if not arq.exists() or arq.stat().st_size < 5000:
        arq.write_text(await ce.baixar_texto(client, sem, item["url"], marca="single-noticias__content"), encoding="utf-8")
    d = detalhe(arq.read_text(encoding="utf-8"))
    return {**item, **d}


# ---------------------------------------------------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------------------------------------------------

def processo_do_texto(t: str) -> str | None:
    m = RE_PROCESSO.search(t[-400:]) or RE_PROCESSO.search(t)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def relator_do_texto(t: str) -> str | None:
    cita = re.search(r"OAB(?:/RO)?,\s*Tribunal de Ética,.*$", t.strip(), re.S)
    m = RE_RELATOR.search(re.sub(r"\s+", " ", cita.group(0))) if cita else None
    nome = re.sub(r"\s+", " ", m.group(1)).strip(" .,") if m else ""
    return nome if len(nome.split()) >= 2 else None


def orgao_do_texto(t: str) -> tuple[str, str, str]:
    """Órgão pelo que o post diz ('acordam os membros da Terceira Turma Disciplinar...', 'Pleno'); senão o TED em geral."""
    m = RE_TURMA.search(t)
    if m:
        n = ORDINAIS[m.group(1).lower()] if m.group(1) else int(m.group(2))
        return f"{n}ª Turma Disciplinar do TED/OAB/RO", f"{SIGLA}-T{n}", f"{ORGAO_GENERICO}/{n}ª Turma"
    if re.search(r"(?i)\bPleno\b", t):
        return "Pleno do TED/OAB/RO", f"{SIGLA}-PLENO", f"{ORGAO_GENERICO}/Pleno"
    return "TED/OAB/RO", SIGLA, ORGAO_GENERICO


_DIARIO: dict[str, list[str]] | None = None
_PDF: dict[str, list[str]] | None = None


def carregar_pdf(raw_dir: Path = RAW_DIR) -> dict[str, list[str]]:
    """{processo (dígitos): [ementa normalizada]} de cada entrada coletada da fonte SITE_RO_EMENTARIO_PDF."""
    arq = raw_dir_fonte(pdf.UF, pdf.SLUG, raw_dir) / "itens.jsonl"
    saida: dict[str, list[str]] = {}
    if arq.exists():
        with arq.open(encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    it = json.loads(ln)
                    k = pdf.digitos(it.get("processo"))
                    if k:
                        saida.setdefault(k, []).append(pdf.sem_acento_letras(it.get("ementa") or ""))
    return saida


def preparar(db_path: Path = DB_PATH) -> None:
    global _DIARIO, _PDF
    _DIARIO = pdf.carregar_diario(db_path)
    _PDF = carregar_pdf()
    MOTIVOS.clear()


def _ementa_e_acordao(texto: str) -> tuple[str, str]:
    m = re.search(r"\bVistos,", texto)
    return (texto[: m.start()].strip(), texto[m.start() :].strip()) if m else (texto.strip(), "")


def sobrepoe_pdf(numero: str | None, ementa: str, base: dict[str, list[str]]) -> bool:
    """Mesmo processo e o começo (60 letras) da ementa de um aparece na do outro: PDF e HTML trazem o mesmo acórdão."""
    k = pdf.digitos(numero)
    mine = pdf.sem_acento_letras(ementa)
    return bool(k and len(mine) >= 60 and any(mine[:60] in t or t[:60] in mine for t in base.get(k, []) if len(t) >= 60))


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    id_ = str(item["id"])
    if id_ == SLUG_PDF_POST:
        MOTIVOS[id_] = "post do PDF do ementário (obra inteira, não é decisão): coberto pela fonte SITE_RO_EMENTARIO_PDF"
        return None
    texto = (item.get("texto") or "").strip()
    if len(texto) < 40:
        MOTIVOS[id_] = "post sem texto de ementa"
        return None
    ementa, acordao = _ementa_e_acordao(texto)
    proc = processo_do_texto(texto)
    if _DIARIO is not None and pdf.sobrepoe(proc, ementa, _DIARIO):
        MOTIVOS[id_] = f"sobreposição com DEOAB_RO (mesmo processo {proc} e mesma ementa)"
        return None
    if _PDF is not None and sobrepoe_pdf(proc, ementa, _PDF):
        MOTIVOS[id_] = f"mesmo acórdão já na fonte SITE_RO_EMENTARIO_PDF (processo {proc}): fica a do PDF"
        return None
    orgao, sigla, norm = orgao_do_texto(texto)
    relator = relator_do_texto(texto)
    rec = montar_registro(
        UF, SLUG, id_,
        ementa=ementa,
        acordao=acordao,
        inteiro_teor=texto,  # o corpo do post como está (ementa + acórdão numa só peça)
        url=item.get("url") or URL,
        orgao_raw=orgao,
        sigla_orgao=sigla,
        orgao_norm=norm,
        data_decisao=None,  # o post só traz a data de publicação; o julgamento não é informado
        numero_processo=proc,
        relator=relator,
        consulta=bool(RE_CONSULTA.search(texto)),  # '(OAB, Tribunal de Ética, Consulta Nº ...)': consulta em tese
        publicacao=f"Site da OAB/RO - Ementários (post de {item['data_post'][8:]}/{item['data_post'][5:7]}/{item['data_post'][:4]})" if item.get("data_post") else None,
    )
    if not relator:
        rec["relator_principal"] = None
    rec["ano_ementa"] = item.get("ano_rotulo")  # rótulo do cartão ('2019 - tema')
    rec["numero_ementa"] = id_
    rec["is_ted_disciplinar"] = 1  # órgão julgador: turmas/Pleno do TED
    from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(rec["inteiro_teor_texto"]) else 0
    return rec
