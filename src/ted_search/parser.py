"""Parser Jurídico (Regex/NLP) para enriquecimento de julgados do CFOAB / TED (1994-2026).

Resolve a desestruturação dos ~19.702 julgados históricos migrados do Ementário Global,
extraindo Órgão Julgador (Câmaras e Turmas), Número da Ementa, Dispositivos Normativos
(EAOAB / CED / RG), Sanção Disciplinar, Resultado do Julgamento, Relatores e UF de Origem.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ted_search.config import ESTADOS_NOME_PARA_UF, OAB_SECCIONAL_PREFIX_MAP
from ted_search.ocr_cleaner import clean_ocr_text

# Mapeamento canônico de siglas de Órgãos Julgadores do CFOAB
ORGAO_SIGLA_PARA_NOME = {
    "SCA": "Segunda Câmara",
    "SCA-PTU": "1ª Turma da 2ª Câmara",
    "SCA-STU": "2ª Turma da 2ª Câmara",
    "SCA-TTU": "3ª Turma da 2ª Câmara",
    "OEP": "Órgão Especial",
    "COP": "Conselho Pleno",
    "PCA": "Primeira Câmara",
    "TCA": "Terceira Câmara",
    "CNEJ": "Comissão Nacional de Educação Jurídica",
    "CNDH": "Comissão Nacional de Direitos Humanos",
    "DIR": "Diretoria do Conselho Federal",
}

VALID_ROMAN_UP_TO_XXX = {
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX",
}

# Regexes compiladas para alta performance no backfill de 24 mil documentos
RE_NUMERO_EMENTA = re.compile(
    r"\b(?:EMENTA|S[ÚU]MULA)\s*(?:N[º°.]?\s*)?(\d{1,4}\s*/\s*(?:19\d{2}|20\d{2})(?:\s*[-/]\s*[A-Z0-9/\-]+)?)",
    re.IGNORECASE,
)

RE_EMENTA_INLINE_CODE = re.compile(
    r"\b(\d{3,4}/(?:19\d{2}|20\d{2})[-/](?:SCA(?:[-/][PST]TU)?|OEP|COP|PCA|TCA))\b",
    re.IGNORECASE,
)

RE_PROCESSO_SUFIXO_ORGAO = re.compile(
    r"\d{2}\.\d{4}\.\d{4}\.\d{6}-\d\s*/\s*(SCA(?:\s*[-/]\s*[PST]TU)?|OEP|COP|PCA|TCA)\b",
    re.IGNORECASE,
)

RE_PUBLICACAO_FINAL = re.compile(
    r"\(\s*((?:D\.?O\.?U\.?|D\.?J\.?|DEOAB)[^)]+)\)\s*$",
    re.IGNORECASE,
)

RE_EAOAB_ART34_BLOCK = re.compile(
    r"(?:incisos?\s+([IVX]+(?:\s*(?:,|e|c/c|a)\s*[IVX]+)*)\s+do\s+art(?:igo|\.)?\s*34"
    r"|art(?:igo|\.)?\s*34\s*,\s*(?:incisos?\s*)?([IVX]+(?:\s*(?:,|e|c/c)\s*[IVX]+)*))",
    re.IGNORECASE,
)

RE_ROMAN_TOKEN = re.compile(r"\b([IVX]{1,6})\b", re.IGNORECASE)

RE_OTHER_NORMS = [
    (re.compile(r"\bart(?:igo|\.)?\s*36\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 36 (Censura)"),
    (re.compile(r"\bart(?:igo|\.)?\s*37\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 37 (Suspensão)"),
    (re.compile(r"\bart(?:igo|\.)?\s*38\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 38 (Exclusão)"),
    (re.compile(r"\bart(?:igo|\.)?\s*39\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 39 (Multa)"),
    (re.compile(r"\bart(?:igo|\.)?\s*40\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 40 (Atenuantes)"),
    (re.compile(r"\bart(?:igo|\.)?\s*41\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 41 (Reabilitação)"),
    (re.compile(r"\bart(?:igo|\.)?\s*43\b", re.I), "EAOAB - Art. 43 (Prescrição)"),
    (re.compile(r"\bart(?:igo|\.)?\s*70\s*,\s*§\s*3[º°]?", re.I), "EAOAB - Art. 70, § 3º (Suspensão Preventiva)"),
    (re.compile(r"\bart(?:igo|\.)?\s*73\s*,\s*§\s*5[º°]?", re.I), "EAOAB - Art. 73, § 5º (Revisão Disciplinar)"),
    (re.compile(r"\bart(?:igo|\.)?\s*75\b.*?(?:EAOAB|Estatuto|8\.906)", re.I | re.S), "EAOAB - Art. 75 (Recurso ao CFOAB)"),
    (re.compile(r"\bart(?:igo|\.)?\s*89-A\b", re.I), "RG - Art. 89-A (Recurso na Segunda Câmara)"),
]

RE_OAB_UF_EXPLICIT = re.compile(
    r"\bOAB\s*/\s*(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b",
    re.IGNORECASE,
)

RE_CONSELHO_SECCIONAL_ESTADO = re.compile(
    r"Conselho\s+Seccional\s+da\s+OAB\s*/\s*([A-Za-zÀ-ÿ\s]{2,22}?)(?:[.,;)\-\n]|$)",
    re.IGNORECASE,
)

RE_RELATOR_TEXT = re.compile(
    r"Relator(?:a)?(?:\s+(?:ad\s+hoc|p/\s*ac[óo]rd[ãa]o))?\s*:\s*(?:Conselheir[oa]\s+Federal\s+)?([A-ZÀ-Ÿa-zà-ÿ\s.'\-]{5,70}?)(?:\s*\([A-Z]{2}\)|\s*[.\-\n]|$)",
    re.IGNORECASE,
)


def clean_text(raw: str | None) -> str:
    """Desescapa entidades HTML, higieniza ruídos de OCR e normaliza quebras de linha."""
    return clean_ocr_text(raw, multiline=True)


def normalize_numero_ementa(raw_num: str | None, text: str) -> str | None:
    """Normaliza ou extrai o número da ementa (ex: '035/2012/SCA-TTU')."""
    candidate = clean_text(raw_num) if raw_num else ""
    if not candidate:
        m = RE_NUMERO_EMENTA.search(text)
        if m:
            candidate = m.group(1)
        else:
            m2 = RE_EMENTA_INLINE_CODE.search(text)
            if m2:
                candidate = m2.group(1)
    if not candidate:
        return None
    candidate = re.sub(r"\s+", "", candidate).upper()
    candidate = candidate.rstrip(".,;:")
    return candidate


def detect_orgao_julgador(
    orgao_obj: dict[str, Any] | None,
    numero_ementa: str | None,
    text: str,
    camara_strapi: str | None = None,
) -> tuple[str, str]:
    """Retorna (sigla_orgao, orgao_julgador_norm) combinando metadados e análise textual."""
    nome_setor = clean_text((orgao_obj or {}).get("nomeSetor") or "")
    nome_org = clean_text((orgao_obj or {}).get("nomeOrganizacao") or "Conselho Federal")

    # 1. Verificar campo vindo do Strapi (Portal Ética e Disciplina)
    if camara_strapi:
        cs = camara_strapi.upper()
        if "PRIMEIRA TURMA" in cs or "1ª TURMA" in cs:
            return "SCA-PTU", ORGAO_SIGLA_PARA_NOME["SCA-PTU"]
        if "SEGUNDA TURMA" in cs or "2ª TURMA" in cs:
            return "SCA-STU", ORGAO_SIGLA_PARA_NOME["SCA-STU"]
        if "TERCEIRA TURMA" in cs or "3ª TURMA" in cs:
            return "SCA-TTU", ORGAO_SIGLA_PARA_NOME["SCA-TTU"]
        if "SEGUNDA CÂMARA" in cs or "SEGUNDA CAMARA" in cs:
            return "SCA", ORGAO_SIGLA_PARA_NOME["SCA"]

    # 2. Verificar nomeSetor explícito da API OAB Jurisprudência
    if nome_setor:
        ns_upper = nome_setor.upper()
        if "PRIMEIRA TURMA DA SEGUNDA" in ns_upper:
            return "SCA-PTU", ORGAO_SIGLA_PARA_NOME["SCA-PTU"]
        if "SEGUNDA TURMA DA SEGUNDA" in ns_upper:
            return "SCA-STU", ORGAO_SIGLA_PARA_NOME["SCA-STU"]
        if "TERCEIRA TURMA DA SEGUNDA" in ns_upper:
            return "SCA-TTU", ORGAO_SIGLA_PARA_NOME["SCA-TTU"]
        if "SEGUNDA CÂMARA" in ns_upper or "SEGUNDA CAMARA" in ns_upper:
            return "SCA", ORGAO_SIGLA_PARA_NOME["SCA"]
        if "PRIMEIRA CÂMARA" in ns_upper or "PRIMEIRA CAMARA" in ns_upper:
            return "PCA", ORGAO_SIGLA_PARA_NOME["PCA"]
        if "TERCEIRA CÂMARA" in ns_upper or "TERCEIRA CAMARA" in ns_upper:
            return "TCA", ORGAO_SIGLA_PARA_NOME["TCA"]
        if "ÓRGÃO ESPECIAL" in ns_upper or "ORGAO ESPECIAL" in ns_upper:
            return "OEP", ORGAO_SIGLA_PARA_NOME["OEP"]
        if "CONSELHO PLENO" in ns_upper:
            return "COP", ORGAO_SIGLA_PARA_NOME["COP"]
        if "EDUCAÇÃO JURÍDICA" in ns_upper:
            return "CNEJ", ORGAO_SIGLA_PARA_NOME["CNEJ"]
        if "DIREITOS HUMANOS" in ns_upper:
            return "CNDH", ORGAO_SIGLA_PARA_NOME["CNDH"]
        if "DIRETORIA" in ns_upper:
            return "DIR", ORGAO_SIGLA_PARA_NOME["DIR"]

    # 3. Extrair do código da Ementa ou do sufixo do Número do Processo no texto
    combined_codes = f"{numero_ementa or ''} {text[:600]}".upper()
    if re.search(r"\bSCA\s*[-/]\s*PTU\b|\b1ª\s*TURMA\s+DA\s+(?:SEGUNDA|2ª)\s+C[ÂA]MARA", combined_codes):
        return "SCA-PTU", ORGAO_SIGLA_PARA_NOME["SCA-PTU"]
    if re.search(r"\bSCA\s*[-/]\s*STU\b|\b2ª\s*TURMA\s+DA\s+(?:SEGUNDA|2ª)\s+C[ÂA]MARA", combined_codes):
        return "SCA-STU", ORGAO_SIGLA_PARA_NOME["SCA-STU"]
    if re.search(r"\bSCA\s*[-/]\s*TTU\b|\b3ª\s*TURMA\s+DA\s+(?:SEGUNDA|2ª)\s+C[ÂA]MARA", combined_codes):
        return "SCA-TTU", ORGAO_SIGLA_PARA_NOME["SCA-TTU"]

    # 4. Inspecionar menções por extenso no texto (ementa + acórdão)
    txt_upper = text.upper()
    if "PRIMEIRA TURMA DA SEGUNDA CÂMARA" in txt_upper or "PRIMEIRA TURMA DA SEGUNDA CAMARA" in txt_upper:
        return "SCA-PTU", ORGAO_SIGLA_PARA_NOME["SCA-PTU"]
    if "SEGUNDA TURMA DA SEGUNDA CÂMARA" in txt_upper or "SEGUNDA TURMA DA SEGUNDA CAMARA" in txt_upper:
        return "SCA-STU", ORGAO_SIGLA_PARA_NOME["SCA-STU"]
    if "TERCEIRA TURMA DA SEGUNDA CÂMARA" in txt_upper or "TERCEIRA TURMA DA SEGUNDA CAMARA" in txt_upper:
        return "SCA-TTU", ORGAO_SIGLA_PARA_NOME["SCA-TTU"]

    if re.search(r"[/\-]SCA\b", combined_codes) or "SEGUNDA CÂMARA" in txt_upper or "2ª CÂMARA" in txt_upper:
        return "SCA", ORGAO_SIGLA_PARA_NOME["SCA"]
    if re.search(r"[/\-]OEP\b", combined_codes) or "ÓRGÃO ESPECIAL" in txt_upper or "ORGAO ESPECIAL" in txt_upper:
        return "OEP", ORGAO_SIGLA_PARA_NOME["OEP"]
    if re.search(r"[/\-]COP\b", combined_codes) or "CONSELHO PLENO" in txt_upper:
        return "COP", ORGAO_SIGLA_PARA_NOME["COP"]
    if re.search(r"[/\-]PCA\b", combined_codes) or "PRIMEIRA CÂMARA" in txt_upper or "1ª CÂMARA" in txt_upper:
        return "PCA", ORGAO_SIGLA_PARA_NOME["PCA"]
    if re.search(r"[/\-]TCA\b", combined_codes) or "TERCEIRA CÂMARA" in txt_upper or "3ª CÂMARA" in txt_upper:
        return "TCA", ORGAO_SIGLA_PARA_NOME["TCA"]

    if nome_setor:
        return "CFOAB", f"{nome_org} / {nome_setor}"
    return "CFOAB", "Conselho Federal (Geral)"


def extract_dispositivos(existing_list: list[dict[str, Any]] | None, text: str) -> list[str]:
    """Combina dispositivos vindos da API com os extraídos via Regex do texto da ementa/acórdão."""
    found: set[str] = set()
    for item in existing_list or []:
        desc = clean_text(item.get("descricao") or "")
        if desc:
            # Padroniza "EAOAB - Art. 34 inciso XX"
            found.add(desc)

    # Extrair incisos do Art. 34 do EAOAB mencionados no texto
    for match in RE_EAOAB_ART34_BLOCK.finditer(text):
        roman_chunk = match.group(1) or match.group(2) or ""
        for r_match in RE_ROMAN_TOKEN.finditer(roman_chunk):
            roman = r_match.group(1).upper()
            if roman in VALID_ROMAN_UP_TO_XXX:
                found.add(f"EAOAB - Art. 34 inciso {roman}")

    # Extrair outros artigos chave do processo ético-disciplinar
    for regex, label in RE_OTHER_NORMS:
        if regex.search(text):
            found.add(label)

    return sorted(found)


def detect_sancao(text: str) -> str | None:
    """Identifica a principal sanção ético-disciplinar discutida ou aplicada no julgado."""
    t = text.lower()
    if "exclusão dos quadros" in t or "pena de exclusão" in t or "sanção de exclusão" in t or "art. 38" in t:
        return "Exclusão"
    if "suspensão preventiva" in t or "art. 70, § 3" in t:
        return "Suspensão Preventiva"
    if (
        "pena de suspensão" in t
        or "sanção de suspensão" in t
        or "sanção disciplinar de suspensão" in t
        or "suspensão do exercício profissional" in t
    ):
        return "Suspensão"
    if "convertida em advertência" in t or "ofício reservado" in t:
        return "Advertência (Ofício Reservado)"
    if "pena de censura" in t or "sanção de censura" in t or "sanção disciplinar de censura" in t:
        return "Censura"
    if "pena de multa" in t or "sanção de multa" in t or "cumulada com multa" in t:
        return "Multa"
    return None


def detect_resultado(text_ementa: str, text_acordao: str) -> str | None:
    """Identifica o resultado do julgamento a partir da parte final da ementa e do acórdão."""
    tail = f"{text_ementa[-600:]}\n{text_acordao}".lower()

    if re.search(r"prescri[çc][ãa]o\s+(?:intercorrente\s+|quinquenal\s+|da\s+pretens[ãa]o\s+punitiva\s+)?(?:que\s+se\s+declara|declarada|reconhecida|acolhida|pronunciada)", tail):
        return "Prescrição Reconhecida"
    if re.search(r"\b(?:parcial\s+provimento|provimento\s+parcial|parcialmente\s+provido|dar\s+parcial\s+provimento)\b", tail):
        return "Parcialmente Provido"
    if re.search(r"\b(?:negar\s+provimento|nego\s+provimento|recurso\s+improvido|recurso\s+desprovido|improvimento|desprovimento|não\s+provido)\b", tail):
        return "Improvido"
    if re.search(r"\b(?:dar\s+provimento|dou\s+provimento|recurso\s+provido|provimento\s+ao\s+recurso)\b", tail):
        return "Provido"
    if re.search(r"\b(?:n[ãa]o\s+conhecid[oa]|n[ãa]o\s+conhecer|n[ãa]o\s+conhecimento)\b", tail):
        return "Não Conhecido"
    if re.search(r"\b(?:embargos\s+.*?\s+rejeitados|rejeitar\s+os\s+embargos)\b", tail):
        return "Embargos Rejeitados"
    if re.search(r"\b(?:embargos\s+.*?\s+acolhidos|acolher\s+os\s+embargos)\b", tail):
        return "Embargos Acolhidos"
    if re.search(r"\b(?:anular\s+o\s+julgamento|nulidade\s+decretada|nulidade\s+acolhida)\b", tail):
        return "Anulado / Nulidade"
    if re.search(r"\b(?:consulta\s+respondida|conhecer\s+da\s+consulta)\b", tail):
        return "Consulta Respondida"
    return None


def detect_uf_origem(numero_processo: str | None, partes: list[dict[str, Any]] | None, text: str) -> str | None:
    """Extrai a UF da Seccional de origem pelo código do processo, partes ou texto."""
    if numero_processo:
        prefix = numero_processo.strip()[:2]
        if prefix in OAB_SECCIONAL_PREFIX_MAP:
            return OAB_SECCIONAL_PREFIX_MAP[prefix]

    for p in partes or []:
        nome_p = clean_text(p.get("nome") or "")
        m_uf = RE_OAB_UF_EXPLICIT.search(nome_p)
        if m_uf:
            return m_uf.group(1).upper()
        for estado_nome, uf_code in ESTADOS_NOME_PARA_UF.items():
            if estado_nome in nome_p.upper():
                return uf_code

    # Procurar no início do texto (cabeçalho de Origem / Recorrido)
    header = text[:800]
    m_cs = RE_CONSELHO_SECCIONAL_ESTADO.search(header)
    if m_cs:
         candidate = m_cs.group(1).strip().upper()
         if len(candidate) == 2 and candidate in OAB_SECCIONAL_PREFIX_MAP.values():
             return candidate
         for estado_nome, uf_code in ESTADOS_NOME_PARA_UF.items():
             if estado_nome in candidate:
                 return uf_code

    m_uf = RE_OAB_UF_EXPLICIT.search(header)
    if m_uf:
        return m_uf.group(1).upper()

    return None


def split_legacy_ementa_acordao(descricao_ementa: str, descricao_acordao: str) -> tuple[str, str, str | None]:
    """Separa ACÓRDÃO e Publicação (DOU/DJ) quando estão concatenados na descricaoEmenta legada."""
    ementa = clean_text(descricao_ementa)
    acordao = clean_text(descricao_acordao)
    pub_extra = None

    # Extrair publicação no final: ex. (DOU. 11/04/2012, S. 1, P. 203)
    m_pub = RE_PUBLICACAO_FINAL.search(ementa)
    if m_pub:
        pub_extra = m_pub.group(1).strip()

    if not acordao:
        m_ac = re.search(r"\bAC[ÓO]RD[ÃA]O\s*:\s*(.+)$", ementa, re.IGNORECASE | re.DOTALL)
        if m_ac:
            acordao = m_ac.group(1).strip()
            ementa = ementa[: m_ac.start()].strip()

    return ementa, acordao, pub_extra


def extract_relator_fallback(relatores_api: list[dict[str, Any]] | None, text: str) -> str | None:
    """Obtém o nome do relator principal da API ou via Regex no texto legado."""
    if relatores_api:
        for r in relatores_api:
            nome = clean_text(r.get("nome") or "")
            if nome:
                return nome.upper()
    m = RE_RELATOR_TEXT.search(text[:800])
    if m:
        return clean_text(m.group(1)).upper()
    return None


def classify_is_ted_disciplinar(
    sigla_orgao: str,
    tipo_ementa: str | None,
    dispositivos: list[str],
    sancao: str | None,
    text: str,
) -> bool:
    """Determina se o julgado pertence à temática Ético-Disciplinar / TED do CFOAB."""
    if sigla_orgao in {"SCA", "SCA-PTU", "SCA-STU", "SCA-TTU"}:
        return True
    if tipo_ementa in {"PROCESSO_DISCIPLINAR", "INIDONEIDADE"}:
        return True
    if sancao is not None:
        return True
    if any("Art. 34" in d or "CED" in d or "Suspensão" in d or "Prescrição" in d for d in dispositivos):
        return True
    t_low = text.lower()
    keywords = (
        "processo disciplinar",
        "ético-disciplinar",
        "etico-disciplinar",
        "tribunal de ética",
        "tribunal de etica",
        "infração disciplinar",
        "infracao disciplinar",
        "código de ética",
        "sanção disciplinar",
        "representação disciplinar",
    )
    return any(k in t_low for k in keywords)
