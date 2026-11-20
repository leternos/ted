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


def test_oabsp_turmas_and_camaras_detection():
    from ted_search.collectors.deoab_sp import normalize_deoab_sp_item

    # 1. Câmara Recursal da OAB/SP (2ª instância seccional)
    sigla_cr, nome_cr = detect_orgao_julgador(
        {"idOrganizacao": 25, "nomeOrganizacao": "Conselho Seccional - São Paulo", "nomeSetor": "Primeira Câmara Recursal"},
        "ACÓRDÃO N. 25360",
        "Vistos, relatados e discutidos estes autos, acordam os membros da Primeira Câmara Recursal do Conselho Seccional de São Paulo.",
    )
    assert sigla_cr == "OABSP-1CR"
    assert nome_cr == "OAB/SP - 1ª Câmara Recursal"

    # 2. Turma Disciplinar por extenso no corpo do acórdão (ex: Vigésima Terceira Turma Disciplinar)
    sigla_td, nome_td = detect_orgao_julgador(
        {"idOrganizacao": 25, "nomeOrganizacao": "Conselho Seccional - São Paulo", "nomeSetor": "Tribunal de Ética e Disciplina"},
        "EMENTA: LOCUPLETAMENTO E AUSÊNCIA DE PRESTAÇÃO DE CONTAS.",
        "Vistos, relatados e discutidos estes autos, a Vigésima Terceira Turma Disciplinar do Tribunal de Ética e Disciplina da OAB/SP decide julgar procedente a representação.",
    )
    assert sigla_td == "OABSP-23TD"
    assert nome_td == "OAB/SP - 23ª Turma Disciplinar (TED)"

    # 3. Turma Deontológica (TED-I)
    sigla_deont, nome_deont = detect_orgao_julgador(
        {"idOrganizacao": 25, "nomeOrganizacao": "Conselho Seccional - São Paulo", "nomeSetor": "Tribunal de Ética e Disciplina"},
        "CONSULTA DEONTOLÓGICA. PUBLICIDADE PROFISSIONAL.",
        "Acordam os membros da Primeira Turma Deontológica do Tribunal de Ética e Disciplina da OAB/SP.",
    )
    assert sigla_deont == "OABSP-TDEONT"
    assert nome_deont == "OAB/SP - Turma Deontológica (TED-I)"

    # 4. Normalização completa de publicação DEOAB OAB/SP
    raw_deoab = {
        "objeto": {
            "id": 999888,
            "edicao": 1839,
            "titulo": "ACÓRDÃO Nº 14855 - 25.0886.2024.012477-3",
            "dataPublicacao": "2026-04-06T00:00:00",
            "organizacao": {"id": 25, "uf": "SP", "nome": "Conselho Seccional - São Paulo"},
            "setorPublicacao": {"nomeAlternativo": "Tribunal de Ética e Disciplina"},
            "tipoPublicacao": {"descricao": "Acórdão"},
            "conteudoCodificado": (
                "<p>Processo Disciplinar nº 25.0886.2024.012477-3</p>"
                "<p>EMENTA: PRESTAÇÃO DE CONTAS. LOCUPLETAMENTO. INFRAÇÃO AO ART. 34, INCISOS XX E XXI DO EAOAB. "
                "Representação julgada procedente para aplicar a pena de suspensão.</p>"
                "<p>ACÓRDÃO N. 14855: Vistos, relatados e discutidos estes autos, a Décima Quarta Turma Disciplinar "
                "do Tribunal de Ética e Disciplina da Ordem dos Advogados do Brasil, Seção de São Paulo, por unanimidade, "
                "nos termos do voto do Relator, em julgar procedente a representação e aplicar a pena de suspensão.</p>"
                "<p>GUSTAVO MOSSO PEREIRA</p><p>Relator</p>"
            ),
        }
    }
    norm = normalize_deoab_sp_item(raw_deoab)
    assert norm is not None
    assert norm["id"] == "DEOAB_SP:999888"
    assert norm["id_organizacao"] == 25
    assert norm["sigla_orgao"] == "OABSP-14TD"
    assert norm["orgao_julgador_norm"] == "OAB/SP - 14ª Turma Disciplinar (TED)"
    assert norm["numero_processo"] == "25.0886.2024.012477-3"
    assert norm["numero_ementa"] == "AC-14855/OABSP-14TD"
    assert norm["sancao_identificada"] == "Suspensão"
    assert norm["resultado_julgamento"] == "Procedente"
    assert norm["pdf_url"] == "/api/diario-pdf/999888"
    assert norm["tem_inteiro_teor"] == 1
    assert "EAOAB - Art. 34 inciso XX" in norm["dispositivos_texto"]
    assert "EAOAB - Art. 34 inciso XXI" in norm["dispositivos_texto"]


