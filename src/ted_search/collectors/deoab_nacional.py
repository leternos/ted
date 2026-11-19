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
    r"(?:^|\n|AC[ÓO]RD[ÃA]O\s*:\s*)\s*(Vistos?\s*,?\s*relatados\s+e\s+(?:discutidos|examinados).+)$",
    re.IGNORECASE | re.DOTALL,
)

UF_TO_ORG = {s["uf"]: s for s in SECCIONAIS}
ORG_TO_UF = {s["id_organizacao"]: s for s in SECCIONAIS}


_ORDINAIS_NACIONAL = (
    ("DÉCIMA PRIMEIRA", 11), ("DECIMA PRIMEIRA", 11), ("DÉCIMA SEGUNDA", 12), ("DECIMA SEGUNDA", 12),
    ("DÉCIMA TERCEIRA", 13), ("DECIMA TERCEIRA", 13), ("PRIMEIRA", 1), ("SEGUNDA", 2), ("TERCEIRA", 3),
    ("QUARTA", 4), ("QUINTA", 5), ("SEXTA", 6), ("SÉTIMA", 7), ("SETIMA", 7), ("OITAVA", 8), ("NONA", 9),
    ("DÉCIMA", 10), ("DECIMA", 10),
)
_RE_ORGAO_NACIONAL = re.compile(
    r"(?P<cam>C[ÂA]MARAS?\b)|(?P<oe>[ÓO]RG[ÃA]O\s+ESPECIAL)|(?P<pleno>\bPLENO\b)|(?P<turma>\bTURMAS?\b)"
)


def _ordinal_before(upper: str, pos: int) -> str:
    """Número ordinal (dígitos ou por extenso) imediatamente antes de 'TURMA'/'CÂMARA' (posição pos)."""
    prefix = upper[max(0, pos - 24):pos]
    m = re.search(r"(?<!\d)(\d{1,2})\s*[ªaAº°]?\s*$", prefix)
    if m:
        return m.group(1)
    for word, num in _ORDINAIS_NACIONAL:
        if re.search(rf"\b{word}\s+$", prefix):
            return str(num)
    return ""


def _classify_orgao_nacional(uf: str, segment: str) -> tuple[str, str] | None:
    """Classifica um trecho que NOMEIA o órgão julgador (cabeçalho, fórmula decisória, intimação).

    Devolve None quando o trecho não nomeia órgão. Vale a menção mais cedo no trecho.
    """
    upper = (segment or "").upper()
    m = _RE_ORGAO_NACIONAL.search(upper)
    if not m:
        return None
    if m.lastgroup == "cam":
        num = _ordinal_before(upper, m.start())
        suffix = f"-{num}CR" if num else "-CR"
        return f"OAB{uf}{suffix}", f"Conselho Seccional - {uf}/{num + 'ª ' if num else ''}Câmara Recursal/Disciplinar"
    if m.lastgroup == "oe":
        return f"OAB{uf}-OE", f"Conselho Seccional - {uf}/Órgão Especial"
    if m.lastgroup == "pleno":
        return f"OAB{uf}-PLENO", f"Conselho Seccional - {uf}/Conselho Pleno"
    num = _ordinal_before(upper, m.start())
    if num:
        return f"OAB{uf}-TED-T{num}", f"Tribunal de Ética e Disciplina da OAB/{uf}/{num}ª Turma Disciplinar"
    return None


# Fórmula decisória: só vale o órgão que ele mesmo julga ("acordam os membros da 3ª Câmara...").
_RE_FORMULA_ACORDAM = re.compile(r"\bAC[ÓO]RDAM\b[^.;:]{0,160}", re.IGNORECASE)
# Intimação de acórdão (DEOAB/AL): "intima ... RECURSO do acórdão prolatado pela 2ª Turma do TED".
_RE_FORMULA_ACORDAM_LONGA = re.compile(r"\bAC[ÓO]RDAM\b[^\n]{0,500}", re.IGNORECASE)
_RE_ACORDAO_PROLATADO = re.compile(r"\bAC[ÓO]RD[ÃA]OS?\s+PROLATAD[OA]S?\b[^.;:]{0,70}", re.IGNORECASE)


