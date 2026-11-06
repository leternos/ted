"""Testes unitários do Parser Jurídico (legado 1994-2018 e acórdãos recentes 2019-2026)."""

from ted_search.collectors.oab_juris import normalize_oab_juris_item
from ted_search.parser import (
    detect_orgao_julgador,
    detect_resultado,
    detect_sancao,
    detect_uf_origem,
    extract_dispositivos,
    normalize_numero_ementa,
    split_legacy_ementa_acordao,
)


def test_parse_legacy_2012_item():
    raw_legacy = {
        "tipoEmenta": "OUTRO",
        "numeroProcesso": "49.0000.2011.005219-8",
        "orgaoJulgador": {
            "_id": "49",
            "idOrganizacao": 49,
            "nomeOrganizacao": "Conselho Federal",
        },
        "numeroEmenta": None,
        "descricaoEmenta": (
            "RECURSO 49.0000.2011.005219-8/SCA-TTU. Recte.: M.K.S.N. (Adv.: Marilane Koerich de Souza Nobre "
            "OAB/SC 7573). Recdos.: Conselho Seccional da OAB/Santa Catarina e D.V.A. "
            "Relator: Conselheiro Federal Ulisses César Martins de Sousa (MA). "
            "Relator &quot;ad hoc&quot;: Conselheiro Federal Mauro José Ribas (TO). "
            "EMENTA 035/2012/SCA-TTU. Recurso ao Conselho Federal. Decisão não unânime de Conselho Seccional. "
            "Infração ao art. 34, incisos XX e XXI da Lei 8.906/94 (EAOAB). Pena de suspensão. "
            "Recurso não conhecido. "
            "ACÓRDÃO: Vistos, relatados e discutidos os autos do processo em referência, acordam os membros da "
            "Terceira Turma da Segunda Câmara do CFOAB, por unanimidade de votos, em não conhecer do recurso, "
            "nos termos do voto do Relator, que integra o presente. Brasília, 6 de março de 2012. "
            "(DOU. 11/04/2012, S. 1, P. 203)"
        ),
        "descricaoAcordao": "",
        "dataDecisao": "2012-04-11T03:00:00.000+0000",
        "documentos": [],
        "relatores": [
            {
                "idMembro": 1669,
                "tipo": "Relator",
                "nome": "MAURO JOSE RIBAS",
                "cargo": "Membro",
            }
        ],
        "dispositivosNormativos": [],
        "partes": [],
    }

    norm = normalize_oab_juris_item(raw_legacy, "EMENTA")
    assert norm["numero_ementa"] == "035/2012/SCA-TTU"
    assert norm["sigla_orgao"] == "SCA-TTU"
    assert norm["orgao_julgador_norm"] == "3ª Turma da 2ª Câmara"
    assert norm["uf_origem"] == "SC"
    assert norm["sancao_identificada"] == "Suspensão"
    assert norm["resultado_julgamento"] == "Não Conhecido"
    assert norm["is_ted_disciplinar"] == 1
    assert norm["tipo_ementa"] == "PROCESSO_DISCIPLINAR"
    assert "ACÓRDÃO:" not in norm["descricao_ementa"]
    assert "Terceira Turma da Segunda Câmara" in norm["descricao_acordao"]
    assert "DOU. 11/04/2012" in (norm["publicacao_str"] or "")
    assert "EAOAB - Art. 34 inciso XX" in norm["dispositivos_texto"]
    assert "EAOAB - Art. 34 inciso XXI" in norm["dispositivos_texto"]


def test_extract_dispositivos_and_sancao():
    texto = (
        "EMENTA N. 003/2026/SCA. PEDIDO DE REVISÃO. ART. 73, § 5º, EAOAB. "
        "Desclassificando a conduta para o inciso IX do artigo 34 do Estatuto da Advocacia e da OAB, "
        "cominando ao recorrente a sanção disciplinar de censura, convertida em advertência, em ofício reservado. "
        "Recurso ao qual se dá parcial provimento."
    )
    disps = extract_dispositivos([], texto)
    assert "EAOAB - Art. 34 inciso IX" in disps
    assert "EAOAB - Art. 73, § 5º (Revisão Disciplinar)" in disps
    assert detect_sancao(texto) == "Advertência (Ofício Reservado)"
    assert detect_resultado(texto, "") == "Parcialmente Provido"


def test_uf_prefix_detection():
    assert detect_uf_origem("24.0000.2023.000001-6", [], "") == "SC"
    assert detect_uf_origem("25.0000.2022.000907-6", [], "") == "SP"
    assert detect_uf_origem("07.0000.2016.018084-4", [], "") == "DF"