def test_ocr_cleaner_repairs():
    from ted_search.ocr_cleaner import clean_ocr_text

    corrupted = (
        '<p style="text-align:justify;"><i>doá Q 9Ídiío^adoíí do\n'
        "Embora próprio e tempestivo o reeurso, na Segunda Câ- mara^, "
        "verifieando- se ausêneia de pressupostos espeeífieos no proeesso ético- disciplinar "
        "da Seeeional quanto à incompete^ncia territorial e ac¸a~o disciplinar no art.34, ineiso XX do EAOA8, "
        "eireunstâneia que impõe o não eonheeimento do aeórdão.</i></p>"
    )
    cleaned = clean_ocr_text(corrupted)
    assert "doá Q 9Ídiío" not in cleaned
    assert "<p" not in cleaned and "<i>" not in cleaned
    assert "recurso" in cleaned
    assert "Segunda Câmara," in cleaned
    assert "verificando-se" in cleaned
    assert "ausência" in cleaned
    assert "específicos" in cleaned
    assert "processo ético-disciplinar" in cleaned
    assert "Seccional" in cleaned
    assert "incompetência" in cleaned
    assert "ação" in cleaned
    assert "art. 34, inciso XX do EAOAB" in cleaned
    assert "circunstância" in cleaned
    assert "não conhecimento do acórdão" in cleaned