def _orgao_do_corpo(uf: str, full_text: str) -> tuple[str, str] | None:
    """Órgão nomeado pelo corpo: fórmula 'acordam os membros da ...' e, na falta dela, 'acórdão prolatado pela ...'."""
    for rx in (_RE_FORMULA_ACORDAM, _RE_ACORDAO_PROLATADO):
        for m in rx.finditer(full_text or ""):
            found = _classify_orgao_nacional(uf, m.group(0))
            if found:
                return found
    return None


def detect_orgao_julgador_nacional(
    uf: str,
    setor_nome: str,
    titulo: str,
    full_text: str,
) -> tuple[str, str]:
    """Determina a sigla padronizada e o nome normalizado do órgão julgador em qualquer Seccional.

    Mesmo padrão de detect_orgao_julgador_oabsp: o cabeçalho (título, depois setor) manda; o corpo só
    decide pela fórmula 'acordam os membros da ...' (ou 'acórdão prolatado pela ...' nas intimações).
    Menções soltas no corpo (origem do recurso, precedentes, 'deontológico') nunca definem o órgão.
    O setor fica depois do corpo porque, no DEOAB, vem às vezes preenchido errado (ex.: '1ª Câmara').
    """
    # 1. Título da publicação
    found = _classify_orgao_nacional(uf, titulo)
    if found:
        return found
    # 2. Fórmula decisória / intimação no corpo
    found = _orgao_do_corpo(uf, full_text)
    if found:
        return found
    # 3. Setor da publicação
    found = _classify_orgao_nacional(uf, setor_nome)
    if found:
        return found

    header = f"{setor_nome} {titulo}".upper()
    # 4. Último recurso (cabeçalho e fórmula mudos): Turma do TED nomeada de forma unívoca no início do texto.
    #    Câmara/Pleno/consulta NÃO entram aqui: aparecem no corpo como origem do recurso ou precedente.
    turmas = {
        c for c in (
            _classify_orgao_nacional(uf, m.group(0))
            for m in re.finditer(r"[^.\n]{0,24}\bTURMA\b[^.\n]{0,24}", (full_text or "")[:400], re.IGNORECASE)
        ) if c and "-TED-T" in c[0]
    }
    if len(turmas) == 1:
        return turmas.pop()
    # Consulta: só o NOME do órgão consultivo no cabeçalho conta, nunca "consulta"/"deontol" soltos no corpo
    if re.search(r"DEONTOL[ÓO]GIC|CONSULTIV|\bCONSULTAS?\b", header):
        return f"OAB{uf}-TED-CONS", f"Tribunal de Ética e Disciplina da OAB/{uf}/Turma Deontológica e Consultiva"
    if "CONSELHO SECCIONAL" in header:
        return f"OAB{uf}-PLENO", f"Conselho Seccional - {uf}/Conselho Pleno"

    # TED genérico: o texto não nomeia a turma
    if "TRIBUNAL DE ÉTICA" in header or "TRIBUNAL DE ETICA" in header or "TED" in header:
        return f"OAB{uf}-TED", f"Tribunal de Ética e Disciplina da OAB/{uf}"
    norm_setor = setor_nome if setor_nome else "Tribunal de Ética e Disciplina"
    return f"OAB{uf}-TED", f"Conselho Seccional - {uf}/{norm_setor}"


_RE_JULGAR_MERITO = re.compile(r"\bJULG[ÁA](?:-L[AO]|R(?:-SE|-L[AO])?)?\s+(IM)?PROCEDENTE\b", re.IGNORECASE)
_RE_FORMULA_PRESCRICAO = re.compile(
    r"\b(?:RECONHECER|RECONHECIMENTO|OCORR[ÊE]NCIA|DECLARAR|DECLARADA)\b[^.]{0,60}\bPRESCRI[ÇC][ÃA]O\b|"
    r"\bEXTINTA\s+A\s+PUNIBILIDADE\b",
    re.IGNORECASE,
)


def _resultado_pela_formula(full_text: str) -> str | None:
    """Resultado lido só da fórmula 'acordam os membros ...', quando detect_resultado não achou nada.

    Cobre redações frequentes nas intimações do DEOAB/AL: 'julgá-la IMPROCEDENTE', 'julgar procedente',
    'reconhecer ... a prescrição', 'declarar extinta a punibilidade'.
    """
    m = _RE_FORMULA_ACORDAM_LONGA.search(full_text or "")
    if not m:
        return None
    formula = m.group(0)
    mj = _RE_JULGAR_MERITO.search(formula)
    if mj:
        return "Improcedente / Arquivado" if mj.group(1) else "Procedente"
    if _RE_FORMULA_PRESCRICAO.search(formula):
        return "Prescrição Reconhecida"
    return None


