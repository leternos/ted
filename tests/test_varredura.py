"""Testes offline do livro-razão e da carga da varredura das Seccionais."""

import json

from ted_search import varredura
from ted_search.db import get_connection, init_db

TEXTO = (
    "<p>EMENTA: REPRESENTAÇÃO DISCIPLINAR. RETENÇÃO DE VALORES DO CLIENTE. INFRAÇÃO AO ART. 34, XXI, DO EAOAB. "
    "PROCEDÊNCIA. SUSPENSÃO POR TRINTA DIAS.</p><p>Vistos, relatados e discutidos estes autos, acordam os membros "
    "da Primeira Turma do Tribunal de Ética e Disciplina da OAB/AC, por unanimidade, em julgar procedente a representação.</p>"
)


def _pub(pid: int, data: str, conteudo: str = TEXTO) -> dict:
    return {
        "id": pid,
        "titulo": f"ACÓRDÃO Nº {pid}",
        "dataPublicacao": f"{data}T00:00:00",
        "tipoPublicacao": {"descricao": "Acórdão"},
        "setorPublicacao": {"nomeAlternativo": "Tribunal de Ética e Disciplina"},
        "conteudoCodificado": conteudo,
    }


def _escrever_bruto(raw_dir, ano: int, pubs: list[dict], skips: list[int], fechou: bool = True) -> None:
    out = varredura.raw_dir_uf("AC", raw_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids = [p["id"] for p in pubs] + skips
    index = {f"{ano}-03": {"modo": "mes", "esperado": {"ACORDAO": len(ids)}, "indexados": {"ACORDAO": len(ids)}, "ids": ids, "fechou": fechou}}
    (out / f"{ano}.index.json").write_text(json.dumps(index), encoding="utf-8")
    linhas = [json.dumps(p, ensure_ascii=False) for p in pubs] + [json.dumps({"id": i, "_skip": "Edital"}) for i in skips]
    (out / f"{ano}.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def test_carga_fecha_o_livro_razao(tmp_path):
    db, raw = tmp_path / "t.db", tmp_path / "raw"
    init_db(db)
    # 2 decisões válidas, 1 decisão vazia (o normalizador recusa) e 1 edital
    _escrever_bruto(raw, 2024, [_pub(101, "2024-03-05"), _pub(102, "2024-03-06"), _pub(103, "2024-03-07", "<p>x</p>")], skips=[104])

    rel = varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    assert rel["inseridos"] == 2 and rel["rejeitados"] == 1 and rel["anos_preservados"] == []

    with get_connection(db) as conn:
        linha = dict(conn.execute("SELECT * FROM cobertura_coleta WHERE uf = 'AC' AND ano = 2024").fetchone())
        assert conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = 'DEOAB_AC'").fetchone()[0] == 2
    assert (linha["candidatos"], linha["inspecionados"], linha["decisoes"], linha["rejeitados"], linha["no_banco"]) == (4, 4, 3, 1, 2)
    assert linha["status"] == "COMPLETO"
    rejeitados = (varredura.raw_dir_uf("AC", raw) / "rejeitados.jsonl").read_text(encoding="utf-8")
    assert '"id": 103' in rejeitados


def test_indice_que_nao_fecha_deixa_o_ano_parcial(tmp_path):
    db, raw = tmp_path / "t.db", tmp_path / "raw"
    init_db(db)
    _escrever_bruto(raw, 2024, [_pub(201, "2024-03-05")], skips=[], fechou=False)
    varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    with get_connection(db) as conn:
        assert conn.execute("SELECT status FROM cobertura_coleta WHERE uf = 'AC'").fetchone()[0] == "PARCIAL"


def test_coleta_menor_que_o_banco_nao_apaga_nada(tmp_path):
    db, raw = tmp_path / "t.db", tmp_path / "raw"
    init_db(db)
    _escrever_bruto(raw, 2024, [_pub(301, "2024-03-05"), _pub(302, "2024-03-06")], skips=[])
    varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    # Nova coleta, incompleta, que nem chegou a inspecionar a publicação 302: o ano é preservado
    _escrever_bruto(raw, 2024, [_pub(301, "2024-03-05")], skips=[])
    rel = varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    assert rel["anos_preservados"] == [{"ano": 2024, "havia": 2, "coletados": 1, "ids_nao_inspecionados": [302]}]
    with get_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = 'DEOAB_AC'").fetchone()[0] == 2


def test_resumo_aceita_o_formato_antigo_da_coleta_de_sp(tmp_path):
    out = varredura.raw_dir_uf("SP", tmp_path)
    out.mkdir(parents=True)
    (out / "2023.index.json").write_text(json.dumps({"2023-02-01": [1, 2], "2023-02-02": [3]}), encoding="utf-8")
    (out / "2023.summary.json").write_text(json.dumps({"esperado_por_termo": {"ACORDAO": 3}, "paginas_com_falha": [], "publicacoes_com_falha": []}), encoding="utf-8")
    linhas = [json.dumps({"id": i, "tipoPublicacao": {"descricao": "Acórdão"}}) for i in (1, 2)] + ['{"id": 3, "_skip": "Edital"}']
    (out / "2023.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    r = varredura.resumir_ano("SP", 2023, tmp_path)
    assert (r["candidatos"], r["inspecionados"], r["decisoes"], r["esperado_por_termo"]) == (3, 3, 2, {"ACORDAO": 3})
    assert r["janelas_parciais"] == []


def test_base_de_site_carrega_e_fecha_o_livro(tmp_path, monkeypatch):
    """Adaptador fictício: 3 itens no site, 1 sem texto (rejeitado). O livro só fecha se a conta bater."""
    import sys
    import types

    from ted_search import fontes

    mod = types.ModuleType("ted_search.collectors.sites.to_consultas")

    async def esperado(client, sem):
        return 3

    async def listar(client, sem):
        return [{"id": i} for i in (1, 2, 3)]

    async def baixar(client, sem, item):
        return {"texto": "" if item["id"] == 3 else f"CONSULTA {item['id']}. PUBLICIDADE NA ADVOCACIA. LIMITES ÉTICOS. Resposta em tese.", "data": "2015-06-01"}

    def normalizar(item):
        if not item["texto"]:
            return None
        return fontes.montar_registro(
            "TO", "consultas", item["id"], ementa=item["texto"], url=f"https://exemplo.invalid/{item['id']}", orgao_raw="TED/TO",
            sigla_orgao="OABTO-TED-CONS", orgao_norm="OAB/TO - TED (Consultas)", data_decisao=item["data"], consulta=True,
        )

    mod.esperado, mod.listar, mod.baixar, mod.normalizar = esperado, listar, baixar, normalizar
    monkeypatch.setitem(sys.modules, "ted_search.collectors.sites.to_consultas", mod)

    import asyncio

    db, raw = tmp_path / "t.db", tmp_path / "raw"
    init_db(db)
    resumo = asyncio.run(fontes.coletar_fonte("TO", "consultas", raw_dir=raw))
    assert (resumo["esperado"], resumo["listados"], resumo["baixados"], resumo["falhas"]) == (3, 3, 3, [])
    rel = fontes.carregar_fonte("TO", "consultas", db_path=db, raw_dir=raw)
    assert (rel["no_banco"], rel["rejeitados"], rel["status"]) == (2, 1, "COMPLETO")
    with get_connection(db) as conn:
        row = conn.execute("SELECT contexto, fonte, ano_ementa FROM julgados WHERE fonte = 'SITE_TO_CONSULTAS' LIMIT 1").fetchone()
        assert tuple(row) == ("EMENTA_CONSULTA", "SITE_TO_CONSULTAS", 2015)
    # Reexecutar não baixa de novo nem duplica
    resumo2 = asyncio.run(fontes.coletar_fonte("TO", "consultas", raw_dir=raw))
    assert resumo2["baixados"] == 3


def test_recarga_remove_o_que_o_normalizador_passou_a_recusar(tmp_path):
    """Mesma coleta, normalizador mais rigoroso: o registro recusado sai do banco e vai para os rejeitados."""
    db, raw = tmp_path / "t.db", tmp_path / "raw"
    init_db(db)
    _escrever_bruto(raw, 2024, [_pub(401, "2024-03-05"), _pub(402, "2024-03-06")], skips=[])
    varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    # A segunda publicação passa a ser recusada (texto vazio), sem que a coleta tenha encolhido
    _escrever_bruto(raw, 2024, [_pub(401, "2024-03-05"), _pub(402, "2024-03-06", "<p>x</p>")], skips=[])
    rel = varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    assert rel["anos_preservados"] == [] and rel["rejeitados"] == 1
    with get_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = 'DEOAB_AC'").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM cobertura_coleta WHERE uf = 'AC'").fetchone()[0] == "COMPLETO"


def test_decisao_e_reconhecida_pelo_titulo_ou_pelo_corpo_mesmo_com_tipo_errado():
    """AL publica "INTIMAÇÃO DE ACÓRDÃO" com ementa completa sob os tipos Retificação e Notificação."""
    perfil = {"tipos_decisao": ["ACÓRDÃO", "EMENTA"]}
    por_titulo = {"titulo": "INTIMAÇÃO DE ACÓRDÃO", "tipoPublicacao": {"descricao": "Retificação"}, "conteudoCodificado": "<p>Processo 964/2014.</p>"}
    por_corpo = {"titulo": "PUBLICAÇÃO", "tipoPublicacao": {"descricao": "Notificação"},
                 "conteudoCodificado": "<p>EMENTA: REPRESENTAÇÃO. PROCEDÊNCIA.</p><p>Acordam os membros da Sexta Turma do TED.</p>"}
    portaria = {"titulo": "PORTARIA Nº 12", "tipoPublicacao": {"descricao": "Portaria"}, "conteudoCodificado": "<p>Nomeia comissão.</p>"}
    antigo = {"id": 9, "_skip": "Edital"}
    assert varredura.eh_decisao(por_titulo, perfil) and varredura.eh_decisao(por_corpo, perfil)
    assert not varredura.eh_decisao(portaria, perfil) and not varredura.eh_decisao(antigo, perfil)


def test_modo_censo_troca_os_termos_por_enumeracao_total():
    assert varredura.termos_de_coleta({"termos": ["ACORDAO"], "modo": "censo"}) == [varredura.TERMO_CENSO]
    assert varredura.termos_de_coleta({"termos": ["ACORDAO", "EMENTA"]}) == ["ACORDAO", "EMENTA"]


def test_normalizador_confia_no_perfil_e_marca_o_que_nao_e_disciplinar():
    from ted_search.collectors.deoab_nacional import motivo_recusa, normalize_deoab_item_nacional

    # TO: resultado de julgamento publicado como "Edital de Notificação" — a varredura já o reconheceu como decisão
    resultado = {
        "id": 704064, "titulo": "EDITAL RESULTADO DE JULGAMENTO.", "dataPublicacao": "2024-02-21T00:00:00",
        "tipoPublicacao": {"descricao": "Edital de Notificação"}, "setorPublicacao": {"nomeAlternativo": "Tribunal de Ética e Disciplina"},
        "conteudoCodificado": "<p>Processo disciplinar 123/2021. Relator: Fulano. Acórdão: Vistos, relatados e discutidos os autos, "
                              "os membros da Turma Julgadora votaram pela procedência da representação, com pena de censura.</p>",
    }
    assert normalize_deoab_item_nacional(resultado, default_uf="TO", default_org_id=27) is None  # filtro antigo, pelo nome do tipo
    assert normalize_deoab_item_nacional(resultado, default_uf="TO", default_org_id=27, confiar_no_tipo=True) is not None

    # AP: acórdão do Conselho sobre registro de sociedade de advogados não é matéria ético-disciplinar
    sociedade = {
        "id": 36166, "titulo": "SOCIEDADE DE ADVOGADOS", "dataPublicacao": "2019-03-01T00:00:00",
        "tipoPublicacao": {"descricao": "Acórdão"}, "setorPublicacao": {"nomeAlternativo": "Secretaria Geral"},
        "conteudoCodificado": "<p>EMENTA: Pedido de registro de sociedade individual de advocacia. Requisitos atendidos. Deferimento. "
                              "Acordam os membros da Primeira Câmara, por unanimidade, em deferir o registro.</p>",
    }
    assert motivo_recusa(sociedade, confiar_no_tipo=True) is None
    rec = normalize_deoab_item_nacional(sociedade, default_uf="AP", default_org_id=3, confiar_no_tipo=True)
    assert rec["is_ted_disciplinar"] == 0  # fica no banco, fora da busca do TED
    assert normalize_deoab_item_nacional(resultado, default_uf="TO", default_org_id=27, confiar_no_tipo=True)["is_ted_disciplinar"] == 1


def test_resultado_de_sessao_e_formula_vistos_e_analisados_sao_decisao():
    """Auditoria de TO: 15 resultados de julgamento ficaram fora por variação de título e de fórmula."""
    perfil = {"tipos_decisao": ["ACÓRDÃO", "EMENTA"]}
    tipo = {"descricao": "Edital de Notificação"}
    analisados = {"titulo": "PROCESSO Nº 27.0000.2019.001795-0", "tipoPublicacao": tipo,
                  "conteudoCodificado": "<p>Acórdão: Vistos e analisados os autos, o TED, por unanimidade, decide pelo reconhecimento da prescrição.</p>"}
    resultado = {"titulo": "PROCESSO Nº 1676/2015", "tipoPublicacao": tipo,
                 "conteudoCodificado": "<p>EDITAL - RESULTADO DE JULGAMENTO DA SESSÃO DO DIA 10.03.2023. Arquivamento aprovado por unanimidade.</p>"}
    pauta = {"titulo": "EDITAL Nº 3/2022 - 2ª Sessão", "tipoPublicacao": tipo,
             "conteudoCodificado": "<p>Ficam os interessados notificados da pauta da sessão ordinária do Tribunal de Ética e Disciplina.</p>"}
    assert varredura.via_decisao(analisados, perfil) == "corpo" and varredura.via_decisao(resultado, perfil) == "corpo"
    assert varredura.via_decisao(pauta, perfil) is None


def test_republicacao_identica_sai_mas_serie_de_processos_fica():
    from ted_search.varredura import _tirar_republicacoes

    base = {"inteiro_teor_texto": "EMENTA: x. Acordam os membros.", "data_decisao": "2024-01-10"}
    recs = [
        dict(base, id="DEOAB_PB:1", numero_processo="15.0000.2024.000001-1"),
        dict(base, id="DEOAB_PB:2", numero_processo="15.0000.2024.000001-1", data_decisao="2024-02-01"),  # republicação
        dict(base, id="DEOAB_PB:3", numero_processo="15.0000.2024.000002-2"),  # outro processo, mesmo corpo
        dict(base, id="DEOAB_PB:4", numero_processo=None),  # sem número: nunca funde
        dict(base, id="DEOAB_PB:5", numero_processo=None),
    ]
    ficam, rej = _tirar_republicacoes(recs, [])
    assert sorted(r["id"] for r in ficam) == ["DEOAB_PB:1", "DEOAB_PB:3", "DEOAB_PB:4", "DEOAB_PB:5"]
    assert rej == [{"id": 2, "motivo": "republicacao_identica", "duplicata_de": "DEOAB_PB:1",
                    "titulo": "15.0000.2024.000001-1", "chars": 30, "data": "2024-02-01"}]


def test_embargos_herdam_a_marca_disciplinar_do_processo():
    from ted_search.varredura import _alinhar_marca_por_processo

    recs = [
        {"id": "a", "numero_processo": "15.0000.2020.000100-1", "is_ted_disciplinar": 1, "inteiro_teor_texto": "Representação. Censura."},
        {"id": "b", "numero_processo": "15.0000.2020.000100-1", "is_ted_disciplinar": 0, "inteiro_teor_texto": "Embargos de declaração rejeitados."},
        {"id": "c", "numero_processo": "15.0000.2020.000100-1", "is_ted_disciplinar": 0, "inteiro_teor_texto": "Pedido de inscrição suplementar. Deferido."},
    ]
    _alinhar_marca_por_processo(recs)
    assert [r["is_ted_disciplinar"] for r in recs] == [1, 1, 0]


def test_decisao_anunciada_por_camara_vale_seja_qual_for_o_tipo():
    from ted_search.varredura import RE_DECISAO_DE_ORGAO, _sem_acento

    assert RE_DECISAO_DE_ORGAO.search(_sem_acento("A OAB/PB torna pública a decisão da Primeira Câmara no Processo nº 15.0000.2024.000599-1."))
    assert RE_DECISAO_DE_ORGAO.search(_sem_acento("torna pública, para os devidos fins, a decisão proferida pela 5ª Turma Julgadora deste Tribunal"))
    assert not RE_DECISAO_DE_ORGAO.search(_sem_acento("torna pública a suspensão do advogado, por decisão transitada em julgado"))