def test_oabsp_deontologica_nao_e_inferida_de_mencao_solta():
    """Regressão: acórdãos disciplinares citando 'deontológica' / 'ética profissional' não são consultas."""
    from ted_search.collectors.deoab_sp import normalize_deoab_sp_item
    from ted_search.parser import detect_orgao_julgador_oabsp

    # Adjetivo solto no corpo de acórdão de Turma Disciplinar (cabeçalho nomeia o órgão)
    sigla, _ = detect_orgao_julgador_oabsp(
        "Tribunal de Ética e Disciplina",
        "ACÓRDÃO Nº 11152 – SEGUNDA TURMA DISCIPLINAR DO TED",
        "EMENTA: REPRESENTAÇÃO. NÃO SIGNIFICA IMUNIDADE AO CONTROLE DEONTOLÓGICO DO ÓRGÃO DE CLASSE.",
    )
    assert sigla == "OABSP-2TD"

    # Turma Deontológica citada como precedente, julgamento pela 4ª Turma Disciplinar
    sigla, _ = detect_orgao_julgador_oabsp(
        "Tribunal de Ética e Disciplina",
        "ACÓRDÃO No 9873 - QUARTA TURMA DISCIPLINAR DO TED",
        "Conforme entendimento pacificado da C. Primeira Turma Deontológica deste Tribunal de Ética e Disciplina.",
    )
    assert sigla == "OABSP-4TD"

    # Câmara Recursal (OAB Jurisprudência) com "ÉTICA PROFISSIONAL" na ementa
    sigla, _ = detect_orgao_julgador(
        {"idOrganizacao": 25, "nomeOrganizacao": "Conselho Seccional - São Paulo", "nomeSetor": "Terceira Câmara Recursal"},
        None,
        "ÉTICA PROFISSIONAL – ADVOGADO – PESSOA IDOSA E VULNERÁVEL – DESÍDIA NA CONDUÇÃO DO PROCESSO.",
    )
    assert sigla == "OABSP-3CR"

    # Setor genérico "Câmaras Recursais": número vem do corpo, mesmo com "via deontológica" no texto
    sigla, _ = detect_orgao_julgador_oabsp(
        "Câmaras Recursais",
        "CÂMARAS RECURSAIS – ACÓRDÃO",
        "INCOMPETÊNCIA DA VIA DEONTOLÓGICA. Acordam os membros da Quarta Câmara Recursal, por unanimidade.",
    )
    assert sigla == "OABSP-4CR"

    # Título "17ª Turma do TED Campinas" identifica a Turma Disciplinar
    sigla, _ = detect_orgao_julgador_oabsp(
        "Tribunal de Ética e Disciplina", "ACÓRDÃO Nº 2503 – 17ª Turma do TED Campinas", "EMENTA: REPRESENTAÇÃO."
    )
    assert sigla == "OABSP-17TD"

    # Publicação disciplinar completa não pode virar EMENTA_CONSULTA
    norm = normalize_deoab_sp_item(
        {
            "id": 1163741,
            "titulo": "ACÓRDÃO Nº 11152 – SEGUNDA TURMA DISCIPLINAR DO TED",
            "dataPublicacao": "2026-09-29T00:00:00",
            "setorPublicacao": {"nomeAlternativo": "Tribunal de Ética e Disciplina"},
            "tipoPublicacao": {"descricao": "Acórdão"},
            "conteudoCodificado": (
                "<p>EMENTA: REPRESENTAÇÃO ÉTICO-DISCIPLINAR. OFENSAS DIRIGIDAS A COLEGA. "
                "IMUNIDADE QUE NÃO AFASTA O CONTROLE DEONTOLÓGICO DO ÓRGÃO DE CLASSE. ÉTICA PROFISSIONAL.</p>"
                "<p>Vistos, relatados e discutidos estes autos, acordam os membros da Segunda Turma Disciplinar "
                "do Tribunal de Ética e Disciplina, por unanimidade, em julgar procedente a representação.</p>"
            ),
        }
    )
    assert norm is not None
    assert norm["sigla_orgao"] == "OABSP-2TD"
    assert norm["contexto"] == "EMENTA"
    assert norm["tipo_ementa"] == "PROCESSO_DISCIPLINAR"


# --- Seccional AL (DEOAB nacional): "Intimação de Acórdão" com texto real ------------------------

_AL_INTIM_PREFIXO = (
    "<p>A ORDEM DOS ADVOGADOS DO BRASIL SEÇÃO DE ALAGOAS, com fulcro no artigo 139 do Regulamento Geral "
    "do EAOAB, c/c com art. 69 caput e seu § 2º e art. 73, § 1º da Lei nº 8.906/94, intima para "
    "apresentação de RECURSO do acórdão prolatado pel{orgao_prolatou}, no prazo de quinze dias úteis, "
    "contados a partir da publicação deste, no processo abaixo:</p>"
)


def _al_pub(pub_id, titulo, setor, corpo, tipo="Intimação de Acórdão"):
    return {
        "id": pub_id,
        "titulo": titulo,
        "dataPublicacao": "2025-05-30",
        "edicao": 1,
        "pagina": 1,
        "ano": "VII",
        "tipoPublicacao": {"descricao": tipo},
        "setorPublicacao": {
            "nomeAlternativo": setor,
            "organizacao": {"id": 2, "nomeAlternativo": "Conselho Seccional - Alagoas"},
        },
        "conteudoCodificado": corpo,
    }