# Marcas de matéria ético-disciplinar (texto sem acento, em maiúsculas). Acórdãos do Conselho sobre inscrição,
# sociedade de advogados, prestação de contas da Seccional ou eleições não têm nenhuma delas.
# "Representação" e "ética" sozinhas ficam de fora: aparecem em matéria institucional (auditoria do AP, nov/2026).
RE_DISCIPLINAR = re.compile(
    r"DISCIPLIN|REABILITA|INFRAC|ART(?:IGO)?S?\.?\s*34\b|CENSURA|TRIBUNAL\s+DE\s+ETICA|\bTED\b|CODIGO\s+DE\s+ETICA|"
    r"LOCUPLET|INIDONE|EXCLUSAO\s+D[OA]S?\s+QUADROS|SUSPENSAO\s+(?:DO\s+EXERCICIO|PREVENTIVA)|CAPTACAO\s+DE\s+CLIENT|ANGARIA"
)


def _sem_acento_upper(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto.upper()) if unicodedata.category(c) != "Mn")


def motivo_recusa(
    pub: dict[str, Any],
    confiar_no_tipo: bool = False,
) -> str | None:
    """Por que uma publicação NÃO vira registro (None = é aceita). Usado também para registrar os rejeitados."""
    if "objeto" in pub and isinstance(pub["objeto"], dict):
        pub = pub["objeto"]
    if not pub.get("id"):
        return "sem_id"
    tipo_pub = clean_text((pub.get("tipoPublicacao") or {}).get("descricao") or "").upper()
    valid_tipos = {"ACÓRDÃO", "ACORDAO", "EMENTA", "DECISÃO", "DECISAO", "RECURSO", "JULGAMENTO"}
    # A varredura já decidiu, pelo perfil da Seccional, que a publicação é decisão: não refiltra pelo nome do tipo
    if not confiar_no_tipo and tipo_pub not in valid_tipos and tipo_pub:
        if any(w in tipo_pub for w in ["CONVOCAÇÃO", "PAUTA", "PORTARIA", "NOTIFICAÇÃO", "EDITAL"]):
            return "tipo_nao_decisorio"
    setor_nome = clean_text((pub.get("setorPublicacao") or {}).get("nomeAlternativo") or "")
    titulo = clean_text(pub.get("titulo") or "")
    full_text = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
    if not full_text or len(full_text) < 150:
        return "texto_vazio_ou_curto"  # inclui erratas de uma linha ("acórdão faltante, não anexado")
    eh_errata = "ERRAT" in _sem_acento_upper(f"{tipo_pub} {titulo}").replace(" ", "")
    if len(full_text) < (1500 if eh_errata else 700) and re.search(r"RETIFIQUE-SE|ONDE\s+SE\s+LE", _sem_acento_upper(full_text)):
        return "errata_sem_decisao"  # só aponta a correção de outra publicação (RR 737522)
    if confiar_no_tipo:
        return None  # a varredura já classificou a publicação como decisão pelo perfil da Seccional
    # Filtrar notificações administrativas puras ("notifica a comparecer")
    txt_lower = full_text.lower()
    has_juris_keywords = any(k in txt_lower for k in ["acordam", "acórdão", "ementa", "vistos", "censura", "suspensão", "exclusão", "representação", "infração", "ced", "eaoab"])
    if not has_juris_keywords and "notifica" in txt_lower:
        return "notificacao_sem_decisao"
    return None


def eh_eleitoral(setor_nome: str, full_text: str) -> bool:
    """Decisões da Comissão Eleitoral e registros de candidatura: ficam no banco, sempre fora da busca do TED."""
    return "COMISSÃO ELEITORAL" in setor_nome.upper() or bool(
        re.search(r"REGISTRO\s+DE\s+(?:CANDIDATURA|CHAPA)|\bCHAPA\b|ELEI[ÇC][ÕO]ES\s+OAB|ELEITORAL", full_text[:600].upper())
    )


