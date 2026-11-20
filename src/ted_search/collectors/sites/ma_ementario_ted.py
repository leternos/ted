"""OAB/MA - Ementário do TED (acórdãos de 2016), https://www.oabma.org.br/ted/ementario.

Página HTML estática, sem paginação: um único GET traz os blocos ``<div class="ementa">`` (um por acórdão). O site não
declara total; o ``esperado`` é a contagem independente de ``class="ementa"`` no HTML e ``listar`` parte os blocos
com outro padrão (as duas contagens têm de bater). Cada bloco traz processo, Repte/Repdo (iniciais, como o site publica),
acórdão nº/ano, relator, ementa e dispositivo. O texto entra exatamente como o site publica (só o HTML vira texto e os
espaços de layout são colapsados): nada é abreviado, mascarado ou retirado. Id estável: ``<processo>[-ac<nº>]``.

Só ementa + dispositivo ("o relatório e o voto integram o processo"): sem inteiro teor, sem PDF.
Sobreposição com DEOAB_MA: nenhuma (o Diário no banco começa em 2019; estes acórdãos são de 2016).
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "MA", "ementario-ted"
URL = "https://www.oabma.org.br/ted/ementario"

RE_BLOCO = re.compile(r'<div class="ementa">(.*?)</div>', re.S)
RE_PROC = re.compile(r"(?i)\bproc(?:esso)?\.?\s*n[ºo°]?\.?\s*:?\s*([\d][\d./-]*\d)")
RE_ACORDAO = re.compile(r"(?i)\bac[óo]rd[ãa]o\s*n[ºo°]?\.?\s*(\d+)\s*/\s*(\d{4})")
RE_RELATOR = re.compile(r"(?i)\bRELATOR(A)?:\s*(.+?)\.?\s*EMENTA\s*:")
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RE_DATA = r"(\d{1,2})\s+de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})"
RE_SESSAO = re.compile(r"(?i)Sess[ãa]o\s+(?:Plen[áa]ria[^.]{0,40}?)?(?:do\s+)?dia\s+" + RE_DATA)
RE_FINAL = re.compile(r"(?i)S[ãa]o\s+Lu[íi]s[^.]{0,10}?,\s*" + RE_DATA + r"\.?\s*$")

_cache: dict[str, str] = {}


def texto_do_bloco(b: str) -> str:
    t = html_lib.unescape(re.sub(r"<[^>]+>", "", b)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _iso(m: re.Match[str]) -> str | None:
    mes = MESES.get(m.group(2).lower())
    return f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(1)):02d}" if mes else None


def data_do_texto(t: str) -> str | None:
    """Data do julgamento: a da sessão plenária citada no dispositivo; senão a data que fecha o texto; senão nenhuma."""
    m = RE_SESSAO.search(t) or RE_FINAL.search(t)
    return _iso(m) if m else None


def blocos(h: str) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    usados: dict[str, int] = {}
    for b in RE_BLOCO.findall(h):
        t = texto_do_bloco(b)
        pm = RE_PROC.search(t)
        am = RE_ACORDAO.search(t)
        rm = RE_RELATOR.search(t)
        if not pm:
            raise ValueError(f"bloco sem número de processo: {t[:80]!r}")
        proc = pm.group(1).rstrip(".")
        base = proc.replace("/", "-") + (f"-ac{int(am.group(1))}" if am else "")
        usados[base] = usados.get(base, 0) + 1
        ident = base if usados[base] == 1 else f"{base}-{usados[base]}"
        saida.append({
            "id": ident, "processo": proc, "acordao": f"{int(am.group(1))}/{am.group(2)}" if am else None,
            "relator": rm.group(2).strip() if rm else None, "relatora": bool(rm and rm.group(1)),
            "data": data_do_texto(t), "texto": t,
        })
    return saida


async def _pagina(client: Any, sem: asyncio.Semaphore) -> str:
    if "h" not in _cache:
        _cache["h"] = await ce.baixar_texto(client, sem, URL, marca='class="ementa"')
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "pagina.html").write_text(_cache["h"], encoding="utf-8")
    return _cache["h"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    return (await _pagina(client, sem)).count('class="ementa"')


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return blocos(await _pagina(client, sem))


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return dict(item)


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    texto = (item.get("texto") or "").strip()
    if len(texto) < 40:
        return None
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=texto,
        url=URL,
        orgao_raw="Tribunal de Ética e Disciplina da OAB/MA",
        sigla_orgao="OABMA-TED",
        orgao_norm="Tribunal de Ética e Disciplina da OAB/MA",
        data_decisao=item.get("data"),
        numero_processo=item.get("processo"),
        numero_ementa=item.get("acordao"),
        relator=item.get("relator"),
        consulta=False,
        publicacao="Site da OAB/MA - Ementário do TED",
    )
    if item.get("acordao"):
        rec["ano_ementa"] = int(item["acordao"].split("/")[1])
    rec["pdf_url"] = None
    rec["is_ted_disciplinar"] = 1
    rec["tem_inteiro_teor"] = 0  # só ementa + dispositivo
    return rec
