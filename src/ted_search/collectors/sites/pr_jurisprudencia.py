"""OAB/PR - buscador de jurisprudência da Seccional (acórdãos de todos os órgãos, 1995 a 2026).

API JSON pública, a mesma que o front React usa (sem login, cookie nem token):
``GET https://celulaadv.oabpr.org.br/api-jurisprudencia/documento-acordao/search?searchTerm=&page=N&limit=100``
com resposta ``{data: [...], pagination: {page, limit, total, totalPages, hasNext, hasPrev}}``. O ``total`` é o que o
próprio site declara (``esperado``); ``ind`` é o ID estável do acórdão. A listagem já traz o registro completo, então
``baixar`` só devolve o item: o "download" é a paginação, e ela é a parte cara.

Coleta educada, porque o host ``ted.`` da mesma entidade proíbe ``/api/`` no robots.txt: UMA conexão, UMA requisição
a cada ``INTERVALO`` segundos, User-Agent identificável, espera crescente em 429/5xx, e PARADA (``ApiBloqueada``)
em 401/403 ou resposta que não seja JSON (login, captcha): nunca se contorna. Cada página fica em
``<raw>/paginas/pNNNN.json`` e não é baixada de novo (retomável).

O texto e os nomes entram no banco exatamente como a OAB/PR publica: nada é abreviado, mascarado ou retirado.

Só há ementa + dispositivo (``titulo``: "Vistos... acordam"); não há voto nem PDF: ``tem_inteiro_teor = 0``.

Sobreposição com o Diário (DEOAB_PR, 2019 em diante): ``chave_processo`` dá a chave "nnnn/aaaa" e ``normalizar`` marca
``_sobreposicao`` quando a chave consta em ``DEOAB_PR``; quem decide o que carregar é ``carregar`` (a sobreposição fica
só no cache bruto).
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ted_search.collectors.deoab_nacional import eh_materia_disciplinar
from ted_search.config import DB_PATH, RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte

UF, SLUG = "PR", "jurisprudencia"
URL = "https://celulaadv.oabpr.org.br/api-jurisprudencia/documento-acordao/search"
LIMITE = 100
INTERVALO = 2.0  # segundos entre requisições (uma conexão só)
ESPERAS = (10, 30, 90, 180, 300, 600)  # espera, em segundos, antes de cada nova tentativa em 429/5xx/erro de rede
HEADERS = {
    "User-Agent": "ted-pesquisa-jurisprudencia/1.0 (coleta academica de baixa taxa; 1 req/2s)",
    "Accept": "application/json",
}
# A partir daqui o Diário (DEOAB_PR, 1ª edição em 2019-02-22) já publica a decisão: só nessa janela a repetição do
# número de processo é sobreposição. Um acórdão de 2010 num processo que reaparece no Diário em 2020 é outra decisão.
INICIO_DIARIO = "2018-10-01"


class ApiBloqueada(RuntimeError):
    """A API passou a exigir login/captcha ou recusou (401/403): a coleta para, sem contornar."""


_ultimo = [0.0]
_trava: asyncio.Lock | None = None  # criada sob demanda, dentro do loop em execução


def _raw() -> Path:
    return raw_dir_fonte(UF, SLUG, RAW_DIR)


def _arquivo_pagina(n: int) -> Path:
    return _raw() / "paginas" / f"p{n:04d}.json"


async def _get(client: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Uma requisição polida: serializada, espaçada, com espera em 429/5xx e parada em 401/403/não-JSON."""
    global _trava
    if _trava is None:
        _trava = asyncio.Lock()
    erro: Exception | None = None
    for tentativa in range(len(ESPERAS) + 1):
        async with _trava:
            falta = INTERVALO - (time.monotonic() - _ultimo[0])
            if falta > 0:
                await asyncio.sleep(falta)
            try:
                resp = await client.get(URL, params=params, headers=HEADERS)
            except Exception as exc:  # erro de rede: espera e repete
                erro = exc
                resp = None
            finally:
                _ultimo[0] = time.monotonic()
        espera = ESPERAS[tentativa] if tentativa < len(ESPERAS) else None
        if resp is not None:
            if resp.status_code in (401, 403):
                raise ApiBloqueada(f"HTTP {resp.status_code} em {params}: a API passou a exigir acesso; coleta interrompida")
            if resp.status_code == 429 or resp.status_code >= 500:
                erro = RuntimeError(f"HTTP {resp.status_code}")
                ra = resp.headers.get("retry-after", "") if hasattr(resp, "headers") else ""
                if ra.isdigit() and espera is not None:
                    espera = max(espera, min(int(ra), 900))
            else:
                resp.raise_for_status()
                try:
                    corpo = resp.json()
                except ValueError as exc:
                    raise ApiBloqueada(f"resposta não-JSON em {params} (login/captcha?): coleta interrompida") from exc
                if not isinstance(corpo, dict) or "data" not in corpo or "pagination" not in corpo:
                    raise ApiBloqueada(f"resposta fora do contrato em {params}: coleta interrompida")
                return corpo
        if espera is None:
            break
        print(f"[PR] {erro}; nova tentativa em {espera}s", flush=True)
        await asyncio.sleep(espera)
    raise RuntimeError(f"falha em {params}: {erro}")


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    """Total declarado pela API (``pagination.total``), numa consulta de 1 item."""
    corpo = await _get(client, {"searchTerm": "", "page": 1, "limit": 1})
    return int(corpo["pagination"]["total"])


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    """Todos os acórdãos, página a página (cada página vai para o cache e só é baixada uma vez)."""
    (_raw() / "paginas").mkdir(parents=True, exist_ok=True)
    primeira = _arquivo_pagina(1)
    if primeira.exists():
        total_paginas = json.loads(primeira.read_text(encoding="utf-8"))["pagination"]["totalPages"]
    else:
        corpo = await _get(client, {"searchTerm": "", "page": 1, "limit": LIMITE})
        primeira.write_text(json.dumps(corpo, ensure_ascii=False), encoding="utf-8")
        total_paginas = int(corpo["pagination"]["totalPages"])
    itens: list[dict[str, Any]] = []
    vistos: set[str] = set()
    repetidos: list[str] = []
    n = 1
    while n <= total_paginas:
        arq = _arquivo_pagina(n)
        if arq.exists():
            corpo = json.loads(arq.read_text(encoding="utf-8"))
        else:
            corpo = await _get(client, {"searchTerm": "", "page": n, "limit": LIMITE})
            if not corpo["data"] and n < total_paginas:
                raise RuntimeError(f"página {n} vazia antes da última ({total_paginas}): total mudou durante a coleta?")
            arq.write_text(json.dumps(corpo, ensure_ascii=False), encoding="utf-8")
            if n % 20 == 0:
                print(f"[PR] página {n}/{total_paginas}", flush=True)
        total_paginas = max(total_paginas, int(corpo["pagination"]["totalPages"]))
        for it in corpo["data"]:
            ident = str(it["ind"])
            if ident in vistos:
                repetidos.append(ident)
                continue
            vistos.add(ident)
            itens.append({"id": ident, "_pagina": n, **it})
        n += 1
    (_raw() / "repetidos.json").write_text(json.dumps(repetidos), encoding="utf-8")
    return itens


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    """A listagem já traz o acórdão inteiro (sem voto nem PDF): nada mais a baixar."""
    return dict(item)


