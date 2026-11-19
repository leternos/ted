"""Inteiro teor de verdade: o selo só vale para quem tem relatório e voto, não para o recorte do Diário.

Duas origens de PDF de inteiro teor (relatório + voto + ementa + acórdão):

1. Portal de Ética e Disciplina do Conselho Federal (services.oab.org.br/etica-disciplina): ementário
   selecionado da Segunda Câmara e das três Turmas, um PDF escaneado por acórdão.
   Lido por scripts/etica_pdfs_scan.py (+ etica_pdfs_ocr.py para os só-imagem) -> data/inteiro_teor/etica_pdfs.jsonl,
   que fica no repositório: refazer a leitura baixa cerca de 5 GB de PDFs.
2. Portal de Jurisprudência do Conselho Federal (jurisprudencia.oab.org.br): alguns julgados trazem
   "documentos" anexos ("Relatório e voto", "Acórdão"). Vêm em data/raw/oab_juris_ementa.json.

O que este módulo faz, sempre a partir dos arquivos brutos (pode ser repetido à vontade):

- monta o catálogo `inteiro_teor_pdfs`, um registro por PDF, com o nº do processo lido do próprio PDF
  e conferido com o nº da ementa no Portal de Jurisprudência;
- liga cada PDF aos julgados do banco: `proprio` (é o inteiro teor daquele julgado) ou `mesmo_processo`
  (o julgado é outra decisão do mesmo processo: a da Seccional de origem, embargos, etc.);
- recalcula `tem_inteiro_teor` (1 só com PDF próprio ou texto próprio com relatório e voto) e
  `inteiro_teor_relacionado` (1 quando há PDF de inteiro teor de outra decisão do mesmo processo).

A regra é o conteúdo do documento, nunca a origem.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from ted_search.config import OAB_JURIS_BASE_URL, RAW_DIR

CATALOGO_DIR = RAW_DIR.parent / "inteiro_teor"
ARQ_ETICA_PDFS = CATALOGO_DIR / "etica_pdfs.jsonl"
ARQ_ETICA_LISTA = "etica_strapi_ementarios.json"
ARQ_OAB_JURIS = "oab_juris_ementa.json"

RE_PROC = re.compile(r"\b(\d{2})\.(\d{4})\.((?:19|20)\d{2})\.(\d{6})-(\d)\b")
# Cabeçalhos de seção em linha própria. "Nos termos do relatório e voto" dentro do acórdão não conta.
RE_CAB_RELATORIO = re.compile(r"(?m)^\s*(?:[IVX]+\s*[-–.)]\s*)?(?:D?O\s+)?RELAT[ÓO]RIO\s*[:.]?\s*$")
RE_CAB_VOTO = re.compile(
    r"(?m)^\s*(?:[IVX]+\s*[-–.)]\s*)?(?:D?O\s+)?(?:VOTO|PARECER)(?:\s+D[OA]\s+RELATORA?|\s+VENCEDOR|\s+VISTA)?\s*[:.]?\s*$"
)
TEXTO_MINIMO = 2500


def so_digitos(numero: str | None) -> str:
    return re.sub(r"\D", "", numero or "")


def chave_ementa(numero: str | None, orgao: str | None = None) -> str | None:
    """Chave comum aos dois portais: 'Ementa 001/2024/SCA-PTU' e '1/2024/PTU' -> '1/2024/PTU'."""
    if not numero:
        return None
    s = re.sub(r"\s+", "", numero.upper())
    s = re.sub(r"^EMENTA(?:N\.?[ºO°]?)?", "", s)
    m = re.match(r"^0*(\d{1,4})/((?:19|20)\d{2})(?:[-/](.+))?$", s)
    if not m:
        return None
    # O órgão vem do próprio número ("002/2018/SCA" é do pleno da Segunda Câmara, mesmo quando o processo
    # correu numa Turma); o órgão do registro só serve quando o número não traz sufixo.
    def _orgao(trecho: str) -> str | None:
        trecho = trecho.upper()
        return next((k for k in ("PTU", "STU", "TTU") if k in trecho), None) or ("SCA" if "SCA" in trecho else None)

    orgao_n = _orgao(m.group(3) or "") or (m.group(3) or "").replace("/", "-") or _orgao(orgao or "")
    return f"{int(m.group(1))}/{m.group(2)}/{orgao_n}" if orgao_n else None


def texto_tem_relatorio_e_voto(texto: str | None) -> bool:
    """Texto próprio do julgado é inteiro teor? Exige as duas seções, com cabeçalho em linha própria."""
    if not texto or len(texto) < TEXTO_MINIMO:
        return False
    return bool(RE_CAB_RELATORIO.search(texto) and RE_CAB_VOTO.search(texto))


def _sem_acento(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", t.upper()) if unicodedata.category(ch) != "Mn")


def _formatar(d: str) -> str:
    return f"{d[:2]}.{d[2:6]}.{d[6:10]}.{d[10:16]}-{d[16]}" if len(d) == 17 else d


def _distancia(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))


# ----------------------------------------------------------------------------- catálogo


def _indice_oab_juris(raw_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Portal de Jurisprudência: índice por chave de ementa e lista dos itens com documentos anexos."""
    arq = raw_dir / ARQ_OAB_JURIS
    if not arq.exists():
        return {}, []
    from ted_search.collectors.oab_juris import normalize_oab_juris_item

    por_chave: dict[str, list[dict[str, Any]]] = defaultdict(list)
    com_docs: list[dict[str, Any]] = []
    for bruto in json.loads(arq.read_text(encoding="utf-8")):
        org = bruto.get("orgaoJulgador") or {}
        if org.get("idOrganizacao") == 49:
            # o nº da ementa quase nunca vem no campo próprio: o normalizador o extrai do texto
            rec = normalize_oab_juris_item(bruto, "EMENTA")
            chave = chave_ementa(rec.get("numero_ementa"), rec.get("sigla_orgao"))
            if chave:
                por_chave[chave].append({"id": rec["id"], "processo": so_digitos(rec.get("numero_processo"))})
        if bruto.get("documentos"):
            com_docs.append(bruto)
    return por_chave, com_docs