def test_al_intimacao_orgao_vem_da_formula_quando_titulo_e_generico():
    from ted_search.collectors.deoab_nacional import normalize_deoab_item_nacional

    corpo = (
        _AL_INTIM_PREFIXO.format(orgao_prolatou="a 2ª Turma do Tribunal de Ética e Disciplina")
        + "<p>Processo nº. 02.0000.2025.000319-7; Repte (s): Jose Marcos Raimundo Da Costa; Repdo (a): A. M. D. L. P. "
        "– OAB/AL 9.597; Juiz Relator: Hugo Rafael Macias Gazzaneo; EMENTA: Representação. Advogada representada "
        "que teria recebido valor previsto em contrato e não prestado serviço. Ausência de provas. Improcedência.</p>"
        "<p>ACORDÃO: Vistos, relatados e discutidos os presentes autos, acordam os membros da 2ª Turma do Tribunal "
        "de Ética e Disciplina do Conselho Seccional da Ordem dos Advogados do Brasil em Alagoas, à UNANIMIDADE de "
        "votos, em conhecer da representação para, no mérito, por idêntica votação, julgá-la IMPROCEDENTE, nos "
        "termos do voto do Relator.</p><p>Maceió/AL, 26 de maio de 2025.</p>"
    )
    norm = normalize_deoab_item_nacional(
        _al_pub(1, "INTIMAÇÃO DE ACÓRDÃO", "TRIBUNAL DE ÉTICA E DISCIPLINA", corpo), "AL", 2
    )
    assert norm["sigla_orgao"] == "OABAL-TED-T2"
    assert norm["contexto"] == "EMENTA"
    # "julgá-la IMPROCEDENTE" aparece só na fórmula decisória
    assert norm["resultado_julgamento"] == "Improcedente / Arquivado"
    assert norm["descricao_acordao"].startswith("Vistos")


def test_al_titulo_manda_sobre_o_corpo_e_pleno_nao_vira_camara_por_origem():
    from ted_search.collectors.deoab_nacional import (
        detect_orgao_julgador_nacional,
        normalize_deoab_item_nacional,
    )

    # Título nomeia a 7ª Turma; a fórmula do corpo (erro de redação da Seccional) cita o Pleno: o cabeçalho manda.
    texto_turma = (
        "INTIMAÇÃO DE ACÓRDÃO A ORDEM DOS ADVOGADOS DO BRASIL SEÇÃO DE ALAGOAS intima para apresentação de RECURSO do "
        "acórdão prolatado pela 7ª Turma do Tribunal de Ética e Disciplina. ACORDÃO: Vistos, relatados e discutidos "
        "os presentes autos, acordam os membros do Pleno do Tribunal de Ética e Disciplina do Conselho Seccional"
    )
    assert detect_orgao_julgador_nacional(
        "AL", "TRIBUNAL DE ÉTICA E DISCIPLINA", "SETIMA TURMA DO TRIBUNAL DE ÉTICA E DISCIPLINA", texto_turma
    )[0] == "OABAL-TED-T7"

    # Acórdão do Conselho Pleno cuja ORIGEM é a Primeira Câmara: menção solta no corpo não define o órgão.
    corpo = (
        "<p><b>Processo n.º</b> 02.0000.2024.004354-2</p><p><b>Tipo:</b> Recurso em Pedido de Inscrição Originária</p>"
        "<p><b>Origem:</b> Primeira Câmara da OAB/AL</p><p><b>Relator:</b> Conselheiro Thiago Mota de Moraes</p>"
        "<p><b>EMENTA: </b>RECURSO EM PEDIDO DE INSCRIÇÃO ORIGINÁRIA. INDEFERIMENTO PELA PRIMEIRA CÂMARA. "
        "EXERCÍCIO DO CARGO DE CONTROLADOR INTERNO MUNICIPAL. INCOMPATIBILIDADE COM O EXERCÍCIO DA ADVOCACIA. "
        "RECURSO CONHECIDO E DESPROVIDO, À UNANIMIDADE.</p><p><b>DECISÃO: </b>O Conselho Pleno da Ordem dos Advogados "
        "do Brasil – Seccional Alagoas, por unanimidade dos votos, conheceu do recurso e negou-lhe provimento, "
        "mantendo a decisão da Primeira Câmara que indeferiu o pedido de inscrição originária.</p>"
    )
    norm = normalize_deoab_item_nacional(_al_pub(2, "ACORDÃO", "Conselho Pleno", corpo, tipo="Acórdão"), "AL", 2)
    assert norm["sigla_orgao"] == "OABAL-PLENO"