# ---------------------------------------------------------------------------------------------------------------------
# Dado pessoal
# ---------------------------------------------------------------------------------------------------------------------

PARTICULAS = {"de", "da", "do", "das", "dos", "e", "di", "du", "del", "la", "van", "von"}


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _padrao_nome(nome: str) -> re.Pattern[str] | None:
    palavras = [re.escape(p) for p in _sem_acento(nome).split() if p]
    if len(palavras) < 2:
        return None
    return re.compile(r"(?<!\w)" + r"[\s,]+".join(palavras) + r"(?!\w)", re.IGNORECASE)


# ---------------------------------------------------------------------------------------------------------------------
# Texto e metadados
# ---------------------------------------------------------------------------------------------------------------------

def _limpar(s: str | None) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _data(ts: str | None) -> str | None:
    """'1995-08-07T03:00:00.000Z' (meia-noite em Brasília) -> '1995-08-07'. Data impossível vira None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.hour < 3:  # meia-noite UTC (00h) já é o dia do julgamento; 03h UTC é meia-noite em Brasília
        d = dt.date()
    else:
        d = dt.astimezone(timezone(timedelta(hours=-3))).date()
    if not (1990 <= d.year) or d > datetime.now().date():  # ano absurdo ou julgamento no futuro (ex.: 2041): inválida
        return None
    return d.isoformat()


def chave_processo(item: dict[str, Any]) -> str | None:
    """'nnnn/aaaa': nrProcesso + ano de dtProcesso (o mesmo formato de ``numero_processo`` do banco)."""
    dp = item.get("documentoProcesso") or {}
    nr = dp.get("nrProcesso")
    ano = (_data(dp.get("dtProcesso")) or "")[:4]
    if nr in (None, "") or not ano:
        return None
    return f"{int(nr)}/{ano}" if str(nr).isdigit() else f"{nr}/{ano}"


def chave_digitos(numero: str | None) -> str | None:
    """Chave de comparação com o Diário: só dígitos do número + ano ('4.724/2020' e '4724/2020' coincidem)."""
    m = re.search(r"(\d[\d.\s]*)\s*/\s*(\d{4})\b", numero or "")
    if not m:
        return None
    nr = re.sub(r"\D", "", m.group(1)).lstrip("0")
    return f"{nr}/{m.group(2)}" if nr else None


def _turma(sub: str | None) -> str | None:
    m = re.match(r"\s*(\d{1,2})\s*[ªaºo°]?\s*(?:TURMA|C[ÂA]MARA)", (sub or "").upper())
    return str(int(m.group(1))) if m else None


def orgao_pr(descricao: str, sub: str | None) -> tuple[str, str, str]:
    """(orgao_raw, sigla, norm) pelo que o site informa em ``orgao`` e ``subOrgao`` (nunca por palavra do texto)."""
    desc = (descricao or "").strip()
    raw = f"{desc} / {sub.strip()}" if sub and sub.strip() else desc
    up = _sem_acento(desc).upper()
    n = _turma(sub)
    if "ETICA" in up:
        if n:
            return raw, f"OABPR-TED-T{n}", f"Tribunal de Ética e Disciplina da OAB/PR/{n}ª Turma Disciplinar"
        return raw, "OABPR-TED", "Tribunal de Ética e Disciplina da OAB/PR"
    chaves = (("DISCIPLINA", "CD", "Câmara de Disciplina"), ("SELECAO", "CS", "Câmara de Seleção"),
              ("PRERROGATIVAS", "CDP", "Câmara de Direitos e Prerrogativas"), ("ESPECIAL", "CE", "Câmara Especial"),
              ("CONSELHO", "CONSELHO", "Conselho Seccional"))
    for chave, sigla, nome in chaves:
        if chave in up:
            return raw, f"OABPR-{sigla}", f"Conselho Seccional - PR/{nome}"
    return raw, "OABPR", f"Conselho Seccional - PR/{desc.title()}" if desc else "Conselho Seccional - PR"


def _disciplinar(orgao: str, assunto: str, texto: str) -> int:
    """TED e Câmara de Disciplina: 1. Seleção e Direitos e Prerrogativas: 0. Demais: o que a regra do Diário disser."""
    up = _sem_acento(orgao).upper()
    if "ETICA" in up or "DISCIPLINA" in up:
        return 1
    if "SELECAO" in up or "PRERROGATIVAS" in up:
        return 0
    return 1 if eh_materia_disciplinar(orgao, assunto, texto) else 0


def _agente_publico(item: dict[str, Any], nome: str | None) -> bool:
    """O campo ``advogado`` repete o relator/revisor/presidente em ~30% dos acórdãos: aí não é parte, é agente público."""
    if not nome:
        return False
    rx = _padrao_nome(nome)
    papeis = [item.get("relator"), item.get("relatorAcordao"), item.get("revisor"), (item.get("presidente") or {}).get("nome")]
    return bool(rx and any(p and rx.search(_sem_acento(p)) for p in papeis))


def _chave_ementa(item: dict[str, Any]) -> tuple[str | None, str]:
    return chave_processo(item), " ".join(_limpar(item.get("ementa")).split()).lower()


def _rejeita(item: dict[str, Any], motivo: str) -> None:
    MOTIVOS[str(item["id"])] = motivo


# Marca de matéria disciplinar para órgãos fora de TED/Disciplina/Seleção/Prerrogativas: regra explícita antes da função do Diário.
RE_ELEICAO = re.compile(r"ELEICAO|ELEICOES|ELEITORA|CHAPA\b")
RE_DISC_EXPLICITA = re.compile(
    r"REVISAO\s+(?:DE\s+|DO\s+|DA\s+)?(?:PROCESSO|PENA|CONDENACAO|SANCAO|DECISAO)|PEDIDO\s+DE\s+REVISAO|"
    r"\bEXCLUSAO\b|ARTIGO\s+38\b|ART\.?\s*38\b|REABILITA"
)


def _marca(desc: str, assunto: str, ementa: str, dispositivo: str) -> int:
    up = _sem_acento(desc).upper()
    if "ETICA" in up or "DISCIPLINA" in up:
        return 1
    if "SELECAO" in up or "PRERROGATIVAS" in up:
        return 0
    abertura = _sem_acento(f"{assunto} {ementa[:600]}").upper()
    if RE_ELEICAO.search(_sem_acento(assunto).upper()) or RE_ELEICAO.search(abertura[:200]):
        return 0
    if RE_DISC_EXPLICITA.search(abertura):
        return 1
    return 1 if eh_materia_disciplinar(desc, assunto, f"{ementa}\n{dispositivo}") else 0


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    ident = str(item["id"])
    adv = (item.get("advogado") or {}).get("nome")

    # O texto entra como a OAB/PR publica: nenhum nome é abreviado, mascarado ou retirado (regra do projeto).
    def m(s: str | None) -> str:
        return _limpar(s)

    ementa = m(item.get("ementa"))
    if not ementa:
        _rejeita(item, "ementa vazia")
        return None
    desc = ((item.get("orgao") or {}).get("descricao") or "").strip()
    if "COLEGIO DE PRESIDENTES" in _sem_acento(desc).upper():
        _rejeita(item, "colegio de presidentes (cartas, nao e acordao)")
        return None
    if ident in _DUPLICADOS:
        _rejeita(item, "duplicata (mesmo processo e mesma ementa)")
        return None
    dispositivo = m(item.get("titulo"))
    assunto = m(item.get("assunto"))
    ementa_bruta = (item.get("ementa") or "").replace("\r\n", "\n")  # como veio da API
    sub = ((item.get("subOrgao") or {}).get("descricao") or "").strip() or None
    raw, sigla, norm = orgao_pr(desc, sub)
    ano_ac = item.get("anoAcordao")
    nr_ac = item.get("nrAcordao")
    rel = _limpar(item.get("relatorAcordao") or item.get("relator")) or None
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa,
        acordao=dispositivo,
        inteiro_teor="\n\n".join(t for t in (ementa, dispositivo) if t),
        url="https://celulaadv.oabpr.org.br/oab-jurisprudencia/",
        orgao_raw=raw, sigla_orgao=sigla, orgao_norm=norm,
        data_decisao=_data(item.get("dtJulgamento")),
        numero_processo=chave_processo(item),
        numero_ementa=str(nr_ac) if nr_ac not in (None, "") else None,
        relator=rel,
        consulta="ESPECIAL" in _sem_acento(desc).upper(),
        publicacao="Site da OAB/PR - Jurisprudência",
    )
    if ano_ac:
        rec["ano_ementa"] = int(ano_ac)
    rec["pdf_url"] = None
    rec["tem_inteiro_teor"] = 0  # ementa + dispositivo; não há voto
    rec["texto_publicado"] = ementa_bruta
    rec["is_ted_disciplinar"] = _marca(desc, assunto, ementa, dispositivo)
    if _DIARIO is not None and sobrepoe(rec, _DIARIO):
        _rejeita(item, "sobreposicao DEOAB_PR")
        return None
    # o campo ``advogado`` repete o relator/revisor em parte dos acórdãos: aí não é parte
    if adv and not _agente_publico(item, adv):
        rec["partes_json"] = json.dumps([{"nome": adv.strip(), "papel": "advogado representado"}], ensure_ascii=False)
    return rec


# ---------------------------------------------------------------------------------------------------------------------
# Sobreposição com o Diário
# ---------------------------------------------------------------------------------------------------------------------

def chaves_diario(db_path: Path = DB_PATH) -> set[str]:
    """Chaves (dígitos do processo + ano) de tudo o que já está em DEOAB_PR."""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        linhas = conn.execute("SELECT numero_processo FROM julgados WHERE fonte = 'DEOAB_PR' AND numero_processo IS NOT NULL").fetchall()
    return {k for (n,) in linhas if (k := chave_digitos(n))}


_DIARIO: set[str] | None = None
_DUPLICADOS: set[str] = set()
MOTIVOS: dict[str, str] = {}


def duplicados(itens: list[dict[str, Any]]) -> set[str]:
    """IDs a descartar: dentro de cada grupo (mesmo processo + mesma ementa) fica só o de menor ``ind``."""
    grupos: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for it in itens:
        proc, ement = _chave_ementa(it)
        if ement:
            grupos.setdefault((proc, ement), []).append(it)
    saida: set[str] = set()
    for g in grupos.values():
        g.sort(key=lambda x: int(x["ind"]))
        saida.update(str(x["id"]) for x in g[1:])
    return saida


def preparar(db_path: Path = DB_PATH) -> None:
    """Chamado pelo motor antes de normalizar: lê as chaves do Diário e zera os motivos de rejeição."""
    global _DIARIO
    _DIARIO = chaves_diario(db_path)
    MOTIVOS.clear()
    arq = _raw() / "itens.jsonl"
    _DUPLICADOS.clear()
    if arq.exists():
        with arq.open(encoding="utf-8") as fh:
            todos = [json.loads(ln) for ln in fh if ln.strip()]
        _DUPLICADOS.update(duplicados(todos))


def sobrepoe(rec: dict[str, Any], diario: set[str]) -> bool:
    """O acórdão já está no Diário: mesma chave de processo e julgamento na janela em que o Diário publica."""
    k = chave_digitos(rec.get("numero_processo"))
    return bool(k and k in diario and (rec.get("data_decisao") or "") >= INICIO_DIARIO)
