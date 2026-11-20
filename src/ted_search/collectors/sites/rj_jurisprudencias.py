"""OAB/RJ - Busca de ementas (Turmas do TED, Câmaras Especializadas, Órgão Especial, Conselho Pleno; 2019 a 2025),
https://oabrj.org.br/jurisprudencias.

``GET ?year=AAAA&orgao=&relator=&keyword=``: cada ano vem INTEIRO numa página (sem paginação), com uma tabela (data, processo,
relator, órgão, ementa cortada) e, no mesmo HTML, um modal ``#modal-ementa-N`` por linha com data, processo, relator, órgão,
ementa completa e "Decisão" (dispositivo curto). São 7 requisições, uma por ano.

Coleta educada: o robots.txt do site pede ``Crawl-delay: 10``; a pausa entre requisições é de 10 s, uma conexão só. A parada é
limpa em 401/403/429 ou captcha/desafio (``BloqueioSite``). Cada página fica em ``<raw>/pagina_AAAA.html`` e não é baixada de novo.

``esperado`` = linhas da tabela (``<td>`` com "Ver detalhes da ementa") somadas dos 7 anos; ``listar`` lê os modais; as duas
contagens têm de bater por ano. Id: ``AAAA-NNNN`` (ano da página + posição do modal; o site não dá id próprio).

O texto e os nomes entram exatamente como o site publica: a ementa vem com hifenização quebrada ("judi-cial") e palavras
coladas ("darepresentação"); entra como está. Só se retiram as quebras e tabulações do HTML nas pontas.

Sem inteiro teor (só ementa + "Decisão"). Sobreposição com DEOAB_RJ: descartam-se (``MOTIVOS``) os que têm o mesmo processo no
Diário E cuja ementa normalizada (sem acento, só letras, 150 primeiras) aparece no texto desse registro do Diário.
"""

from __future__ import annotations

import asyncio
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
from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

UF, SLUG = "RJ", "jurisprudencias"
URL = "https://oabrj.org.br/jurisprudencias"
ANOS = range(2019, 2026)
PAUSA_RJ = 10.0  # Crawl-delay do robots.txt
MARCA = b"modal-ementa-"

RE_LINHA_TABELA = re.compile(r'data-bs-target="#modal-ementa-(\d+)"')
RE_MODAL = re.compile(r'<div class="modal fade" id="modal-ementa-(\d+)".*?<div class="modal-body">(.*?)</div>\s*</div>\s*</div>\s*</div>', re.S)
RE_CAMPO = {
    "processo": re.compile(r"<strong>Processo:</strong>\s*(.*?)</h5>", re.S),
    "data": re.compile(r"<strong>Data:</strong>\s*(.*?)<br", re.S),
    "relator": re.compile(r"<strong>Relator:</strong>\s*(.*?)<br", re.S),
    "orgao": re.compile(r"<strong>Órgão julgador:</strong>\s*(.*?)<br", re.S),
    "ementa": re.compile(r"<strong>Ementa:</strong>\s*<br\s*/?>(.*?)<br\s*/?>\s*<br\s*/?>\s*<strong>Decis", re.S),
    "decisao": re.compile(r"<strong>Decisão:</strong>\s*<br\s*/?>(.*?)<br\s*/?>\s*<br\s*/?>", re.S),
}

MOTIVOS: dict[str, str] = {}
_cache: dict[int, str] = {}
_DIARIO: dict[str, list[str]] | None = None


def _raw() -> Path:
    return raw_dir_fonte(UF, SLUG, RAW_DIR)