# Matéria administrativa do Conselho, reconhecida pelo assunto no título ou na abertura do texto (sem acento).
# "Prestação de contas" só conta quando é a da instituição: a do advogado ao cliente é infração disciplinar.
RE_ADMINISTRATIVO = re.compile(
    r"PRESTACAO\s+DE\s+CONTAS\s+D[AO]S?\s+(?:CAIXA|CAA|CONSELHO|SECCIONAL|SUBSECAO|SUBSECOES|DIRETORIA|EXERCICIO|OAB)|"
    r"PRESTACAO\s+DE\s+CONTAS\s+DE\s+\d{4}|CANCELAMENTO\s+DE\s+INSCRICAO|"
    # auditorias de MT e PA: incidente de inidoneidade é de inscrição; anuidade, desagravo e normas são administrativos
    r"INCIDENTE\s+DE\s+INIDONEIDADE|INIDONEIDADE\s+MORAL|ISENCAO\s+D[AE]\s+ANUIDADE|REMISSAO\s+D[AEO]S?\s+(?:DEBITO|ANUIDADE)|DESAGRAVO|PEDIDO\s+DE\s+PROVIDENCIAS|"
    r"TABELA\s+DE\s+HONORARIOS|"
    r"PEDIDO\s+DE\s+INSCRICAO|INSCRICAO\s+(?:ORIGINARIA|SUPLEMENTAR|PRINCIPAL|DEFINITIVA)|"
    r"SOCIEDADE\s+(?:INDIVIDUAL\s+)?DE\s+ADVO|ATOS\s+SOCIETARIOS"
)


def eh_materia_disciplinar(setor_nome: str, titulo: str, full_text: str) -> bool:
    """Decisões do Conselho sobre inscrição, sociedades, contas ou eleições ficam no banco, mas fora da busca do TED."""
    if eh_eleitoral(setor_nome, full_text):
        return False
    if RE_ADMINISTRATIVO.search(_sem_acento_upper(f"{titulo} {full_text[:600]}")):
        return False
    return bool(RE_DISCIPLINAR.search(_sem_acento_upper(f"{setor_nome} {titulo} {full_text}")))


def normalize_deoab_item_nacional(
    pub: dict[str, Any],
    default_uf: str | None = None,
    default_org_id: int | None = None,
    confiar_no_tipo: bool = False,
) -> dict[str, Any] | None:
    """Normaliza uma publicação do DEOAB para o schema do banco julgados (None se recusada; ver motivo_recusa)."""
    if "objeto" in pub and isinstance(pub["objeto"], dict):
        pub = pub["objeto"]
    if motivo_recusa(pub, confiar_no_tipo=confiar_no_tipo):
        return None
    pub_id = pub.get("id")

    setor_obj = pub.get("setorPublicacao") or {}
    setor_nome = clean_text(setor_obj.get("nomeAlternativo") or "")
    org_info = setor_obj.get("organizacao") or {}
    org_id = org_info.get("id") or setor_obj.get("organizacaoId") or default_org_id

    sec_meta = ORG_TO_UF.get(org_id)
    uf = sec_meta["uf"] if sec_meta else (default_uf or "BR")
    nome_org = sec_meta["nome"] if sec_meta else (org_info.get("nomeAlternativo") or f"OAB/{uf}")

    titulo = clean_text(pub.get("titulo") or "")
    full_text = html_to_paragraphs(pub.get("conteudoCodificado") or pub.get("conteudo") or "")
    txt_lower = full_text.lower()

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
    resultado = detect_resultado(ementa_text, acordao_text) or _resultado_pela_formula(full_text)
    relator = extract_relator_fallback(None, full_text)

    data_pub = (pub.get("dataPublicacao") or "")[:10] or None
    ano_ementa = int(data_pub[:4]) if (data_pub and data_pub[:4].isdigit()) else None
    edicao = pub.get("edicao")
    pagina = pub.get("pagina")
    ano_romano = pub.get("ano") or ""
    pub_str = f"DEOAB/{uf}, Ano {ano_romano}, Ed. {edicao}, p. {pagina} ({data_pub})"

    # Consulta só quando o ÓRGÃO é consultivo (sigla -CONS); "consulta" solta no início do texto não basta
    tipo_ementa = "CONSULTA" if sigla_orgao.endswith("-CONS") else "PROCESSO_DISCIPLINAR"

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
        "is_ted_disciplinar": 1 if eh_materia_disciplinar(setor_nome, titulo, full_text) else 0,
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