def test_al_setor_errado_perde_para_formula_do_corpo():
    from ted_search.collectors.deoab_nacional import normalize_deoab_item_nacional

    # Publicação com setor "1ª CÂMARA", mas intimação, ementa e fórmula são da 3ª Câmara.
    corpo = (
        _AL_INTIM_PREFIXO.format(orgao_prolatou="a 3ª Câmara desta Seccional")
        + "<p>Processo nº. 1770/16 (número antigo 0792/11). Repte: Wladimir Vieira da Silva – OAB/AL 9203, Repdo(a): "
        "F. P. C. C. Relatora: Manuela Gatto Santa Rita de Souza. EMENTA: representação. Alegação de ilações "
        "injuriosas, calúnia e difamação. Improcedência. ACORDÃO: vistos, relatados e discutidos os presentes autos, "
        "acordam os membros da 3ª Câmara do Conselho Seccional da Ordem dos Advogados do Brasil em Alagoas, à "
        "unanimidade, em julgar improcedente a representação.</p><p>Presidente da 3ª Câmara da OAB/AL</p>"
    )
    norm = normalize_deoab_item_nacional(_al_pub(3, "INTIMAÇÃO DE ACÓRDÃO", "1ª CÂMARA", corpo), "AL", 2)
    assert norm["sigla_orgao"] == "OABAL-3CR"


def test_nacional_consulta_so_pelo_nome_do_orgao_e_nao_por_mencao_no_inicio():
    from ted_search.collectors.deoab_nacional import (
        detect_orgao_julgador_nacional,
        normalize_deoab_item_nacional,
    )

    # Menção a "consulta"/"deontologia" no início do corpo não faz do acórdão disciplinar uma consulta.
    corpo = (
        "<p>EMENTA: Deontologia Jurídica. Abandono de Processo. Renúncia aos poderes outorgados sem comunicação ao "
        "cliente. Consulta ao sistema de acompanhamento processual. Representação Ex Officio julgada procedente. "
        "ACÓRDÃO: Vistos, relatados e discutidos os presentes autos, acordam os membros da 5ª Turma do Tribunal de "
        "Ética e Disciplina do Conselho Seccional da Ordem dos Advogados do Brasil em Alagoas, por unanimidade, em "
        "conhecer da representação para, no mérito, dar-lhe provimento, nos termos do voto do Relator.</p>"
    )
    norm = normalize_deoab_item_nacional(_al_pub(4, "QUINTA TURMA DO TRIBUNAL DE ÉTICA E DISCIPLINA", "TRIBUNAL DE ÉTICA E DISCIPLINA", corpo, tipo="Acórdão"), "AL", 2)
    assert norm["sigla_orgao"] == "OABAL-TED-T5"
    assert norm["contexto"] == "EMENTA"
    assert norm["tipo_ementa"] == "PROCESSO_DISCIPLINAR"

    # Sem órgão nomeado no cabeçalho e sem fórmula: a consulta só vale se o cabeçalho disser.
    assert detect_orgao_julgador_nacional("XX", "Turma Deontológica", "ACÓRDÃO", "texto")[0] == "OABXX-TED-CONS"
    assert detect_orgao_julgador_nacional("XX", "TRIBUNAL DE ÉTICA E DISCIPLINA", "ACÓRDÃO", "consulta deontológica sobre honorários")[0] == "OABXX-TED"