def _txt(s: str | None) -> str:
    """Texto do campo como o site publica: entidades resolvidas, sem tags; só as pontas são aparadas."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", s or "")).replace("\xa0", " ").strip()


def linhas_da_tabela(h: str) -> int:
    return len(RE_LINHA_TABELA.findall(h))


def modais(h: str, ano: int) -> list[dict[str, Any]]:
    saida = []
    for m in RE_MODAL.finditer(h):
        corpo = m.group(0)
        it: dict[str, Any] = {"id": f"{ano}-{int(m.group(1)):04d}", "ano_pagina": ano, "modal": int(m.group(1))}
        for k, rx in RE_CAMPO.items():
            mm = rx.search(corpo)
            it[k] = _txt(mm.group(1)) if mm else ""
        saida.append(it)
    return saida


def _arquivo(ano: int) -> Path:
    return _raw() / f"pagina_{ano}.html"


async def _pagina(client: Any, sem: asyncio.Semaphore, ano: int) -> str:
    if ano in _cache:
        return _cache[ano]
    arq = _arquivo(ano)
    if arq.exists() and arq.stat().st_size > 1000:
        _cache[ano] = arq.read_text(encoding="utf-8")
    else:
        _raw().mkdir(parents=True, exist_ok=True)
        corpo = await ce.baixar_bytes(client, sem, f"{URL}?year={ano}&orgao=&relator=&keyword=", marca=MARCA, pausa=PAUSA_RJ)
        arq.write_bytes(corpo)
        _cache[ano] = corpo.decode("utf-8", "replace")
        print(f"[RJ] ano {ano}: {len(corpo)} bytes", flush=True)
    return _cache[ano]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    total = 0
    for ano in ANOS:
        total += linhas_da_tabela(await _pagina(client, sem, ano))
    return total


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    itens: list[dict[str, Any]] = []
    for ano in ANOS:
        h = await _pagina(client, sem, ano)
        ms = modais(h, ano)
        if len(ms) != linhas_da_tabela(h):
            raise RuntimeError(f"ano {ano}: {len(ms)} modais para {linhas_da_tabela(h)} linhas na tabela")
        itens.extend(ms)
    return itens


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return dict(item)  # o modal já traz tudo


# ---------------------------------------------------------------------------------------------------------------------
# Órgão, data, marca
# ---------------------------------------------------------------------------------------------------------------------

def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def orgao_rj(orgao: str) -> tuple[str, str]:
    """(sigla, orgao_norm) pelo que o site informa em 'Órgão julgador' (nunca por palavra do texto)."""
    up = _sem_acento(orgao).upper().strip().replace("T.E.D.", "TED")
    m = re.match(r"(\d{1,2})\s*A?\W{0,3}\s*TURMA\b", up)
    if m and "TED" in up:
        n = int(m.group(1))
        return f"OABRJ-TED-T{n}", f"Tribunal de Ética e Disciplina da OAB/RJ/{n}ª Turma Disciplinar"
    if "TURMA UNICA" in up:
        return "OABRJ-TED-TU", "Tribunal de Ética e Disciplina da OAB/RJ/Turma Única"
    if "PLENO" in up and "TED" in up:
        return "OABRJ-TED-PLENO", "Tribunal de Ética e Disciplina da OAB/RJ/Pleno"
    m = re.match(r"(\d{1,2})\s*A?\W{0,3}\s*CAMARA\s+ESPECIALIZADA", up)
    if m:
        n = int(m.group(1))
        return f"OABRJ-CE{n}", f"Conselho Seccional - RJ/{n}ª Câmara Especializada"
    if "ORGAO ESPECIAL" in up:
        return "OABRJ-OE", "Conselho Seccional - RJ/Órgão Especial"
    if "CONSELHO PLENO" in up:
        return "OABRJ-PLENO", "Conselho Seccional - RJ/Conselho Pleno"
    return "OABRJ", f"Conselho Seccional - RJ/{orgao.strip().title()}" if orgao.strip() else "Conselho Seccional - RJ"


def marca_disciplinar(orgao: str, ementa: str, decisao: str) -> int:
    """Turmas do TED: 1. Câmaras Especializadas (inscrição/licença): 0. Demais: o que a regra do Diário disser."""
    up = _sem_acento(orgao).upper().replace("T.E.D.", "TED")
    if (re.match(r"\s*\d{1,2}\s*A?\W{0,3}\s*TURMA\b", up) or "TURMA UNICA" in up) and "TED" in up:
        return 1
    if "CAMARA ESPECIALIZADA" in up:
        return 0
    return 1 if eh_materia_disciplinar(orgao, "", f"{ementa}\n{decisao}") else 0


def _data(s: str | None) -> str | None:
    m = re.fullmatch(r"\s*(\d{2})/(\d{2})/(\d{4})\s*", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


# ---------------------------------------------------------------------------------------------------------------------
# Sobreposição com DEOAB_RJ
# ---------------------------------------------------------------------------------------------------------------------

def chave_processo(numero: str | None) -> str | None:
    """'009.501/18' e '9.501/2018' -> '9501/2018' (sem pontos e zeros à esquerda, ano com 4 dígitos)."""
    m = re.search(r"(\d[\d.\s]*)\s*/\s*(\d{2}|\d{4})\b", numero or "")
    if not m:
        return None
    nr = re.sub(r"\D", "", m.group(1)).lstrip("0")
    ano = m.group(2)
    if len(ano) == 2:
        ano = ("20" if int(ano) <= 30 else "19") + ano
    return f"{nr}/{ano}" if nr else None


def so_letras(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", _sem_acento(s or "").lower())


def chave_ementa(ementa: str | None) -> str:
    return so_letras(ementa)[:150]


def carregar_diario(db_path: Path = DB_PATH) -> dict[str, list[str]]:
    """chave do processo -> textos normalizados (só letras) dos registros de DEOAB_RJ com esse processo."""
    mapa: dict[str, list[str]] = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=120) as conn:
        for num, e, a, t in conn.execute(
            "SELECT numero_processo, descricao_ementa, descricao_acordao, inteiro_teor_texto FROM julgados "
            "WHERE fonte = 'DEOAB_RJ' AND numero_processo IS NOT NULL"
        ):
            k = chave_processo(num)
            if k:
                mapa.setdefault(k, []).append(so_letras(f"{e or ''} {a or ''} {t or ''}"))
    return mapa


def sobrepoe(item: dict[str, Any], diario: dict[str, list[str]]) -> bool:
    k = chave_processo(item.get("processo"))
    ch = chave_ementa(item.get("ementa"))
    return bool(k and ch and any(ch in t for t in diario.get(k, ())))


def preparar(db_path: Path = DB_PATH) -> None:
    global _DIARIO
    MOTIVOS.clear()
    _DIARIO = carregar_diario(db_path)


# ---------------------------------------------------------------------------------------------------------------------

def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    ident = str(item["id"])
    ementa = item.get("ementa") or ""
    if not ementa.strip() and not (item.get("decisao") or "").strip():
        MOTIVOS[ident] = "sem ementa e sem decisão"  # o site publica linhas só com a 'Decisão': essas entram
        return None
    if _DIARIO is not None and sobrepoe(item, _DIARIO):
        MOTIVOS[ident] = "sobreposicao DEOAB_RJ (mesmo processo e ementa no texto do Diário)"
        return None
    decisao = item.get("decisao") or ""
    orgao = item.get("orgao") or ""
    sigla, norm = orgao_rj(orgao)
    data = _data(item.get("data"))
    rec = montar_registro(
        UF, SLUG, ident,
        ementa=ementa,
        acordao=decisao,
        url=f"{URL}?year={item['ano_pagina']}&orgao=&relator=&keyword=",
        orgao_raw=orgao, sigla_orgao=sigla, orgao_norm=norm,
        data_decisao=data,
        numero_processo=item.get("processo") or None,
        relator=item.get("relator") or None,
        consulta=False,
        publicacao="Site da OAB/RJ - Busca de ementas",
    )
    rec["pdf_url"] = None
    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(rec["inteiro_teor_texto"]) else 0
    rec["is_ted_disciplinar"] = marca_disciplinar(orgao, ementa, decisao)
    return rec