def montar_catalogo(conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> list[dict[str, Any]]:
    """Um registro por PDF de inteiro teor, com o processo resolvido e como foi resolvido."""
    por_chave, com_docs = _indice_oab_juris(raw_dir)
    processos_no_banco = {
        so_digitos(r[0]) for r in conn.execute("SELECT DISTINCT numero_processo FROM julgados WHERE numero_processo IS NOT NULL")
    }
    processos_no_banco = {p for p in processos_no_banco if len(p) == 17}
    catalogo: list[dict[str, Any]] = []

    arq = ARQ_ETICA_PDFS
    linhas = [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()] if arq.exists() else []
    for pdf in linhas:
        chave = chave_ementa(pdf.get("nome"), pdf.get("camara"))
        candidatos = por_chave.get(chave or "", [])
        procs_ementa = {c["processo"] for c in candidatos if len(c["processo"]) == 17}
        lidos = [so_digitos(p) for p in pdf.get("processos") or []]
        processo, como = None, "nao_resolvido"
        confirmados = [p for p in lidos if p in procs_ementa]
        if confirmados:
            processo, como = confirmados[0], "pdf_e_ementa"
        elif lidos and procs_ementa:
            # a camada de texto do PDF escaneado troca dígitos: fica o processo da ementa mais parecido com o lido
            melhor = min(procs_ementa, key=lambda p: min(_distancia(p, l) for l in lidos))
            if min(_distancia(melhor, l) for l in lidos) <= 3:
                processo, como = melhor, "ementa_proxima_do_pdf"
        if not processo and lidos:
            no_banco = [p for p in lidos if p in processos_no_banco]
            if no_banco:
                processo, como = no_banco[0], "pdf"
        if not processo and len(procs_ementa) == 1:
            processo, como = next(iter(procs_ementa)), "ementa"
        if not processo and lidos:
            processo, como = lidos[0], "pdf_nao_conferido"
        proprios = [c["id"] for c in candidatos if processo and c["processo"] == processo]
        completo = bool(pdf.get("tem_relatorio") and pdf.get("tem_voto"))
        sem_texto = (pdf.get("chars") or 0) < 500
        catalogo.append(
            {
                "id": f"ETICA_PDF:{pdf['strapi_id']}",
                "origem": "PORTAL_ETICA_CFOAB",
                "titulo": re.sub(r"\s+", " ", pdf.get("nome") or "").strip(),
                "chave_ementa": chave,
                "orgao": pdf.get("camara"),
                "ano": int(pdf["ano"]) if str(pdf.get("ano") or "").isdigit() else None,
                "numero_processo": _formatar(processo) if processo else None,
                "processo_como": como,
                "pdf_url": pdf.get("pdf_url"),
                "paginas": pdf.get("paginas"),
                # só-imagem ainda não lido por OCR (scripts/etica_pdfs_ocr.py) fica de fora até ser conferido
                "conteudo": "relatorio_e_voto" if completo else ("imagem_nao_conferida" if sem_texto and not pdf.get("ocr") else "incompleto"),
                "julgados_proprios": proprios,
            }
        )

    from ted_search.collectors.oab_juris import normalize_oab_juris_item

    for bruto in com_docs:
        rec = normalize_oab_juris_item(bruto, "EMENTA")
        tipos = {_sem_acento((d.get("tipo") or {}).get("descricao") or "") for d in bruto["documentos"]}
        tem_voto = any("VOTO" in t for t in tipos)
        for doc in bruto["documentos"]:
            tipo = ((doc.get("tipo") or {}).get("descricao") or "Documento").strip()
            catalogo.append(
                {
                    "id": f"JURIS_DOC:{doc['_id']}",
                    "origem": "PORTAL_JURISPRUDENCIA_CFOAB",
                    "titulo": f"{tipo} — {(doc.get('descricao') or '').strip().rstrip('.') or rec.get('numero_ementa') or ''}".strip(" —"),
                    "chave_ementa": chave_ementa(bruto.get("numeroEmenta"), (bruto.get("orgaoJulgador") or {}).get("nomeSetor")),
                    "orgao": (bruto.get("orgaoJulgador") or {}).get("nomeSetor") or (bruto.get("orgaoJulgador") or {}).get("nomeOrganizacao"),
                    "ano": rec.get("ano_ementa"),
                    "numero_processo": bruto.get("numeroProcesso"),
                    "processo_como": "portal",
                    "pdf_url": f"{OAB_JURIS_BASE_URL}/contexto/EMENTA/documento/{doc['_id']}/download/binario",
                    "paginas": None,
                    "conteudo": "relatorio_e_voto" if "VOTO" in _sem_acento(tipo) else ("acordao" if tem_voto else "incompleto"),
                    "julgados_proprios": [rec["id"]],
                }
            )
    # o mesmo documento pode vir anexado a mais de um julgado: um registro por PDF, somando os julgados
    unicos: dict[str, dict[str, Any]] = {}
    for pdf in catalogo:
        if pdf["id"] in unicos:
            ja = unicos[pdf["id"]]["julgados_proprios"]
            ja.extend(j for j in pdf["julgados_proprios"] if j not in ja)
        else:
            unicos[pdf["id"]] = pdf
    return list(unicos.values())


# ----------------------------------------------------------------------------- aplicação no banco

SCHEMA = """
CREATE TABLE IF NOT EXISTS inteiro_teor_pdfs (
    id TEXT PRIMARY KEY,
    origem TEXT NOT NULL,
    titulo TEXT,
    chave_ementa TEXT,
    orgao TEXT,
    ano INTEGER,
    numero_processo TEXT,
    processo_como TEXT,
    pdf_url TEXT NOT NULL,
    paginas INTEGER,
    conteudo TEXT,
    julgados_proprios_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_itpdf_processo ON inteiro_teor_pdfs(numero_processo);
"""


def garantir_esquema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if "julgados_proprios_json" not in {r[1] for r in conn.execute("PRAGMA table_info(inteiro_teor_pdfs)")}:
        conn.execute("ALTER TABLE inteiro_teor_pdfs ADD COLUMN julgados_proprios_json TEXT")
    colunas = {r[1] for r in conn.execute("PRAGMA table_info(julgados)")}
    if "inteiro_teor_pdfs_json" not in colunas:
        conn.execute("ALTER TABLE julgados ADD COLUMN inteiro_teor_pdfs_json TEXT")
    if "inteiro_teor_relacionado" not in colunas:
        conn.execute("ALTER TABLE julgados ADD COLUMN inteiro_teor_relacionado INTEGER DEFAULT 0")


def reconciliar_registros(conn: sqlite3.Connection, catalogo: list[dict[str, Any]], raw_dir: Path = RAW_DIR) -> dict[str, int]:
    """Um julgado por acórdão: o registro do Portal de Jurisprudência fica; o duplicado do Portal de Ética sai.

    - julgado do Portal de Jurisprudência apontado por um PDF e ainda ausente do banco: é inserido;
    - registro antigo `ETICA_STRAPI` do mesmo acórdão: cede o texto extraído e é apagado;
    - PDF do portal sem nenhum julgado correspondente: ganha um registro `ETICA_STRAPI` próprio.
    """
    from ted_search.cli import _insert_record
    from ted_search.collectors.etica_strapi import normalize_etica_strapi_item
    from ted_search.collectors.oab_juris import normalize_oab_juris_item

    n = {"juris_inseridos": 0, "etica_fundidos": 0, "etica_criados": 0}
    existentes = {r[0] for r in conn.execute("SELECT id FROM julgados")}
    alvos = {j for pdf in catalogo for j in pdf["julgados_proprios"]} - existentes
    arq = raw_dir / ARQ_OAB_JURIS
    if alvos and arq.exists():
        for bruto in json.loads(arq.read_text(encoding="utf-8")):
            rec = normalize_oab_juris_item(bruto, "EMENTA")
            if rec["id"] in alvos:
                _insert_record(conn, rec)
                alvos.discard(rec["id"])
                existentes.add(rec["id"])
                n["juris_inseridos"] += 1

    etica_por_url: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for jid, url, texto in conn.execute("SELECT id, pdf_url, inteiro_teor_texto FROM julgados WHERE fonte = 'ETICA_STRAPI'"):
        etica_por_url[url].append((jid, texto))
    brutos_etica = {}
    arq_lista = raw_dir / ARQ_ETICA_LISTA
    if arq_lista.exists():
        brutos_etica = {f"ETICA_PDF:{b['id']}": b for b in json.loads(arq_lista.read_text(encoding="utf-8"))}

    for pdf in catalogo:
        if pdf["origem"] != "PORTAL_ETICA_CFOAB":
            continue
        proprios = [j for j in pdf["julgados_proprios"] if j in existentes]
        antigos = etica_por_url.get(pdf["pdf_url"], [])
        if proprios:
            for jid, texto in antigos:
                if texto:
                    conn.execute(
                        "UPDATE julgados SET inteiro_teor_texto = ? WHERE id = ? AND (inteiro_teor_texto IS NULL OR inteiro_teor_texto = '')",
                        (texto, proprios[0]),
                    )
                conn.execute("DELETE FROM julgado_dispositivos WHERE julgado_id = ?", (jid,))
                conn.execute("DELETE FROM julgados WHERE id = ?", (jid,))
                n["etica_fundidos"] += 1
        elif not antigos and pdf["id"] in brutos_etica:
            rec = normalize_etica_strapi_item(brutos_etica[pdf["id"]])
            rec["numero_processo"] = rec.get("numero_processo") or pdf["numero_processo"]
            _insert_record(conn, rec)
            n["etica_criados"] += 1
    return n


def _ordem(item: dict[str, Any]) -> tuple:
    return (0 if item["relacao"] == "proprio" else 1, 0 if item["conteudo"] == "relatorio_e_voto" else 1, -(item.get("ano") or 0), item["titulo"])


def atualizar_catalogo(conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    """Regrava `inteiro_teor_pdfs` a partir dos arquivos brutos e acerta os registros duplicados do portal."""
    garantir_esquema(conn)
    catalogo = montar_catalogo(conn, raw_dir)
    with conn:
        n = reconciliar_registros(conn, catalogo, raw_dir)
        conn.execute("DELETE FROM inteiro_teor_pdfs")
        conn.executemany(
            "INSERT INTO inteiro_teor_pdfs VALUES (:id,:origem,:titulo,:chave_ementa,:orgao,:ano,:numero_processo,:processo_como,:pdf_url,:paginas,:conteudo,:proprios)",
            [dict(pdf, proprios=json.dumps(pdf["julgados_proprios"])) for pdf in catalogo],
        )
    return n


COM_INTEIRO_TEOR = ("relatorio_e_voto", "acordao")


def recalcular_selos(conn: sqlite3.Connection) -> dict[str, Any]:
    """Recalcula o selo de todos os julgados a partir do catálogo já gravado no banco (não precisa dos brutos).

    Roda no fim de toda carga (ver db.rebuild_fts): os normalizadores antigos ainda gravam tem_inteiro_teor=1
    para qualquer publicação do Diário, e é aqui que isso é corrigido.
    """
    garantir_esquema(conn)
    conn.row_factory = None
    catalogo = []
    for r in conn.execute(
        "SELECT id, origem, titulo, orgao, ano, numero_processo, pdf_url, paginas, conteudo, julgados_proprios_json FROM inteiro_teor_pdfs"
    ):
        catalogo.append(dict(zip(("id", "origem", "titulo", "orgao", "ano", "numero_processo", "pdf_url", "paginas", "conteudo"), r[:9]),
                             julgados_proprios=json.loads(r[9] or "[]")))
    por_julgado: dict[str, list[dict[str, Any]]] = defaultdict(list)
    por_processo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    por_url: dict[str, dict[str, Any]] = {}
    for pdf in catalogo:
        if pdf["conteudo"] not in COM_INTEIRO_TEOR:
            continue
        por_url[pdf["pdf_url"]] = pdf
        for jid in pdf["julgados_proprios"]:
            por_julgado[jid].append(pdf)
        if len(so_digitos(pdf["numero_processo"])) == 17:
            por_processo[so_digitos(pdf["numero_processo"])].append(pdf)

    n = {"pdfs_no_catalogo": len(catalogo), "proprio_pdf": 0, "proprio_texto": 0, "relacionado": 0}
    with conn:
        # registro criado a partir do próprio portal (fonte ETICA_STRAPI): o PDF dele é o inteiro teor dele
        for jid, url in conn.execute("SELECT id, pdf_url FROM julgados WHERE fonte = 'ETICA_STRAPI'").fetchall():
            pdf = por_url.get(url)
            if pdf:
                if pdf not in por_julgado[jid]:
                    por_julgado[jid].append(pdf)
                if pdf["numero_processo"]:
                    conn.execute("UPDATE julgados SET numero_processo = COALESCE(numero_processo, ?) WHERE id = ?", (pdf["numero_processo"], jid))
        conn.execute("UPDATE julgados SET tem_inteiro_teor = 0, inteiro_teor_relacionado = 0, inteiro_teor_pdfs_json = NULL")
        for jid, proc, texto, fonte in conn.execute("SELECT id, numero_processo, inteiro_teor_texto, fonte FROM julgados").fetchall():
            proprios = {p["id"]: p for p in por_julgado.get(jid, [])}
            itens = [dict(p, relacao="proprio") for p in proprios.values()]
            for p in por_processo.get(so_digitos(proc), []) if len(so_digitos(proc)) == 17 else []:
                if p["id"] not in proprios:
                    itens.append(dict(p, relacao="mesmo_processo"))
            # o texto extraído por máquina de PDF escaneado (fonte ETICA_STRAPI) não é critério: vale o PDF
            texto_ok = fonte != "ETICA_STRAPI" and texto_tem_relatorio_e_voto(texto)
            tem_proprio = any(i["relacao"] == "proprio" and i["conteudo"] == "relatorio_e_voto" for i in itens)
            relacionado = any(i["relacao"] == "mesmo_processo" and i["conteudo"] == "relatorio_e_voto" for i in itens)
            if not (itens or texto_ok):
                continue
            itens.sort(key=_ordem)
            enxuto = [{k: i[k] for k in ("titulo", "pdf_url", "paginas", "relacao", "origem", "orgao", "ano", "conteudo")} for i in itens]
            conn.execute(
                "UPDATE julgados SET tem_inteiro_teor = ?, inteiro_teor_relacionado = ?, inteiro_teor_pdfs_json = ? WHERE id = ?",
                (1 if (tem_proprio or texto_ok) else 0, 1 if relacionado else 0, json.dumps(enxuto, ensure_ascii=False) if enxuto else None, jid),
            )
            n["proprio_pdf"] += tem_proprio
            n["proprio_texto"] += texto_ok and not tem_proprio
            n["relacionado"] += relacionado and not tem_proprio and not texto_ok
    n["por_conteudo"] = dict(conn.execute("SELECT conteudo, COUNT(*) FROM inteiro_teor_pdfs GROUP BY 1").fetchall())
    n["processo_como"] = dict(conn.execute("SELECT processo_como, COUNT(*) FROM inteiro_teor_pdfs GROUP BY 1").fetchall())
    n["por_fonte"] = {
        r[0]: {"inteiro_teor": r[1], "relacionado": r[2]}
        for r in conn.execute(
            "SELECT CASE WHEN fonte LIKE 'DEOAB_%' AND fonte != 'DEOAB_SP' THEN 'DEOAB_outras' ELSE fonte END, "
            "SUM(tem_inteiro_teor), SUM(inteiro_teor_relacionado AND NOT tem_inteiro_teor) FROM julgados GROUP BY 1"
        )
    }
    return n


def aplicar(conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Atualiza o catálogo (se os arquivos brutos estiverem no disco) e recalcula os selos."""
    reconciliacao = atualizar_catalogo(conn, raw_dir) if ARQ_ETICA_PDFS.exists() else None
    n = recalcular_selos(conn)
    n["reconciliacao"] = reconciliacao
    return n
