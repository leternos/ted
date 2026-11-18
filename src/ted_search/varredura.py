"""Varredura sistemática das 27 Seccionais no Diário Eletrônico da OAB (DEOAB).

Cada Seccional passa por cinco etapas, todas retomáveis e verificáveis:

    perfil   -> censo de dias-amostra: quais tipos de publicação são decisão e quais termos as encontram
    coletar  -> índice por janelas (mês; dia quando o mês é grande) + inspeção de cada publicação
    carregar -> normaliza e substitui, no banco, os registros DEOAB daquela Seccional
    auditar  -> censo independente de dias sorteados, comparado com o que foi coletado e carregado
    cobertura-> livro-razão (tabela cobertura_coleta): esperado x indexado x inspecionado x no banco

O princípio é contábil: nada é dado como completo sem que o total informado pela API
(`quantidade`) feche com o que foi indexado, inspecionado e gravado.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import random
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ted_search.collectors.deoab_nacional import (
    UF_TO_ORG,
    eh_eleitoral,
    eh_materia_disciplinar,
    motivo_recusa,
    normalize_deoab_item_nacional,
    save_records_to_database,
)
from ted_search.collectors.deoab_sp import (
    DEOAB_PAGE_SIZE,
    _get_json_with_retry,
    _slim_publication,
    html_to_paragraphs,
    normalize_deoab_sp_item,
)
from ted_search.config import DATA_DIR, DB_PATH, DEOAB_API_BASE_URL, RAW_DIR, create_async_http_client
from ted_search.db import get_connection, rebuild_fts

PERFIS_DIR = DATA_DIR / "perfis"
ANO_INICIAL = 2019  # primeiro ano do DEOAB
TERMOS_CANDIDATOS = ("ACORDAO", "EMENTA", "DECISAO", "JULGAMENTO", "RECURSO")
TERMOS_PADRAO = ("ACORDAO", "EMENTA")  # sem perfil validado, usa-se a união (mais lenta, mais segura)
TIPOS_DECISAO_PADRAO = ("ACÓRDÃO", "ACORDAO", "EMENTA")
TERMO_CENSO = "TODAS"  # perfil com "modo": "censo": enumera todas as publicações da Seccional, sem termo de busca
LIMITE_JANELA_MES = 300  # acima disso o mês é indexado dia a dia (consultas grandes são lentas no DEOAB)
SEARCH_URL = f"{DEOAB_API_BASE_URL}/diario/pesquisa"

COBERTURA_SCHEMA = """
CREATE TABLE IF NOT EXISTS cobertura_coleta (
    uf TEXT NOT NULL,
    fonte TEXT NOT NULL,
    ano INTEGER NOT NULL,
    esperado_json TEXT,          -- total informado pela API por termo de busca
    candidatos INTEGER,          -- publicações distintas encontradas pelo índice
    inspecionados INTEGER,       -- publicações baixadas e classificadas por tipo
    decisoes INTEGER,            -- publicações cujo tipo é decisão (perfil da Seccional)
    rejeitados INTEGER,          -- decisões que o normalizador recusou (ver rejeitados.jsonl)
    no_banco INTEGER,            -- registros dessa fonte/ano em julgados
    fundidos INTEGER,            -- decisões já presentes por outra fonte (mesma diario_url)
    janelas_parciais INTEGER,    -- janelas em que o índice não fechou com o total da API
    falhas INTEGER,              -- publicações que não puderam ser baixadas
    status TEXT,                 -- COMPLETO | PARCIAL | PENDENTE
    auditoria_json TEXT,         -- resultado da última auditoria independente
    atualizado_em TEXT,
    PRIMARY KEY (uf, fonte, ano)
);
"""


# ----------------------------------------------------------------------------- utilidades


def _sec(uf: str) -> dict[str, Any]:
    sec = UF_TO_ORG.get(uf.upper())
    if not sec:
        raise ValueError(f"UF inválida: {uf}")
    return sec


def _fonte(uf: str) -> str:
    return f"DEOAB_{uf.upper()}"


def raw_dir_uf(uf: str, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"deoab_{uf.lower()}"


def anos_padrao() -> list[int]:
    return list(range(ANO_INICIAL, date.today().year + 1))


def carregar_perfil(uf: str) -> dict[str, Any]:
    """Perfil da Seccional (data/perfis/UF.json). Sem arquivo, devolve o padrão conservador."""
    uf = uf.upper()
    path = PERFIS_DIR / f"{uf}.json"
    perfil: dict[str, Any] = {
        "uf": uf,
        "id_organizacao": _sec(uf)["id_organizacao"],
        "termos": list(TERMOS_PADRAO),
        "tipos_decisao": list(TIPOS_DECISAO_PADRAO),
        "validado": False,
    }
    if path.exists():
        perfil.update(json.loads(path.read_text(encoding="utf-8")))
    perfil["tipos_decisao"] = sorted({t.upper() for t in perfil["tipos_decisao"]})
    return perfil


def termos_de_coleta(perfil: dict[str, Any]) -> list[str]:
    """No modo censo a coleta ignora os termos e enumera tudo; é o modo seguro para Seccionais pequenas."""
    return [TERMO_CENSO] if perfil.get("modo") == "censo" else list(perfil["termos"])


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _buscar_janela(
    client: Any, sem: asyncio.Semaphore, org_id: int, termo: str | None, inicio: date, fim: date, so_total: bool = False
) -> tuple[int, set[int], bool]:
    """Percorre todas as páginas de uma janela. Devolve (total da API, ids, ok)."""
    params = {"inicio": _fmt(inicio), "fim": _fmt(fim), "idOrganizacao": str(org_id)}
    if termo:
        params["termo"] = termo
    first = await _get_json_with_retry(client, SEARCH_URL, {**params, "pagina": "1"}, sem)
    if first is None:
        return 0, set(), False
    total = int(first.get("quantidade") or 0)
    ids = {int(it["id"]) for it in (first.get("itens") or []) if it.get("id")}
    if so_total or total <= DEOAB_PAGE_SIZE:
        return total, ids, True

    async def _page(p: int) -> bool:
        for _ in range(2):
            data = await _get_json_with_retry(client, SEARCH_URL, {**params, "pagina": str(p)}, sem)
            itens = (data or {}).get("itens") or []
            if itens:
                ids.update(int(it["id"]) for it in itens if it.get("id"))
                return True
        return False

    n_pages = -(-total // DEOAB_PAGE_SIZE)
    results = await asyncio.gather(*(_page(p) for p in range(2, n_pages + 1)))
    return total, ids, all(results)


async def _baixar_publicacao(client: Any, sem: asyncio.Semaphore, pub_id: int) -> dict[str, Any] | None:
    data = await _get_json_with_retry(client, f"{DEOAB_API_BASE_URL}/publicacao/{pub_id}/publicado", None, sem)
    if not data:
        return None
    if isinstance(data.get("objeto"), dict):
        data = data["objeto"]
    return data


def _tipo(pub: dict[str, Any]) -> str:
    return ((pub.get("tipoPublicacao") or {}).get("descricao") or "").strip()


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.upper()) if unicodedata.category(c) != "Mn")


TITULOS_DECISAO_PADRAO = ("ACORDAO", "EMENTA")
# "Vistos, relatados e discutidos" e variantes ("Vistos e analisados os autos"), em texto sem acento
RE_FORMULA_JULGAMENTO = re.compile(r"VISTOS,?\s+(?:E\s+)?(?:RELATADOS|ANALISADOS|EXAMINADOS)")
# Marcas de que o texto traz o resultado de um julgamento, usadas só para confirmar tipos amplos
RE_RESULTADO_NO_CORPO = re.compile(r"UNANIMIDADE|POR\s+MAIORIA|JULGO\s+(?:IM)?PROCEDENTE|ACOMPANHO\s+O\s+VOTO|NEGO\s+PROVIMENTO|DOU\s+PROVIMENTO")
# Publicação que abre anunciando o resultado de uma sessão de julgamento (TO, nov/2026)
RE_RESULTADO_SESSAO = re.compile(r"RESULTADO\s+D[EO]\s+JULGAMENTO")


def via_decisao(pub: dict[str, Any], perfil: dict[str, Any]) -> str | None:
    """Por qual via a publicação é decisão: "tipo", "tipo_amplo", "titulo", "corpo" — ou None se não é.

    O tipo sozinho não basta: há Seccionais que publicam "INTIMAÇÃO DE ACÓRDÃO" com ementa e acórdão
    completos sob os tipos "Retificação" ou "Notificação" (medido em AL, nov/2026). E há tipos amplos
    que o perfil inclui porque às vezes trazem decisão ("Edital" no MA): esses saem como "tipo_amplo"
    e só viram registro se o conteúdo confirmar (ver normalizar_decisoes).
    """
    if pub.get("_skip"):  # formato antigo: descartada só pelo tipo, sem conteúdo guardado
        return None
    titulo = _sem_acento(pub.get("titulo") or "")
    if any(t in titulo for t in perfil.get("titulos_decisao", TITULOS_DECISAO_PADRAO)):
        return "titulo"
    corpo = _sem_acento(html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or ""))
    # Fórmulas de julgamento colegiado: ementa + "acordam", ou "vistos, relatados e discutidos"
    # (TO publica os resultados de sessão como "Edital de Notificação", sem ementa)
    tipo = _tipo(pub).upper()
    # Despacho é ato monocrático: a fórmula "Vistos e analisados" dentro dele não o torna decisão colegiada (AP, nov/2026)
    if "DESPACHO" not in tipo and (
        ("EMENTA" in corpo and "ACORDAM" in corpo) or RE_FORMULA_JULGAMENTO.search(corpo) or RE_RESULTADO_SESSAO.search(corpo[:200])
    ):
        return "corpo"
    if tipo in perfil["tipos_decisao"]:
        nuclear = any(k in _sem_acento(tipo) for k in ("ACORD", "EMENTA", "DECIS", "JULGAMENTO"))
        return "tipo" if nuclear else "tipo_amplo"
    return None


def eh_decisao(pub: dict[str, Any], perfil: dict[str, Any]) -> bool:
    return via_decisao(pub, perfil) is not None


# ----------------------------------------------------------------------------- 1. perfil (censo)


def _parece_decisao(texto: str) -> bool:
    """Heurística usada só no censo, para sugerir ao revisor quais tipos são decisórios."""
    t = texto.lower()
    formula = any(k in t for k in ("acordam", "acórdão", "acordao", "ementa"))
    desfecho = any(
        k in t
        for k in ("provimento", "procedente", "procedência", "improced", "censura", "suspensão", "exclusão", "arquiv", "prescri")
    )
    return formula and desfecho


async def censo_do_dia(
    client: Any, sem: asyncio.Semaphore, org_id: int, dia: date, termos: tuple[str, ...] = TERMOS_CANDIDATOS, limite: int = 600,
    perfil: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Baixa TODAS as publicações da Seccional em um dia (sem termo) e mede o que cada termo encontra."""
    total, ids, ok = await _buscar_janela(client, sem, org_id, None, dia, dia)
    amostrado = sorted(ids)
    if len(amostrado) > limite:
        passo = len(amostrado) / limite
        amostrado = [amostrado[int(i * passo)] for i in range(limite)]
    pubs = await asyncio.gather(*(_baixar_publicacao(client, sem, i) for i in amostrado))
    por_termo: dict[str, set[int]] = {}
    for termo in termos:
        _, t_ids, _ = await _buscar_janela(client, sem, org_id, termo, dia, dia)
        por_termo[termo] = t_ids

    itens = []
    for pid, pub in zip(amostrado, pubs):
        if not pub:
            continue
        texto = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
        itens.append(
            {
                "id": pid,
                "tipo": _tipo(pub) or "?",
                "setor": ((pub.get("setorPublicacao") or {}).get("nomeAlternativo") or "").strip(),
                "titulo": (pub.get("titulo") or "").strip()[:140],
                "trecho": " ".join(texto.split())[:260],
                "parece_decisao": _parece_decisao(texto),
                "eh_decisao": eh_decisao(pub, perfil) if perfil else None,
                "termos": [t for t, s in por_termo.items() if pid in s],
            }
        )
    return {"dia": dia.isoformat(), "total_api": total, "indice_ok": ok, "baixadas": len(itens), "itens": itens}