def test_al_sem_turma_nomeada_fica_generico_e_comissao_eleitoral_fica_fora_da_busca_do_ted():
    from ted_search.collectors.deoab_nacional import normalize_deoab_item_nacional

    corpo = (
        "<p>EMENTA: “REPRESENTAÇÃO DISCIPLINAR. PRATICAR CRIME INFAMANTE. PENA DE EXCLUSÃO. Aplicabilidade da pena "
        "sujeita ao crivo do Conselho Seccional”.</p><p>Vistos, relatados e discutidos os presentes autos do Processo "
        "Ético Disciplinar nº 7862/2018, ACORDAM os Membros do Conselho da Ordem dos Advogados do Brasil Seccional de "
        "Alagoas, por UNANIMIDADE dos votos, em julgar procedente a representação, aplicando a pena de EXCLUSÃO.</p>"
    )
    norm = normalize_deoab_item_nacional(_al_pub(5, "ACORDÃO Nº 01/2023", "Gabinete da Presidência", corpo, tipo="Acórdão"), "AL", 2)
    assert norm["sigla_orgao"] == "OABAL-TED"
    assert norm["sancao_identificada"] == "Exclusão"

    eleitoral = (
        "<p>REQUERENTE: CANDIDATO A PRESIDENTE: VAGNER PAES CAVALCANTI FILHO - OAB/AL 7163 E OUTROS ASSUNTO : "
        "REGISTRO DE CANDIDATURA DA CHAPA 01 (Seccional Maceió/AL)</p><p>EMENTA REQUERIMENTO DE REGISTRO DE "
        "CANDIDATURA. ELEIÇÕES OAB/AL 2024. PROCESSO ADMINISTRATIVO. DEFERIMENTO DO REGISTRO DA CHAPA 01.</p>"
    )
    # Política desde a auditoria do AP (18/11/2026): decisão eleitoral fica no banco, fora da busca do TED
    rec_eleitoral = normalize_deoab_item_nacional(
        _al_pub(6, "PROCESSO Nº 02.0000.2024.005055-7", "COMISSÃO ELEITORAL", eleitoral, tipo="Acórdão"), "AL", 2
    )
    assert rec_eleitoral is not None and rec_eleitoral["is_ted_disciplinar"] == 0


def test_acordao_em_linha_unica_nao_e_apagado_como_ruido():
    from ted_search.ocr_cleaner import is_gibberish_ocr_line

    linha = "EMENTA: Representação. Advogado inscrito na OAB/MT sob n. 1/O c/c art. 34 do EAOAB. " * 6
    assert not is_gibberish_ocr_line(linha)
    assert is_gibberish_ocr_line("/a b/c /d x/y")


def test_marca_disciplinar_segundas_leituras_go_ma_pe():
    from ted_search.collectors.deoab_nacional import eh_materia_disciplinar as d

    # MA: pedido de inscrição com "representação criminal" na ementa é matéria de inscrição
    assert not d("Conselho Seccional", "", "REQUERENTE: J.R.F. EMENTA: PEDIDO DE INSCRIÇÃO ORIGINÁRIA. EXISTÊNCIA DE REPRESENTAÇÃO CRIMINAL CONTRA O REQUERENTE. INSCRIÇÃO DEFERIDA.")
    # ES: consulta ao TED sobre inscrição continua disciplinar
    assert d("TED", "", "Consulta nº 1 Consulente: Fulana. EMENTA N.º 197/TURMA JULGADORA/2024 PEDIDO DE INSCRIÇÃO DE ADVOGADO SERVIDOR DA DEFENSORIA. CÓDIGO DE ÉTICA.")
    # GO: o TED anuncia o julgamento; "sociedade de advogados" é só o nome da parte
    assert d("", "", "O Tribunal de Ética e Disciplina da OAB-GO torna público que foi julgado o seguinte processo: 201508915. Repte: X Sociedade de Advogados. Repdo: B. EMENTA: Condenação por ausência de prestação de contas.")
    # GO e RJ: alteração de regimento e solicitação de providências são administrativas
    assert not d("Conselho Pleno", "", "Requerente: Presidente do TED. Ementa: PROPOSTA DE ALTERAÇÃO DO REGIMENTO INTERNO DO TRIBUNAL DE ÉTICA E DISCIPLINA.")
    # PE: prescrição do art. 43 em recurso sem a palavra "disciplinar"
    assert d("Segunda Câmara", "", "Recorrente: R.B.F. Recorrido: M.C. decidiu a Câmara, por unanimidade, reconhecer a prescrição da pretensão punitiva, nos termos do art. 43 do Estatuto.")
