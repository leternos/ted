"""Testes unitários para o coletor nacional DEOAB e normalização de acórdãos seccionais."""

from ted_search.collectors.deoab_nacional import (
    detect_orgao_julgador_nacional,
    normalize_deoab_item_nacional,
)


def test_detect_orgao_julgador_nacional():
    # Teste Câmara Recursal/Disciplinar
    sigla, norm = detect_orgao_julgador_nacional("PR", "CÂMARA DE DISCIPLINA", "Acórdão 123", "Acordam os membros da 2ª Câmara...")
    assert sigla == "OABPR-2CR"
    assert "Câmara Recursal/Disciplinar" in norm

    # Teste TED Turma
    sigla_ted, norm_ted = detect_orgao_julgador_nacional("BA", "TRIBUNAL DE ÉTICA E DISCIPLINA", "Acórdão 456", "Sétima Turma Tribunal de Ética e Disciplina")
    assert sigla_ted == "OABBA-TED-T7"
    assert "7ª Turma Disciplinar" in norm_ted

    # Teste Órgão Especial
    sigla_oe, norm_oe = detect_orgao_julgador_nacional("MG", "Órgão Especial", "Decisão", "O Órgão Especial da OAB/MG...")
    assert sigla_oe == "OABMG-OE"
    assert "Órgão Especial" in norm_oe


def test_normalize_deoab_item_nacional():
    raw_pub = {
        "id": 999999,
        "ano": "VI",
        "edicao": "1234",
        "pagina": 15,
        "dataPublicacao": "2024-05-10",
        "tipoPublicacao": {"descricao": "Acórdão"},
        "setorPublicacao": {
            "organizacaoId": 21,
            "nomeAlternativo": "Tribunal de Ética e Disciplina",
            "organizacao": {"id": 21, "nomeAlternativo": "Conselho Seccional - Rio Grande do Sul"},
        },
        "titulo": "ACÓRDÃO Nº 123/2024",
        "conteudo": (
            "<p>Processo n. 1101020.00085689/2021-20</p>"
            "<p>EMENTA TED Nº 2036/2023: INFRAÇÃO AO ARTIGO 34, INCISO XX DO EAOAB. "
            "APLICAÇÃO DA PENA DE SUSPENSÃO POR 90 DIAS.</p>"
            "<p>Vistos, relatados e discutidos estes autos, acordam os membros da Turma Julgadora...</p>"
        ),
    }

    norm = normalize_deoab_item_nacional(raw_pub)
    assert norm is not None
    assert norm["id"] == "DEOAB_RS:999999"
    assert norm["uf_origem"] == "RS"
    assert norm["numero_processo"] == "1101020.00085689/2021-20"
    assert norm["ano_ementa"] == 2024
    assert norm["sancao_identificada"] == "Suspensão"
    assert norm["is_ted_disciplinar"] == 1
    assert norm["tem_inteiro_teor"] == 1
    assert "EAOAB - Art. 34" in norm["dispositivos_texto"]
