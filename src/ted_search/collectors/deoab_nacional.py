"""Coletor assíncrono nacional para Seccionais da OAB via Diário Eletrônico (DEOAB).

Capaz de varrer qualquer uma das 27 Seccionais (idOrganizacao 1 a 27),
extrair acórdãos e ementas ético-disciplinares e normalizar os registros
no schema padrão de julgados do ted_search (SQLite + FTS5).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ted_search.config import (
    DB_PATH,
    DEOAB_API_BASE_URL,
    DEOAB_ASSETS_BASE_URL,
    DIARIO_MATERIA_URL,
    RAW_DIR,
    SECCIONAIS,
    create_async_http_client,
)
from ted_search.collectors.deoab_sp import html_to_paragraphs
from ted_search.db import get_connection, rebuild_fts
from ted_search.parser import (
    clean_text,
    detect_resultado,
    detect_sancao,
    extract_dispositivos,
    extract_relator_fallback,
)

RE_PROC_CNJ = re.compile(r"\b(\d{2}\.\d{4}\.(?:19|20)\d{2}\.\d{6}-\d)\b")
RE_PROC_GENERIC = re.compile(
    r"\b(?:Processo|Proc\.?|Autos)\s*(?:n[º°.]?\s*)?([0-9A-Z.\-/]{4,30})\b",
    re.IGNORECASE,
)
RE_ACORDAO_NUM = re.compile(
    r"A\s*C\s*[ÓO]\s*R\s*D\s*[ÃA]\s*O\s*(?:N[º°o.]?\s*)?(\d{1,6}(?:/\d{2,4})?)\b",
    re.IGNORECASE,
)
RE_EMENTA_NUM = re.compile(
    r"EMENTA\s*(?:TED)?\s*(?:N[º°o.]?\s*)?(\d{1,6}(?:/\d{2,4})?)\b",
    re.IGNORECASE,
)
RE_VISTOS_SPLIT = re.compile(
    r"(?:^|\n)\s*(Vistos\s*,?\s*relatados\s+e\s+(?:discutidos|examinados).+)$",
    re.IGNORECASE | re.DOTALL,
)

UF_TO_ORG = {s["uf"]: s for s in SECCIONAIS}
ORG_TO_UF = {s["id_organizacao"]: s for s in SECCIONAIS}


def detect_orgao_julgador_nacional(
    uf: str,
    setor_nome: str,
    titulo: str,
    full_text: str,
) -> tuple[str, str]:
    """Determina a sigla padronizada e o nome normalizado do órgão julgador em qualquer Seccional."""
    comb = f"{setor_nome} {titulo} {full_text[:400]}".upper()

    # Mapeamento de ordinais por extenso
    ordinais = {
        "PRIMEIRA": "1", "SEGUNDA": "2", "TERCEIRA": "3", "QUARTA": "4", "QUINTA": "5",
        "SEXTA": "6", "SÉTIMA": "7", "SETIMA": "7", "OITAVA": "8", "NONA": "9", "DÉCIMA": "10",
        "DECIMA": "10", "DÉCIMA PRIMEIRA": "11", "DÉCIMA SEGUNDA": "12", "DÉCIMA TERCEIRA": "13",
    }

    # Detectar Câmaras Recursais / Disciplinares
    m_cam = re.search(r"(\d{1,2})[ªaª°]?\s*C[ÂA]MARA", comb)
    cam_num = m_cam.group(1) if m_cam else ""
    if not cam_num:
        for ord_word, num_str in ordinais.items():
            if f"{ord_word} CÂMARA" in comb or f"{ord_word} CAMARA" in comb:
                cam_num = num_str
                break
    if "CÂMARA" in comb or "CAMARA" in comb:
        num_suffix = f"-{cam_num}CR" if cam_num else "-CR"
        return f"OAB{uf}{num_suffix}", f"Conselho Seccional - {uf}/{cam_num + 'ª ' if cam_num else ''}Câmara Recursal/Disciplinar"

    # Detectar Órgão Especial
    if "ÓRGÃO ESPECIAL" in comb or "ORGAO ESPECIAL" in comb:
        return f"OAB{uf}-OE", f"Conselho Seccional - {uf}/Órgão Especial"

    # Detectar Conselho Pleno
    if "PLENO" in comb or "CONSELHO PLENO" in comb or "CONSELHO SECCIONAL" in comb:
        return f"OAB{uf}-PLENO", f"Conselho Seccional - {uf}/Conselho Pleno"

    # Detectar Turmas do TED
    m_turma = re.search(r"(\d{1,2})[ªaª°]?\s*TURMA", comb)
    t_num = m_turma.group(1) if m_turma else ""
    if not t_num:
        for ord_word, num_str in ordinais.items():
            if f"{ord_word} TURMA" in comb:
                t_num = num_str
                break
    if t_num:
        return f"OAB{uf}-TED-T{t_num}", f"Tribunal de Ética e Disciplina da OAB/{uf}/{t_num}ª Turma Disciplinar"

    # Deontológico / Consultivo
    if "DEONTOL" in comb or "CONSULTIV" in comb or "CONSULTA" in comb:
        return f"OAB{uf}-TED-CONS", f"Tribunal de Ética e Disciplina da OAB/{uf}/Turma Deontológica e Consultiva"

    # TED Geral
    if "TRIBUNAL DE ÉTICA" in comb or "TRIBUNAL DE ETICA" in comb or "TED" in comb:
        return f"OAB{uf}-TED", f"Tribunal de Ética e Disciplina da OAB/{uf}"

    # Fallback para o próprio setor
    norm_setor = setor_nome if setor_nome else "Tribunal de Ética e Disciplina"
    return f"OAB{uf}-TED", f"Conselho Seccional - {uf}/{norm_setor}"


def normalize_deoab_item_nacional(
    pub: dict[str, Any],
    default_uf: str | None = None,
    default_org_id: int | None = None,
) -> dict[str, Any] | None:
    """Normaliza uma publicação do DEOAB para o schema do banco julgados."""
    if "objeto" in pub and isinstance(pub["objeto"], dict):
        pub = pub["objeto"]
    pub_id = pub.get("id")
    if not pub_id:
        return None

    tipo_pub = clean_text((pub.get("tipoPublicacao") or {}).get("descricao") or "").upper()
    valid_tipos = {"ACÓRDÃO", "ACORDAO", "EMENTA", "DECISÃO", "DECISAO", "RECURSO", "JULGAMENTO"}
    if tipo_pub not in valid_tipos and tipo_pub:
        # Se for edital ou notificação pura sem conteúdo decisório, ignorar
        if any(w in tipo_pub for w in ["CONVOCAÇÃO", "PAUTA", "PORTARIA", "NOTIFICAÇÃO", "EDITAL"]):
            return None

    setor_obj = pub.get("setorPublicacao") or {}
    setor_nome = clean_text(setor_obj.get("nomeAlternativo") or "")
    org_info = setor_obj.get("organizacao") or {}
    org_id = org_info.get("id") or setor_obj.get("organizacaoId") or default_org_id

    sec_meta = ORG_TO_UF.get(org_id)
    uf = sec_meta["uf"] if sec_meta else (default_uf or "BR")
    nome_org = sec_meta["nome"] if sec_meta else (org_info.get("nomeAlternativo") or f"OAB/{uf}")

    titulo = clean_text(pub.get("titulo") or "")
    full_text = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
    if not full_text or len(full_text) < 60:
        return None

    # Filtrar notificações administrativas puras ("notifica a comparecer")
    txt_lower = full_text.lower()
    has_juris_keywords = any(k in txt_lower for k in ["acordam", "acórdão", "ementa", "vistos", "censura", "suspensão", "exclusão", "representação", "infração", "ced", "eaoab"])
    if not has_juris_keywords and "notifica" in txt_lower:
        return None

    # Separar ementa e acórdão
    m_vistos = RE_VISTOS_SPLIT.search(full_text)
    if m_vistos:
        acordao_text = m_vistos.group(1).strip()
        ementa_text = full_text[: m_vistos.start()].strip()
    else:
        acordao_text = ""
        ementa_text = full_text

    if not ementa_text and acordao_text:
        ementa_text = acordao_text

    # Extrair número do processo
    m_proc = RE_PROC_CNJ.search(full_text)
    if not m_proc:
        m_proc = RE_PROC_GENERIC.search(f"{titulo}\n{full_text[:300]}")
    numero_processo = m_proc.group(1).strip() if m_proc else None

    # Extrair número do acórdão / ementa
    m_ac = RE_ACORDAO_NUM.search(f"{titulo}\n{full_text[:500]}")
    numero_acordao = m_ac.group(1).strip() if m_ac else None

    m_em = RE_EMENTA_NUM.search(f"{titulo}\n{full_text[:500]}")
    numero_ementa = m_em.group(1).strip() if m_em else None

    sigla_orgao, orgao_norm = detect_orgao_julgador_nacional(uf, setor_nome, titulo, full_text)
    if not numero_ementa and numero_acordao:
        numero_ementa = f"AC-{numero_acordao}/{sigla_orgao}"

    dispositivos = extract_dispositivos(None, full_text)
    sancao = detect_sancao(full_text)
    resultado = detect_resultado(ementa_text, acordao_text)
    relator = extract_relator_fallback(None, full_text)

    data_pub = (pub.get("dataPublicacao") or "")[:10] or None
    ano_ementa = int(data_pub[:4]) if (data_pub and data_pub[:4].isdigit()) else None
    edicao = pub.get("edicao")
    pagina = pub.get("pagina")
    ano_romano = pub.get("ano") or ""
    pub_str = f"DEOAB/{uf}, Ano {ano_romano}, Ed. {edicao}, p. {pagina} ({data_pub})"

    tipo_ementa = "CONSULTA" if "CONS" in sigla_orgao or "CONSULTA" in txt_lower[:300] else "PROCESSO_DISCIPLINAR"

    return {
        "id": f"DEOAB_{uf}:{pub_id}",
        "fonte": f"DEOAB_{uf}",
        "contexto": "EMENTA_CONSULTA" if tipo_ementa == "CONSULTA" else "EMENTA",
        "tipo_ementa": tipo_ementa,
        "numero_processo": numero_processo,
        "numero_ementa": numero_ementa,
        "ano_ementa": ano_ementa,
        "id_organizacao": org_id,
        "nome_organizacao": f"Conselho Seccional - {nome_org}",
        "orgao_julgador_raw": f"Conselho Seccional - {nome_org} / {setor_nome}" if setor_nome else f"Conselho Seccional - {nome_org}",
        "orgao_julgador_norm": orgao_norm,
        "sigla_orgao": sigla_orgao,
        "uf_origem": uf,
        "relator_principal": relator,
        "relatores_json": "[]",
        "partes_json": "[]",
        "dispositivos_json": json.dumps(dispositivos, ensure_ascii=False),
        "dispositivos_texto": " | ".join(dispositivos),
        "sancao_identificada": sancao,
        "resultado_julgamento": resultado,
        "descricao_ementa": ementa_text,
        "descricao_acordao": acordao_text,
        "inteiro_teor_texto": full_text,
        "pdf_url": f"/api/diario-pdf/{pub_id}",
        "diario_url": f"{DIARIO_MATERIA_URL}/{pub_id}",
        "publicacao_str": pub_str,
        "data_decisao": data_pub,
        "is_ted_disciplinar": 1,
        "tem_inteiro_teor": 1,
    }


async def fetch_seccional_sample(
    client,
    uf: str,
    max_pages: int = 2,
    year: int = 2024,
    concurrency: int = 10,
    sem: asyncio.Semaphore | None = None,
) -> list[dict[str, Any]]:
    """Varre páginas de uma Seccional específica e retorna os registros normalizados."""
    sec = UF_TO_ORG.get(uf.upper())
    if not sec:
        raise ValueError(f"UF inválida: {uf}")
    org_id = sec["id_organizacao"]

    if sem is None:
        sem = asyncio.Semaphore(concurrency)

    # 1. Coletar IDs de publicações
    candidate_ids = []
    seen = set()

    for termo in ["ACORDAO", "EMENTA"]:
        for page in range(1, max_pages + 1):
            params = {
                "pagina": str(page),
                "idOrganizacao": str(org_id),
                "termo": termo,
                "inicio": f"01/01/{year}",
                "fim": f"31/12/{year}",
            }
            async with sem:
                try:
                    r = await client.get(f"{DEOAB_API_BASE_URL}/diario/pesquisa", params=params)
                    if r.status_code == 200:
                        itens = (r.json() or {}).get("itens") or []
                        for it in itens:
                            pid = it.get("id")
                            if pid and pid not in seen:
                                seen.add(pid)
                                candidate_ids.append(pid)
                except Exception:
                    pass

    # 2. Baixar detalhes e normalizar
    normalized_items: list[dict[str, Any]] = []

    async def _fetch_one(pid: int):
        async with sem:
            for attempt in range(2):
                try:
                    r = await client.get(f"{DEOAB_API_BASE_URL}/publicacao/{pid}/publicado")
                    if r.status_code == 200:
                        pub_data = r.json()
                        norm = normalize_deoab_item_nacional(pub_data, default_uf=uf, default_org_id=org_id)
                        if norm:
                            normalized_items.append(norm)
                    break
                except Exception:
                    await asyncio.sleep(0.3)

    await asyncio.gather(*(_fetch_one(pid) for pid in candidate_ids))
    return normalized_items


def save_records_to_database(
    records: list[dict[str, Any]],
    db_path: Path = DB_PATH,
    rebuild_fts_after: bool = True,
) -> int:
    """Insere ou atualiza registros de julgados no SQLite e atualiza o FTS5."""
    if not records:
        return 0

    with get_connection(db_path) as conn:
        with conn:
            for rec in records:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO julgados (
                        id, fonte, contexto, tipo_ementa, numero_processo, numero_ementa,
                        ano_ementa, id_organizacao, nome_organizacao, orgao_julgador_raw,
                        orgao_julgador_norm, sigla_orgao, uf_origem, relator_principal,
                        relatores_json, partes_json, dispositivos_json, dispositivos_texto,
                        sancao_identificada, resultado_julgamento, descricao_ementa,
                        descricao_acordao, inteiro_teor_texto, pdf_url, diario_url,
                        publicacao_str, data_decisao, is_ted_disciplinar, tem_inteiro_teor
                    ) VALUES (
                        :id, :fonte, :contexto, :tipo_ementa, :numero_processo, :numero_ementa,
                        :ano_ementa, :id_organizacao, :nome_organizacao, :orgao_julgador_raw,
                        :orgao_julgador_norm, :sigla_orgao, :uf_origem, :relator_principal,
                        :relatores_json, :partes_json, :dispositivos_json, :dispositivos_texto,
                        :sancao_identificada, :resultado_julgamento, :descricao_ementa,
                        :descricao_acordao, :inteiro_teor_texto, :pdf_url, :diario_url,
                        :publicacao_str, :data_decisao, :is_ted_disciplinar, :tem_inteiro_teor
                    )
                    """,
                    rec,
                )
                dispositivos = json.loads(rec.get("dispositivos_json") or "[]")
                for disp in dispositivos:
                    conn.execute(
                        "INSERT OR IGNORE INTO julgado_dispositivos (julgado_id, dispositivo) VALUES (?, ?)",
                        (rec["id"], disp),
                    )
            if rebuild_fts_after:
                rebuild_fts(conn)

    return len(records)

