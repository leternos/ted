"""Configurações, endpoints das APIs oficiais da OAB e caminhos locais."""

import os
import ssl
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TED_DATA_DIR", str(PROJECT_ROOT / "data")))
RAW_DIR = DATA_DIR / "raw"
PDFS_DIR = DATA_DIR / "pdfs"
DB_PATH = Path(os.environ.get("TED_DB_PATH", str(DATA_DIR / "ted_jurisprudencia.db")))


def create_async_http_client(
    timeout: float = 45.0,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Cria um AsyncClient httpx compatível com proxy corporativo/VM e CA do sistema."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    cert_file = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=cert_file) if cert_file else True
    default_headers = {"User-Agent": "TEDSearch-OAB-Backfill/1.0"}
    if headers:
        default_headers.update(headers)
    return httpx.AsyncClient(
        proxy=proxy,
        verify=verify,
        trust_env=False,
        timeout=httpx.Timeout(timeout, connect=15.0),
        headers=default_headers,
        follow_redirects=follow_redirects,
    )


# 1. API Nacional de Jurisprudência (jurisprudencia.oab.org.br)
OAB_JURIS_BASE_URL = "https://jurisprudencia.oab.org.br/api/jurisprudencia/v1/portal/pesquisa"
OAB_JURIS_CONTEXTS = [
    "EMENTA",
    "EMENTA_CONSULTA",
    "DECISAO_SUMULA",
]

# 2. API Strapi do Portal Ética e Disciplina (eticaedisciplina.oab.org.br)
ETICA_STRAPI_BASE_URL = "https://services.oab.org.br/etica-disciplina"
ETICA_STRAPI_EMENTARIOS_URL = f"{ETICA_STRAPI_BASE_URL}/api/ementarios"
# Token público de leitura embutido no bundle JS de eticaedisciplina.oab.org.br
ETICA_STRAPI_PUBLIC_TOKEN = (
    "7e08e16b70b6fdbe027d84c93d21669027ac8f24d7980c4edc93f32a9e5fc442"
    "6ef1405923c1e8a0327b47fe1ae07877e5a094d25b786d588d919d53bcf71b5f"
    "150a21ed0451946bb12938459318ddc09d070dddb4b9a0bc447036109f1c59d4"
    "5f592edb380c076a4fcd19b31fd1eafdf7c302e348aaeac7d7c4bd8b9e6ecfa1"
)

# 3. Diário Eletrônico da OAB (DEOAB)
DIARIO_MATERIA_URL = "https://diario.oab.org.br/pages/materia"
DEOAB_API_BASE_URL = "https://diario.oab.org.br/api/v1"
DEOAB_ASSETS_BASE_URL = "https://diario.oab.org.br/assets"

# Mapeamento dos códigos numéricos de Seccionais da OAB no número do processo (XX.0000.AAAA.NNNNNN-D)
OAB_SECCIONAL_PREFIX_MAP = {
    "01": "AC",
    "02": "AL",
    "03": "AP",
    "04": "AM",
    "05": "BA",
    "06": "CE",
    "07": "DF",
    "08": "ES",
    "09": "GO",
    "10": "MA",
    "11": "MT",
    "12": "MS",
    "13": "MG",
    "14": "PA",
    "15": "PB",
    "16": "PR",
    "17": "PE",
    "18": "PI",
    "19": "RJ",
    "20": "RN",
    "21": "RS",
    "22": "RO",
    "23": "RR",
    "24": "SC",
    "25": "SP",
    "26": "SE",
    "27": "TO",
}

ESTADOS_NOME_PARA_UF = {
    "ACRE": "AC",
    "ALAGOAS": "AL",
    "AMAPA": "AP",
    "AMAPÁ": "AP",
    "AMAZONAS": "AM",
    "BAHIA": "BA",
    "CEARA": "CE",
    "CEARÁ": "CE",
    "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES",
    "ESPÍRITO SANTO": "ES",
    "GOIAS": "GO",
    "GOIÁS": "GO",
    "MARANHAO": "MA",
    "MARANHÃO": "MA",
    "MATO GROSSO DO SUL": "MS",
    "MATO GROSSO": "MT",
    "MINAS GERAIS": "MG",
    "PARAIBA": "PB",
    "PARAÍBA": "PB",
    "PARANA": "PR",
    "PARANÁ": "PR",
    "PARA": "PA",
    "PARÁ": "PA",
    "PERNAMBUCO": "PE",
    "PIAUI": "PI",
    "PIAUÍ": "PI",
    "RIO DE JANEIRO": "RJ",
    "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS",
    "RONDONIA": "RO",
    "RONDÔNIA": "RO",
    "RORAIMA": "RR",
    "SANTA CATARINA": "SC",
    "SAO PAULO": "SP",
    "SÃO PAULO": "SP",
    "SERGIPE": "SE",
    "TOCANTINS": "TO",
}