async def _dias_com_publicacao(
    client: Any, sem: asyncio.Semaphore, org_id: int, n: int, seed: int | None, anos: list[int] | None = None
) -> list[date]:
    """Escolhe n dias com publicações (um por mês sorteado), entre 2019 e hoje ou só nos anos dados."""
    rng = random.Random(seed)
    hoje = date.today()
    meses = [(y, m) for y in (anos or range(ANO_INICIAL, hoje.year + 1)) for m in range(1, 13) if date(y, m, 1) <= hoje]
    rng.shuffle(meses)
    dias: list[date] = []
    for y, m in meses:
        if len(dias) >= n:
            break
        ini, fim = date(y, m, 1), min(date(y, m, calendar.monthrange(y, m)[1]), hoje)
        # Sem termo de busca, para o censo não herdar o viés do próprio índice que ele vai avaliar
        base = {"inicio": _fmt(ini), "fim": _fmt(fim), "idOrganizacao": str(org_id)}
        primeira = await _get_json_with_retry(client, SEARCH_URL, {**base, "pagina": "1"}, sem)
        total = int((primeira or {}).get("quantidade") or 0)
        if not total:
            continue
        pagina = rng.randint(1, -(-total // DEOAB_PAGE_SIZE))
        data = primeira if pagina == 1 else await _get_json_with_retry(client, SEARCH_URL, {**base, "pagina": str(pagina)}, sem)
        achados: set[date] = set()
        for it in (data or {}).get("itens") or []:
            d = (it.get("dataPublicacao") or "")[:10]
            if d:
                achados.add(date.fromisoformat(d))
        if achados:
            dias.append(rng.choice(sorted(achados)))
    return sorted(dias)


async def perfilar_seccional(uf: str, n_dias: int = 6, concurrency: int = 10, seed: int = 7) -> dict[str, Any]:
    """Gera a proposta de perfil da Seccional a partir de um censo de dias-amostra.

    O resultado é gravado em data/perfis/UF.json com ``validado: false``: quem revisa as amostras
    (o agente batedor ou uma pessoa) confirma ``tipos_decisao`` e ``termos`` e marca ``validado: true``.
    """
    uf = uf.upper()
    org_id = _sec(uf)["id_organizacao"]
    sem = asyncio.Semaphore(concurrency)
    async with create_async_http_client(timeout=40.0) as client:
        dias = await _dias_com_publicacao(client, sem, org_id, n_dias, seed)
        censos = [await censo_do_dia(client, sem, org_id, d) for d in dias]

    itens = [it for c in censos for it in c["itens"]]
    por_tipo: dict[str, dict[str, Any]] = {}
    for it in itens:
        t = por_tipo.setdefault(it["tipo"], {"publicacoes": 0, "parecem_decisao": 0, "setores": Counter(), "amostras": []})
        t["publicacoes"] += 1
        t["parecem_decisao"] += int(it["parece_decisao"])
        t["setores"][it["setor"]] += 1
        if len(t["amostras"]) < 3:
            t["amostras"].append({"id": it["id"], "titulo": it["titulo"], "trecho": it["trecho"]})
    for t in por_tipo.values():
        t["setores"] = dict(t["setores"].most_common(6))

    # Um tipo entra como decisório pelo nome (variações de Acórdão/Ementa) ou pelo conteúdo da amostra
    tipos_decisao = sorted(
        {t.upper() for t in TIPOS_DECISAO_PADRAO}
        | {nome.upper() for nome in por_tipo if any(k in nome.upper() for k in ("ACÓRD", "ACORD", "EMENTA"))}
        | {nome.upper() for nome, t in por_tipo.items() if t["publicacoes"] >= 3 and t["parecem_decisao"] / t["publicacoes"] >= 0.6}
    )
    decisoes = [it for it in itens if it["tipo"].upper() in tipos_decisao]
    # Menor conjunto de termos que encontra todas as decisões do censo (cobertura gulosa)
    faltam = {it["id"] for it in decisoes}
    termos: list[str] = []
    while faltam:
        melhor = max(TERMOS_CANDIDATOS, key=lambda t: sum(1 for it in decisoes if it["id"] in faltam and t in it["termos"]))
        ganho = {it["id"] for it in decisoes if it["id"] in faltam and melhor in it["termos"]}
        if not ganho:
            break
        termos.append(melhor)
        faltam -= ganho
    recall = {t: round(sum(1 for it in decisoes if t in it["termos"]) / len(decisoes), 4) if decisoes else None for t in TERMOS_CANDIDATOS}

    perfil = {
        "uf": uf,
        "id_organizacao": org_id,
        "termos": termos or list(TERMOS_PADRAO),
        "tipos_decisao": tipos_decisao,
        "validado": False,
        "evidencia": {
            "gerado_em": _agora(),
            "dias_censo": [{"dia": c["dia"], "publicacoes": c["total_api"], "baixadas": c["baixadas"]} for c in censos],
            "decisoes_no_censo": len(decisoes),
            "decisoes_que_nenhum_termo_encontra": sorted(faltam),
            "recall_por_termo": recall,
            "tipos": {k: por_tipo[k] for k in sorted(por_tipo, key=lambda k: -por_tipo[k]["publicacoes"])},
        },
    }
    PERFIS_DIR.mkdir(parents=True, exist_ok=True)
    (PERFIS_DIR / f"{uf}.json").write_text(json.dumps(perfil, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return perfil


# ----------------------------------------------------------------------------- 2. coleta


async def _indexar_mes(
    client: Any, sem: asyncio.Semaphore, org_id: int, ano: int, mes: int, termos: list[str]
) -> dict[str, Any]:
    hoje = date.today()
    ini, fim = date(ano, mes, 1), min(date(ano, mes, calendar.monthrange(ano, mes)[1]), hoje)
    esperado: dict[str, int] = {}
    indexados: dict[str, int] = {}
    ids: set[int] = set()
    ok = True
    modo = "mes"
    for termo in termos:
        busca = None if termo == TERMO_CENSO else termo  # modo censo: sem termo, todas as publicações
        total, _, t_ok = await _buscar_janela(client, sem, org_id, busca, ini, fim, so_total=True)
        esperado[termo] = total
        ok = ok and t_ok
        if total == 0:
            indexados[termo] = 0
            continue
        if total <= LIMITE_JANELA_MES:
            _, t_ids, t_ok = await _buscar_janela(client, sem, org_id, busca, ini, fim)
        else:
            modo = "dias"
            dias = [ini + timedelta(days=i) for i in range((fim - ini).days + 1)]
            partes = await asyncio.gather(*(_buscar_janela(client, sem, org_id, busca, d, d) for d in dias))
            t_ids = set().union(*(p[1] for p in partes))
            t_ok = all(p[2] for p in partes)
        indexados[termo] = len(t_ids)
        ids |= t_ids
        ok = ok and t_ok
    fechou = ok and all(indexados[t] == esperado[t] for t in termos)
    return {"modo": modo, "esperado": esperado, "indexados": indexados, "ids": sorted(ids), "fechou": fechou, "indexado_em": _agora()}


async def coletar_ano(
    client: Any, sem: asyncio.Semaphore, uf: str, ano: int, perfil: dict[str, Any], raw_dir: Path = RAW_DIR
) -> dict[str, Any]:
    """Indexa e inspeciona um ano de uma Seccional. Retomável: refaz só o que não fechou."""
    uf = uf.upper()
    org_id = perfil["id_organizacao"]
    out = raw_dir_uf(uf, raw_dir)
    out.mkdir(parents=True, exist_ok=True)
    index_file, jsonl = out / f"{ano}.index.json", out / f"{ano}.jsonl"
    index: dict[str, Any] = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    termos = termos_de_coleta(perfil)
    hoje = date.today()

    for mes in range(1, 13):
        if date(ano, mes, 1) > hoje:
            break
        chave = f"{ano}-{mes:02d}"
        atual = index.get(chave)
        recente = (hoje - date(ano, mes, calendar.monthrange(ano, mes)[1])).days <= 5  # mês em curso ou recém-fechado
        if atual and atual.get("fechou") and sorted(atual.get("esperado", {})) == sorted(termos) and not recente:
            continue
        index[chave] = await _indexar_mes(client, sem, org_id, ano, mes, termos)
        index_file.write_text(json.dumps(index), encoding="utf-8")

    # Linhas do formato antigo ("_skip" sem conteúdo) são descartadas e baixadas de novo: sem o conteúdo
    # não dá para reavaliar a publicação quando a regra de decisão ou o perfil mudam.
    inspecionados: set[int] = set()
    if jsonl.exists():
        linhas = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        boas = [r for r in linhas if "_skip" not in r]
        if len(boas) != len(linhas):
            jsonl.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in boas), encoding="utf-8")
        inspecionados = {int(r["id"]) for r in boas}
    candidatos = {i for m in index.values() for i in m["ids"]}
    pendentes = sorted(candidatos - inspecionados)
    falhas: list[int] = []
    lock = asyncio.Lock()
    with jsonl.open("a", encoding="utf-8") as fh:

        async def _um(pid: int) -> None:
            pub = await _baixar_publicacao(client, sem, pid)
            if not pub:
                falhas.append(pid)
                return
            row = _slim_publication(pub)  # guarda TODAS as inspecionadas; decisão ou não se decide na leitura
            row["id"] = pid
            async with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        for start in range(0, len(pendentes), 400):
            await asyncio.gather(*(_um(pid) for pid in pendentes[start : start + 400]))
            fh.flush()
            if len(pendentes) > 800:
                print(f"[varredura] {uf} {ano}: {min(start + 400, len(pendentes))}/{len(pendentes)} publicações inspecionadas...", flush=True)

    resumo = resumir_ano(uf, ano, raw_dir)
    resumo["publicacoes_com_falha"] = falhas
    (out / f"{ano}.resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumo


def resumir_ano(uf: str, ano: int, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Recalcula, a partir dos arquivos brutos, os números de um ano (aceita o formato antigo da coleta de SP)."""
    out = raw_dir_uf(uf, raw_dir)
    index_file, jsonl = out / f"{ano}.index.json", out / f"{ano}.jsonl"
    esperado: Counter[str] = Counter()
    candidatos: set[int] = set()
    parciais: list[str] = []
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))
        for chave, m in index.items():
            if isinstance(m, list):  # formato antigo (SP): lista de ids por dia; totais ficam no .summary.json
                candidatos.update(m)
                continue
            candidatos.update(m["ids"])
            esperado.update(m["esperado"])
            if not m.get("fechou"):
                parciais.append(chave)
    antigo = out / f"{ano}.summary.json"
    falhas_previas: list[int] = []
    if antigo.exists():
        s = json.loads(antigo.read_text(encoding="utf-8"))
        esperado.update(s.get("esperado_por_termo") or {})
        parciais += [str(p) for p in s.get("paginas_com_falha") or []]
        falhas_previas = list(s.get("publicacoes_com_falha") or [])
    inspecionados = decisoes = 0
    descartados: Counter[str] = Counter()
    perfil = carregar_perfil(uf)
    if jsonl.exists():
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                inspecionados += 1
                if eh_decisao(row, perfil):
                    decisoes += 1
                else:
                    descartados[row.get("_skip") or _tipo(row) or "?"] += 1
    return {
        "uf": uf.upper(),
        "ano": ano,
        "esperado_por_termo": dict(esperado),
        "candidatos": len(candidatos),
        "inspecionados": inspecionados,
        "decisoes": decisoes,
        "descartados_por_tipo": dict(descartados.most_common()),
        "janelas_parciais": parciais,
        "publicacoes_com_falha": falhas_previas,
    }


async def coletar_seccional(uf: str, anos: list[int] | None = None, concurrency: int = 10, raw_dir: Path = RAW_DIR) -> list[dict[str, Any]]:
    uf = uf.upper()
    perfil = carregar_perfil(uf)
    sem = asyncio.Semaphore(concurrency)
    resumos = []
    async with create_async_http_client(timeout=40.0) as client:
        for ano in sorted(anos or anos_padrao()):
            r = await coletar_ano(client, sem, uf, ano, perfil, raw_dir)
            resumos.append(r)
            print(
                f"[varredura] {uf} {ano}: esperado {r['esperado_por_termo']} | candidatos {r['candidatos']} | "
                f"decisões {r['decisoes']} | parciais {len(r['janelas_parciais'])} | falhas {len(r['publicacoes_com_falha'])}",
                flush=True,
            )
    return resumos


# ----------------------------------------------------------------------------- 3. carga no banco


def ler_decisoes(uf: str, anos: list[int] | None = None, raw_dir: Path = RAW_DIR) -> list[dict[str, Any]]:
    """Publicações coletadas que são decisão segundo o perfil ATUAL da Seccional."""
    out = raw_dir_uf(uf, raw_dir)
    perfil = carregar_perfil(uf)
    pubs: dict[int, dict[str, Any]] = {}
    for ano in anos or anos_padrao():
        jsonl = out / f"{ano}.jsonl"
        if not jsonl.exists():
            continue
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    if eh_decisao(row, perfil):
                        pubs[int(row["id"])] = row
    return list(pubs.values())


def ids_inspecionados(uf: str, raw_dir: Path = RAW_DIR) -> set[int]:
    """Ids de todas as publicações já baixadas e classificadas (decisão ou não) de uma Seccional."""
    ids: set[int] = set()
    for jsonl in raw_dir_uf(uf, raw_dir).glob("[0-9][0-9][0-9][0-9].jsonl"):
        with jsonl.open(encoding="utf-8") as fh:
            ids.update(int(json.loads(line)["id"]) for line in fh if line.strip())
    return ids


def normalizar_decisoes(uf: str, pubs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Devolve (registros, rejeitados). Nenhuma decisão some em silêncio: a recusa fica registrada."""
    uf = uf.upper()
    org_id = _sec(uf)["id_organizacao"]
    recs, rejeitados = [], []
    perfil = carregar_perfil(uf)
    for pub in pubs:
        if uf != "SP" and via_decisao(pub, perfil) == "tipo_amplo":
            # Tipo amplo (ex.: "Edital"): só entra se o conteúdo for de matéria ético-disciplinar
            texto = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
            setor = ((pub.get("setorPublicacao") or {}).get("nomeAlternativo") or "")
            # ...ou trouxer o resultado de um julgamento (atas de sessão e decisões eleitorais de RR, nov/2026)
            plano = _sem_acento(texto)
            tem_resultado = bool(RE_RESULTADO_NO_CORPO.search(plano))
            if "ATA" in _sem_acento(_tipo(pub)).split():
                # Ata só entra se for de sessão de julgamento (não de reunião de diretoria ou de conselho)
                aceita = tem_resultado and bool(re.search(r"SESSAO\s+DE\s+JULGAMENTO|TRIBUNAL\s+DE\s+ETICA", plano[:400]))
            else:
                aceita = eh_materia_disciplinar(setor, pub.get("titulo") or "", texto) or (eh_eleitoral(setor, texto) and tem_resultado)
            if not aceita:
                rejeitados.append({"id": pub["id"], "motivo": "tipo_amplo_sem_decisao", "tipo": _tipo(pub), "titulo": (pub.get("titulo") or "")[:140],
                                   "chars": len(texto), "data": (pub.get("dataPublicacao") or "")[:10]})
                continue
        rec = (
            normalize_deoab_sp_item(pub)
            if uf == "SP"
            else normalize_deoab_item_nacional(pub, default_uf=uf, default_org_id=org_id, confiar_no_tipo=True)
        )
        if rec:
            if uf != "SP":
                # Subseções e órgãos regionais publicam com outro idOrganizacao; a Seccional é a da busca
                rec.update({"fonte": _fonte(uf), "id": f"{_fonte(uf)}:{pub['id']}", "uf_origem": uf, "id_organizacao": org_id})
            recs.append(rec)
        else:
            texto = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
            rejeitados.append(
                {
                    "id": pub["id"], "motivo": "normalizador_sp" if uf == "SP" else motivo_recusa(pub, confiar_no_tipo=True),
                    "tipo": _tipo(pub), "titulo": (pub.get("titulo") or "")[:140], "chars": len(texto), "data": (pub.get("dataPublicacao") or "")[:10],
                }
            )
    return recs, rejeitados


def registrar_rejeitados(uf: str, raw_dir: Path = RAW_DIR) -> list[dict[str, Any]]:
    """Grava rejeitados.jsonl (decisões que o normalizador recusa) sem tocar o banco."""
    _, rejeitados = normalizar_decisoes(uf, ler_decisoes(uf, None, raw_dir))
    out = raw_dir_uf(uf, raw_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rejeitados.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejeitados), encoding="utf-8")
    return rejeitados


def carregar_seccional(uf: str, anos: list[int] | None = None, rebuild: bool = True, db_path: Path = DB_PATH, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Substitui no banco os registros DEOAB da Seccional pelos coletados (só nos anos em que a coleta é >= ao que havia)."""
    uf = uf.upper()
    fonte = _fonte(uf)
    anos = sorted(anos or anos_padrao())
    pubs = ler_decisoes(uf, anos, raw_dir)
    recs, rejeitados = normalizar_decisoes(uf, pubs)
    # Decisões brutas por ano: é contra elas que se mede se a coleta encolheu (o normalizador pode, legitimamente, recusar mais)
    brutas = Counter(int((p.get("dataPublicacao") or "0000")[:4] or 0) for p in pubs)
    inspecionados = ids_inspecionados(uf, raw_dir)
    out = raw_dir_uf(uf, raw_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rejeitados.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejeitados), encoding="utf-8")

    por_ano: dict[int, list[dict[str, Any]]] = {}
    for r in recs:
        por_ano.setdefault(r.get("ano_ementa") or 0, []).append(r)

    relatorio: dict[str, Any] = {"uf": uf, "normalizados": len(recs), "rejeitados": len(rejeitados), "anos": {}, "anos_preservados": []}
    with get_connection(db_path) as conn:
        conn.executescript(COBERTURA_SCHEMA)
        outras = {
            r["diario_url"]: r["id"]
            for r in conn.execute("SELECT id, diario_url FROM julgados WHERE fonte NOT LIKE 'DEOAB_%' AND diario_url IS NOT NULL")
        }
        a_inserir: list[dict[str, Any]] = []
        with conn:
            for ano in anos:
                novos = por_ano.get(ano, [])
                havia = conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = ? AND ano_ementa = ?", (fonte, ano)).fetchone()[0]
                # Só há perda se algum registro que já está no banco NÃO foi inspecionado nesta coleta.
                # Registro inspecionado e agora classificado como não decisão (ou recusado) pode sair.
                existentes = {
                    int(r[0].rsplit(":", 1)[-1])
                    for r in conn.execute("SELECT id FROM julgados WHERE fonte = ? AND ano_ementa = ?", (fonte, ano))
                    if r[0].rsplit(":", 1)[-1].isdigit()
                }
                nao_vistos = sorted(existentes - inspecionados)
                if nao_vistos:
                    relatorio["anos_preservados"].append({"ano": ano, "havia": havia, "coletados": brutas.get(ano, 0), "ids_nao_inspecionados": nao_vistos[:50]})
                    continue
                conn.execute(
                    "DELETE FROM julgado_dispositivos WHERE julgado_id IN (SELECT id FROM julgados WHERE fonte = ? AND ano_ementa = ?)", (fonte, ano)
                )
                conn.execute("DELETE FROM julgados WHERE fonte = ? AND ano_ementa = ?", (fonte, ano))
                fundidos = 0
                for rec in novos:
                    existente = outras.get(rec["diario_url"])
                    if existente:
                        conn.execute(
                            "UPDATE julgados SET inteiro_teor_texto = COALESCE(NULLIF(inteiro_teor_texto, ''), ?), tem_inteiro_teor = 1, "
                            "pdf_url = COALESCE(pdf_url, ?) WHERE id = ?",
                            (rec["inteiro_teor_texto"], rec["pdf_url"], existente),
                        )
                        fundidos += 1
                    else:
                        a_inserir.append(rec)
                relatorio["anos"][ano] = {"havia": havia, "coletados": len(novos), "fundidos": fundidos}
    save_records_to_database(a_inserir, db_path, rebuild_fts_after=False)
    relatorio["inseridos"] = len(a_inserir)
    if rebuild:
        with get_connection(db_path) as conn, conn:
            rebuild_fts(conn)
    sincronizar_cobertura(uf, db_path, raw_dir, fundidos={a: v["fundidos"] for a, v in relatorio["anos"].items()}, rejeitados=rejeitados)
    return relatorio


# ----------------------------------------------------------------------------- 4. livro-razão de cobertura


def sincronizar_cobertura(
    uf: str,
    db_path: Path = DB_PATH,
    raw_dir: Path = RAW_DIR,
    fundidos: dict[int, int] | None = None,
    rejeitados: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Atualiza cobertura_coleta para uma Seccional a partir dos arquivos brutos e do banco."""
    uf = uf.upper()
    fonte = _fonte(uf)
    out = raw_dir_uf(uf, raw_dir)
    if rejeitados is None and (out / "rejeitados.jsonl").exists():
        rejeitados = [json.loads(l) for l in (out / "rejeitados.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rej_ano = Counter(int(r["data"][:4]) for r in (rejeitados or []) if r.get("data"))
    linhas = []
    with get_connection(db_path) as conn:
        conn.executescript(COBERTURA_SCHEMA)
        # Decisões que já estavam no banco por outra fonte (mesma matéria do Diário) contam como fundidas
        outras = {
            int(r[0].rsplit("/", 1)[-1])
            for r in conn.execute("SELECT diario_url FROM julgados WHERE fonte NOT LIKE 'DEOAB_%' AND diario_url IS NOT NULL")
            if r[0].rsplit("/", 1)[-1].isdigit()
        }
        rej_ids = {int(r["id"]) for r in (rejeitados or [])}
        fund_calc: Counter[int] = Counter()
        if fundidos is None:
            for pub in ler_decisoes(uf, None, raw_dir):
                if int(pub["id"]) in outras and int(pub["id"]) not in rej_ids:
                    fund_calc[int((pub.get("dataPublicacao") or "0000")[:4] or 0)] += 1
        with conn:
            for ano in anos_padrao():
                if not (out / f"{ano}.jsonl").exists() and not (out / f"{ano}.index.json").exists():
                    continue
                r = resumir_ano(uf, ano, raw_dir)
                no_banco = conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = ? AND ano_ementa = ?", (fonte, ano)).fetchone()[0]
                anterior = conn.execute("SELECT fundidos, auditoria_json FROM cobertura_coleta WHERE uf = ? AND fonte = ? AND ano = ?", (uf, fonte, ano)).fetchone()
                fund = (fundidos or fund_calc).get(ano, 0)
                falhas = len(r["publicacoes_com_falha"])
                fecha_indice = not r["janelas_parciais"] and r["inspecionados"] == r["candidatos"] and falhas == 0
                fecha_banco = no_banco + fund + rej_ano.get(ano, 0) == r["decisoes"]
                status = "COMPLETO" if fecha_indice and fecha_banco else ("PARCIAL" if r["inspecionados"] else "PENDENTE")
                linha = {
                    "uf": uf, "fonte": fonte, "ano": ano, "esperado_json": json.dumps(r["esperado_por_termo"]), "candidatos": r["candidatos"],
                    "inspecionados": r["inspecionados"], "decisoes": r["decisoes"], "rejeitados": rej_ano.get(ano, 0), "no_banco": no_banco,
                    "fundidos": fund, "janelas_parciais": len(r["janelas_parciais"]), "falhas": falhas, "status": status,
                    "auditoria_json": anterior["auditoria_json"] if anterior else None, "atualizado_em": _agora(),
                }
                conn.execute(
                    "INSERT OR REPLACE INTO cobertura_coleta (uf, fonte, ano, esperado_json, candidatos, inspecionados, decisoes, rejeitados, no_banco, "
                    "fundidos, janelas_parciais, falhas, status, auditoria_json, atualizado_em) VALUES (:uf, :fonte, :ano, :esperado_json, :candidatos, "
                    ":inspecionados, :decisoes, :rejeitados, :no_banco, :fundidos, :janelas_parciais, :falhas, :status, :auditoria_json, :atualizado_em)",
                    linha,
                )
                linhas.append(linha)
    return linhas


def _situacao(rows: list[Any], status: Counter[str], auditorias: list[dict[str, Any]], perfil_validado: bool) -> str:
    """NÃO INICIADA -> EM ANDAMENTO -> AGUARDA AUDITORIA -> AGUARDA PERFIL -> FECHADA."""
    if not rows:
        return "NÃO INICIADA"
    if status.get("COMPLETO", 0) != len(rows):
        return "EM ANDAMENTO"
    if not auditorias or not auditorias[-1].get("aprovada"):
        return "AGUARDA AUDITORIA"
    return "FECHADA" if perfil_validado else "AGUARDA PERFIL"


def relatorio_cobertura(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Uma linha por Seccional: situação da varredura (inclui as que ainda não começaram)."""
    with get_connection(db_path) as conn:
        conn.executescript(COBERTURA_SCHEMA)
        linhas = []
        for uf in sorted(UF_TO_ORG):
            rows = conn.execute("SELECT * FROM cobertura_coleta WHERE uf = ? AND fonte = ? ORDER BY ano", (uf, _fonte(uf))).fetchall()
            no_banco = conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = ?", (_fonte(uf),)).fetchone()[0]
            status = Counter(r["status"] for r in rows)
            auditorias = [json.loads(r["auditoria_json"]) for r in rows if r["auditoria_json"]]
            linhas.append(
                {
                    "uf": uf,
                    "perfil_validado": bool(carregar_perfil(uf).get("validado")),
                    "anos_no_livro": len(rows),
                    "completos": status.get("COMPLETO", 0),
                    "parciais": status.get("PARCIAL", 0),
                    "decisoes_coletadas": sum(r["decisoes"] or 0 for r in rows),
                    "no_banco": no_banco,
                    "auditada": bool(auditorias),
                    "faltantes_na_auditoria": sum(a.get("faltantes_na_coleta", 0) for a in auditorias[-1:]),
                    "situacao": _situacao(rows, status, auditorias, bool(carregar_perfil(uf).get("validado"))),
                }
            )
    return linhas


# ----------------------------------------------------------------------------- 5. auditoria independente


async def auditar_seccional(uf: str, n_dias: int = 4, concurrency: int = 10, seed: int | None = None, db_path: Path = DB_PATH, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Refaz o censo (sem termos de busca) em dias sorteados e confere contra a coleta e o banco.

    É a prova independente de que o índice por termos não deixou decisão para trás.
    """
    uf = uf.upper()
    perfil = carregar_perfil(uf)
    org_id = perfil["id_organizacao"]
    coletadas: dict[str, set[int]] = {}
    for pub in ler_decisoes(uf, None, raw_dir):
        coletadas.setdefault((pub.get("dataPublicacao") or "")[:10], set()).add(int(pub["id"]))
    if not coletadas:
        raise SystemExit(f"[auditoria] {uf}: nada coletado ainda.")
    rng = random.Random(seed)
    dias_set = set(rng.sample(sorted(coletadas), min(n_dias, len(coletadas))))
    # Metade extra da amostra vem de dias com QUALQUER publicação, com peso nos anos mais magros:
    # é o que revela um ano em que a Seccional publicou decisões com outro tipo ou outro vocabulário.
    por_ano = Counter(int(d[:4]) for d, ids in coletadas.items() for _ in ids)
    magros = sorted(anos_padrao(), key=lambda a: por_ano.get(a, 0))[:3]

    sem = asyncio.Semaphore(concurrency)
    async with create_async_http_client(timeout=40.0) as client:
        extras = await _dias_com_publicacao(client, sem, org_id, max(2, n_dias // 2), seed, anos=magros)
        dias = sorted(dias_set | {d.isoformat() for d in extras})
        censos = [
            await censo_do_dia(client, sem, org_id, date.fromisoformat(d), termos=tuple(perfil["termos"]), limite=2000, perfil=perfil) for d in dias
        ]

    with get_connection(db_path) as conn:
        conn.executescript(COBERTURA_SCHEMA)
        resultado_dias = []
        faltam_coleta = faltam_banco = 0
        tipos_fora: Counter[str] = Counter()
        for c in censos:
            decisoes_censo = {it["id"] for it in c["itens"] if it["eh_decisao"]}
            suspeitas = [it for it in c["itens"] if not it["eh_decisao"] and it["parece_decisao"]]
            tipos_fora.update(it["tipo"] for it in suspeitas)
            fora_coleta = sorted(decisoes_censo - coletadas.get(c["dia"], set()))
            # Só fontes do Diário (ou fundidas com ele) têm diario_url terminada no id da matéria; bases de site, não
            no_banco = {
                int(r[0].rsplit("/", 1)[-1])
                for r in conn.execute(
                    "SELECT diario_url FROM julgados WHERE data_decisao = ? AND diario_url LIKE '%/materia/%' AND fonte NOT LIKE 'SITE_%'", (c["dia"],)
                )
                if r[0].rsplit("/", 1)[-1].isdigit()
            }
            rejeitados = set()
            rej_file = raw_dir_uf(uf, raw_dir) / "rejeitados.jsonl"
            if rej_file.exists():
                rejeitados = {int(json.loads(l)["id"]) for l in rej_file.read_text(encoding="utf-8").splitlines() if l.strip()}
            fora_banco = sorted(decisoes_censo - no_banco - rejeitados - set(fora_coleta))
            faltam_coleta += len(fora_coleta)
            faltam_banco += len(fora_banco)
            resultado_dias.append(
                {
                    "dia": c["dia"], "publicacoes_no_dia": c["total_api"], "censo_completo": c["baixadas"] == c["total_api"] and c["indice_ok"],
                    "decisoes_no_censo": len(decisoes_censo), "fora_da_coleta": fora_coleta, "fora_do_banco": fora_banco,
                    "suspeitas_de_outro_tipo": [{"id": s["id"], "tipo": s["tipo"], "titulo": s["titulo"]} for s in suspeitas[:8]],
                }
            )
        auditoria = {
            "uf": uf, "auditado_em": _agora(), "dias": resultado_dias, "faltantes_na_coleta": faltam_coleta, "faltantes_no_banco": faltam_banco,
            "tipos_suspeitos_fora_do_perfil": dict(tipos_fora),
            "aprovada": faltam_coleta == 0 and faltam_banco == 0 and all(d["censo_completo"] for d in resultado_dias),
        }
        with conn:
            conn.execute(
                "UPDATE cobertura_coleta SET auditoria_json = ? WHERE uf = ? AND fonte = ?", (json.dumps(auditoria, ensure_ascii=False), uf, _fonte(uf))
            )
    out = raw_dir_uf(uf, raw_dir)
    (out / f"auditoria-{auditoria['auditado_em'][:10]}.json").write_text(json.dumps(auditoria, ensure_ascii=False, indent=2), encoding="utf-8")
    return auditoria
