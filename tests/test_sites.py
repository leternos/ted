"""Adaptadores das bases de sites das Seccionais (collectors/sites/). Sem rede: só trechos reais em tests/fixtures/."""

import asyncio
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(nome: str) -> dict:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------------------------------
# SP / ementario - Turma de Ética Profissional (TED-I) no site novo da OAB/SP
# ----------------------------------------------------------------------------------------------------------------------

class _RespostaFalsa:
    def __init__(self, corpo: dict, pagina: str = ""):
        self._corpo = corpo
        self.content = pagina.encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._corpo


class _ClienteFalso:
    """Devolve sempre a mesma resposta e guarda o que foi pedido."""

    def __init__(self, corpo: dict, pagina: str = ""):
        self._corpo = corpo
        self._pagina = pagina
        self.pedidos: list[tuple[str, dict]] = []

    async def post(self, url, data=None, headers=None, **_k):
        self.pedidos.append((url, dict(data or {})))
        return _RespostaFalsa(self._corpo)

    async def get(self, url, params=None, headers=None, **_k):
        self.pedidos.append((url, dict(params or {})))
        return _RespostaFalsa({}, self._pagina)


def test_sp_ementario_listagem_le_itens_e_total_do_ano():
    from ted_search.collectors.sites import sp_ementario as m

    pagina = _fixture("sp_ementario_listagem_2025_start216.json")
    assert m.total_declarado(pagina) == 220
    itens = m.itens_da_pagina(pagina, 2025)
    # cada item aparece duas vezes no HTML (número e descrição): tem de sair uma vez só, com o data-guid como id
    assert [it["id"] for it in itens] == ["1744290515", "1744290293", "1744290175", "1744289947"]
    assert itens[0]["slug"] == "25-04-10-100835-"
    assert itens[0]["titulo"] == "5450-7/2025"
    assert itens[0]["descricao"].startswith("INCOMPATIBILIDADES E IMPEDIMENTOS")
    assert itens[0]["ano_etiqueta"] == 2025
    assert pagina["btnMore"] == 0  # última página do ano


def test_sp_ementario_detalhe_antigo_com_relatorio_e_parecer():
    from ted_search.collectors.sites import sp_ementario as m

    cliente = _ClienteFalso(_fixture("sp_ementario_detalhe_e-2465-2001.json"))
    item = {"id": "1040727600", "slug": "02-12-24-0900-e-2465200", "titulo": "E-2.465/2001", "descricao": "", "ano_etiqueta": 2001}
    det = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), item))
    assert cliente.pedidos == [(m.URL_DETALHE, {"bracket": "5", "slug": "02-12-24-0900-e-2465200"})]
    assert det["numero"] == "E-2.465/2001"
    assert det["relator"] == "OSMAR DE PAULA CONCEIÇÃO JÚNIOR"
    assert det["revisor"] == "RICARDO GARRIDO JÚNIOR"
    assert det["data_julgamento"] == "2002-02-21"  # 'v.u. em 21/02/02': o ano-etiqueta é 2001, o julgamento foi em 2002
    assert det["ementa"].startswith('HONORÁRIOS - CONTRATO VERBAL - PACTO "QUOTA LITIS"')
    assert det["ementa"].endswith("Presidente Dr. ROBISON BARONI.")
    assert det["relatorio"].startswith("RELATÓRIO - Indaga o ilustre advogado")
    assert det["parecer"].startswith("PARECER - Trata-se de caso concreto")
    json.dumps(det)  # serializável

    rec = m.normalizar(det)
    assert rec["id"] == "SITE_SP_EMENTARIO:1040727600"
    assert (rec["fonte"], rec["contexto"], rec["tipo_ementa"]) == ("SITE_SP_EMENTARIO", "EMENTA_CONSULTA", "CONSULTA")
    assert (rec["sigla_orgao"], rec["orgao_julgador_norm"]) == ("OABSP-TDEONT", "OAB/SP - Turma Deontológica (TED-I)")
    assert rec["orgao_julgador_raw"] == "Turma de Ética Profissional"
    assert (rec["numero_processo"], rec["numero_ementa"]) == ("E-2.465/2001", "E-2.465/2001")
    assert (rec["data_decisao"], rec["ano_ementa"]) == ("2002-02-21", 2002)
    assert rec["relator_principal"] == "OSMAR DE PAULA CONCEIÇÃO JÚNIOR"
    assert rec["tem_inteiro_teor"] == 1
    assert "PARECER - Trata-se de caso concreto" in rec["inteiro_teor_texto"]
    assert rec["descricao_ementa"] == det["ementa"]
    assert rec["diario_url"] == "https://www.oabsp.org.br/tribunal-de-etica-e-disciplina/ementario#modal=02-12-24-0900-e-2465200%5b%21%5d5"
    assert rec["sancao_identificada"] is None and rec["resultado_julgamento"] is None


def test_sp_ementario_detalhe_novo_usa_numero_unificado():
    from ted_search.collectors.sites import sp_ementario as m

    cliente = _ClienteFalso(_fixture("sp_ementario_detalhe_4516-6-2024.json"))
    item = {"id": "1737137847", "slug": "25-01-17-151727-", "titulo": "4516-6/2024", "descricao": "", "ano_etiqueta": 2024}
    det = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), item))
    assert (det["numero"], det["numero_processo"]) == ("4516-6/2024", "25.0886.2024.024516-6")
    assert (det["relator"], det["revisor"]) == ("CLÁUDIO FELIPPE ZALAF", "FÁBIO DE SOUZA RAMACCIOTTI")
    assert det["data_julgamento"] == "2024-12-05"
    assert det["parecer"].startswith("Parecer")
    rec = m.normalizar(det)
    assert (rec["numero_processo"], rec["numero_ementa"], rec["data_decisao"]) == ("25.0886.2024.024516-6", "4516-6/2024", "2024-12-05")
    assert rec["tem_inteiro_teor"] == 1


def test_sp_ementario_so_ementa_sem_data_usa_a_sessao_da_coletanea():
    """1994-1996: o detalhe é só a ementa e a linha de votação não traz data. A data vem da sessão na coletânea legada."""
    from ted_search.collectors.sites import sp_ementario as m

    m._coletanea.clear()
    pagina = (FIXTURES / "sp_ementario_coletanea_1994_trecho.html").read_text(encoding="utf-8")
    assert m.datas_da_coletanea(m.html_para_texto(pagina)) == {"1138": ["1994-12-15"], "1181": ["1994-12-15"], "1201": ["1994-12-15"]}
    cliente = _ClienteFalso(_fixture("sp_ementario_detalhe_e-1201-1994.json"), pagina)
    item = {"id": "788922000", "slug": "94-12-31-2300-e--120", "titulo": "E - 1.201", "descricao": "PATROCÍNIO SIMULTÂNEO - INFRAÇÃO ÉTICA", "ano_etiqueta": 1994}
    try:
        det = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), item))
    finally:
        m._coletanea.clear()
    assert cliente.pedidos[-1] == (m.URL_COLETANEA, {"ano": "1994"})
    assert det["numero"] == "E-1.201"
    assert det["data_julgamento"] is None
    assert (det["data_sessao_coletanea"], det["data_origem"]) == ("1994-12-15", "coletanea")
    assert (det["relatorio"], det["parecer"]) == ("", "")
    assert det["relator"] == "JOSÉ EDUARDO DIAS COLLAÇO"
    rec = m.normalizar(det)
    assert rec["data_decisao"] == "1994-12-15"
    assert rec["tem_inteiro_teor"] == 0
    assert rec["descricao_ementa"].startswith("PATROCÍNIO SIMULTÂNEO - INFRAÇÃO ÉTICA O advogado não pode")
    # sem data no texto nem na coletânea, sobra o ano-etiqueta do site
    assert m.normalizar({**det, "data_sessao_coletanea": None})["data_decisao"] == "1994-01-01"
    assert m.normalizar({**det, "texto": ""}) is None  # item sem texto é rejeitado, não inventado


def test_sp_ementario_numero_com_erro_de_digitacao_ou_titulo_que_nao_e_numero():
    from ted_search.collectors.sites import sp_ementario as m

    assert m.normalizar_numero("E - 1.201") == "E-1.201"
    assert m.normalizar_numero("E-l.O61") == "E-1.061"  # o site traz 'l' por '1' e 'O' por '0' em itens de 1994
    assert m.normalizar_numero("3917-2/2025") == "3917-2/2025"
    assert (m.chave_numero("E-2.465/2001"), m.chave_numero("3917-2/2025")) == ("2465", None)
    partes = m.estruturar("São Paulo, 20 de\nProc. E-1.614/97\nA Primeira Turma - Seção Deontológica do Tribunal de Ética", "São Paulo, 20 de")
    assert partes["numero"] == "E-1.614/97"


def test_sp_ementario_resposta_de_erro_do_site_nao_vira_item():
    """Sob carga o site responde 200 com uma mensagem de erro no lugar do detalhe: é falha, não registro."""
    from ted_search.collectors.sites import sp_ementario as m

    m_pausa, m.PAUSA = m.PAUSA, 0
    dormir, m.asyncio.sleep = m.asyncio.sleep, (lambda *_a, **_k: dormir(0))
    try:
        cliente = _ClienteFalso({"title": "", "html": "<p>Ocorreu um erro, feche a janela e tente novamente.</p>", "post": {}})
        item = {"id": "946677600", "slug": "x", "titulo": "E-2.018/99", "descricao": "", "ano_etiqueta": 1999}
        assert asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), item)) is None
        assert len(cliente.pedidos) > 3  # insistiu antes de desistir
        assert cliente.pedidos[-1][1].get("guid") == "946677600"  # e por último tentou pelo guid
    finally:
        m.PAUSA, m.asyncio.sleep = m_pausa, dormir
    assert m.normalizar({"id": "1", "titulo": "E-2.018/99", "ano_etiqueta": 1999, "texto": "Ocorreu um erro, feche a janela e tente novamente."}) is None


def test_sp_ementario_slug_que_abre_outro_item_e_refeito_pelo_guid():
    """O slug não é único no site: para alguns itens o detalhe pelo slug é de OUTRA consulta. Só vale o guid pedido."""
    from ted_search.collectors.sites import sp_ementario as m

    outro, certo = _fixture("sp_ementario_detalhe_e-2465-2001.json"), _fixture("sp_ementario_detalhe_e-1201-1994.json")
    pagina = (FIXTURES / "sp_ementario_coletanea_1994_trecho.html").read_text(encoding="utf-8")

    class _Cliente(_ClienteFalso):
        async def post(self, url, data=None, headers=None, **_k):
            self.pedidos.append((url, dict(data or {})))
            return _RespostaFalsa(certo if (data or {}).get("guid") else outro)

    cliente = _Cliente({}, pagina)
    item = {"id": "788922000", "slug": "94-12-31-2300-e--120", "titulo": "E - 1.201", "descricao": "", "ano_etiqueta": 1994}
    m._coletanea.clear()
    try:
        det = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), item))
    finally:
        m._coletanea.clear()
    assert cliente.pedidos[0] == (m.URL_DETALHE, {"bracket": "5", "slug": "94-12-31-2300-e--120"})
    assert cliente.pedidos[1] == (m.URL_DETALHE, {"bracket": "5", "slug": "94-12-31-2300-e--120", "guid": "788922000"})
    assert det["numero"] == "E-1.201" and "PATROCÍNIO SIMULTÂNEO" in det["ementa"]
    assert det["link_direto"] is False
    assert m.normalizar(det)["diario_url"] == m.URL_PAGINA  # a âncora abriria outro item: fica a página do ementário


# ----------------------------------------------------------------------------------------------------------------------
# SP / legado-disciplinar - acórdãos das Turmas Disciplinares do TED no sistema legado (2010-2014)
# ----------------------------------------------------------------------------------------------------------------------

def _html(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


class _ClienteHtml:
    """GET devolve o HTML dado (ou um por id); POST devolve a listagem dada."""

    def __init__(self, paginas: dict[str, str] | str, lista: str = ""):
        self._paginas, self._lista = paginas, lista
        self.pedidos: list[tuple[str, dict]] = []

    async def get(self, url, params=None, headers=None, **_k):
        self.pedidos.append((url, dict(params or {})))
        h = self._paginas if isinstance(self._paginas, str) else self._paginas[str((params or {})["idEmenta"])]
        return type("R", (), {"content": h.encode("utf-8"), "raise_for_status": lambda self: None})()

    async def post(self, url, data=None, headers=None, **_k):
        self.pedidos.append((url, dict(data or {})))
        return type("R", (), {"content": self._lista.encode("utf-8"), "raise_for_status": lambda self: None})()


def test_sp_legado_disciplinar_detalhe_com_conteudo():
    from ted_search.collectors.sites import sp_legado_disciplinar as m

    cliente = _ClienteHtml(_html("sp_legado_disciplinar_id5024.html"))
    item = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), {"id": 5024, "data_listagem": "2014-09-17"}))
    assert item["vazio"] is False and item["numero_acordao"] == "996"
    assert item["numero_processo"] == "15R0006612011" and item["turma"] == "Décima Quinta Turma Disciplinar"
    assert item["data_texto"] == "2014-09-16" and item["relator"] == "DINO BOLDRINI NETO"
    assert cliente.pedidos == [(m.URL_DETALHE, {"idEmenta": "5024"})]

    rec = m.normalizar(item)
    assert rec["id"] == "SITE_SP_LEGADO_DISCIPLINAR:5024" and rec["fonte"] == "SITE_SP_LEGADO_DISCIPLINAR"
    assert rec["contexto"] == "EMENTA" and rec["tipo_ementa"] == "PROCESSO_DISCIPLINAR"
    assert rec["data_decisao"] == "2014-09-16" and rec["ano_ementa"] == 2014  # a data do texto vence a da listagem
    assert rec["sigla_orgao"] == "OABSP-15TD" and rec["orgao_julgador_norm"] == "OAB/SP - 15ª Turma Disciplinar (TED)"
    assert rec["orgao_julgador_raw"] == "Décima Quinta Turma Disciplinar"
    assert rec["numero_processo"] == "15R0006612011" and rec["numero_ementa"] == "996"
    assert rec["relator_principal"] == "DINO BOLDRINI NETO"
    assert rec["descricao_ementa"].startswith("ADVOGADA PRESA") and rec["descricao_acordao"].startswith("Vistos, relatados")
    assert rec["diario_url"].endswith("textoEmentas.asp?idEmenta=5024")


def test_sp_legado_disciplinar_turma_com_do_ted_e_data_da_listagem_quando_falta_no_texto():
    from ted_search.collectors.sites import sp_legado_disciplinar as m

    item = m.extrair(_html("sp_legado_disciplinar_id1.html"))
    assert item["turma"] == "Segunda Turma Disciplinar do TED" and item["data_texto"] == "2012-03-28"
    item.update(id=1, vazio=False, url="u", data_texto=None, data_listagem="2012-03-30")
    rec = m.normalizar(item)
    assert rec["sigla_orgao"] == "OABSP-2TD" and rec["data_decisao"] == "2012-03-30"
    item.update(turma=None, turma_n=None)
    rec = m.normalizar(item)
    assert rec["sigla_orgao"] == "OABSP-TED" and rec["orgao_julgador_norm"] == "OAB/SP - Tribunal de Ética e Disciplina"


def test_sp_legado_disciplinar_id_vazio_e_gravado_como_vazio_e_rejeitado_na_carga():
    from ted_search.collectors.sites import sp_legado_disciplinar as m

    cliente = _ClienteHtml(_html("sp_legado_disciplinar_id_vazio.html"))
    item = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), {"id": 5400}))
    assert item and item["vazio"] is True and "ementa" not in item
    assert m.normalizar(item) is None
    assert asyncio.run(m.esperado(cliente, asyncio.Semaphore(1))) is None


def test_sp_legado_disciplinar_resposta_fora_do_padrao_nao_vira_vazio():
    from ted_search.collectors.sites import sp_legado_disciplinar as m

    assert m.pagina_valida(_html("sp_legado_disciplinar_id_vazio.html"))
    assert not m.pagina_valida("<html><body>Server Error 500</body></html>")
    assert not m.pagina_valida("<html><body><b>Nota da Corregedoria do TED:</b> cortada")  # sem </html>


def test_sp_legado_disciplinar_listagem_mensal_traz_data_por_id():
    from ted_search.collectors.sites import sp_legado_disciplinar as m

    pares = m.RE_ITEM_LISTA.findall(_html("sp_legado_disciplinar_listagem_2012_3_trecho.html"))
    assert pares[0] == ("31/03/2012", "712") and pares[1] == ("30/03/2012", "725") and len(pares) == 3


def test_sp_legado_disciplinar_variantes_de_turma_relator_data_e_processo():
    from ted_search.collectors.sites import sp_legado_disciplinar as m

    def campos(abertura: str, fecho: str) -> dict:
        return m.campos_do_acordao(f"Vistos, relatados e discutidos estes autos {abertura}\n{fecho}")

    c = campos("do Processo Disciplinar 09r0002452011 (066/07), acordam os membros da NONA TURMA do TED, julgar.", "Sala das sessões, 30 de março de 2012. Rel. Dr. Taddeo Gallo Junior - Presidente de sala Dr. Fulano de Tal.")
    assert (c["turma_n"], c["numero_processo"], c["data_texto"], c["relator"]) == (9, "09R0002452011", "2012-03-30", "TADDEO GALLO JUNIOR")
    c = campos("do processo disciplinar no 496/12, na 39a Sessão de Julgamento, acordam os membros da 3a Seção da Décima Sétima Turma Disciplinar, por", "Sala das sessões, 21 de fevereiro 2014. Rel. e Presidente de sala: Dr. Francisco Luiz Maccire.")
    assert (c["turma_n"], c["numero_processo"], c["data_texto"], c["relator"]) == (17, "496/12", "2014-02-21", "FRANCISCO LUIZ MACCIRE")
    c = campos("de Processo Disciplinar no 197/2005, acordam os membros do Tribunal de Ética e Disciplina XI, por", "Sala das Sessões, 1o de março de 2011. Rel. (voto divergente) Dr. Rafael Cury Bicalho - Presidente Dr. X Y.")
    assert (c["turma_n"], c["data_texto"], c["relator"]) == (11, "2011-03-01", "RAFAEL CURY BICALHO")
    c = campos("de Pedido de Reabilitação no 4744/1999, acordam os membros da XXII Turma de Ética e Disciplina, por", "Sala das Sessões, 25 março de 2011. Rel. \"ad hoc\" Dr. Alfredo José Diani - Presidente Dr. J S.")
    assert (c["turma_n"], c["numero_processo"], c["data_texto"], c["relator"]) == (22, "4744/1999", "2011-03-25", "ALFREDO JOSÉ DIANI")
    c = campos("do Processo PD22R000011/2011–Lins-SP, acordam os membros da Vigésima Segunda Turma Disciplinar TED XXII da OAB, por", "Sala das sessões, 30 de março de 2012. Rev. Dr. Fulano Beltrano - Presidente Dr. A B.")
    assert c["turma_n"] == 22 and c["numero_processo"] == "PD22R000011/2011" and c["relator"] is None  # só Revisor: sem relator
    c = campos("do Processo X 1/2, os membros decidiram.", "Sala das sessões, 1 de abril de 2012.")
    assert c["turma"] is None and c["turma_n"] is None  # sem turma nomeada: nada é inferido de palavra solta


# ----------------------------------------------------------------------------------------------------------------------
# GO / ementarios-ted e ementarios-conselho - ementários do TED e do Conselho Seccional da OAB/GO (HTML WordPress)
# ----------------------------------------------------------------------------------------------------------------------

class _ClienteGO:
    """GET devolve o HTML da fixture que corresponde ao fim da URL (ou o texto dado)."""

    def __init__(self, respostas: dict[str, str]):
        self._r, self.pedidos = respostas, []

    async def get(self, url, params=None, headers=None, **_k):
        self.pedidos.append(url)
        corpo = next(v for k, v in self._r.items() if url.endswith(k))
        return type("R", (), {"content": corpo.encode("utf-8"), "raise_for_status": lambda self: None})()


def _go_ted(slug: str, nome: str) -> dict:
    from ted_search.collectors.sites import go_ementarios_ted as m

    cliente = _ClienteGO({f"/ementarios-ted/{slug}/": _html(f"go_ementarios_ted_detalhe_{nome}.html")})
    return asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), {"id": slug, "pagina": 1, "no_sitemap": True}))


def test_go_ted_esperado_vem_do_contador_de_paginas_e_da_ultima_pagina():
    from ted_search.collectors.sites import go_ementarios_ted as m

    m._info.clear()
    cliente = _ClienteGO({"/ementarios-ted/": _html("go_ementarios_ted_lista_p1.html"), "/page/679/": _html("go_ementarios_ted_lista_ultima.html")})
    assert asyncio.run(m.esperado(cliente, asyncio.Semaphore(1))) == 678 * 12 + 8
    assert m.itens_da_pagina(_html("go_ementarios_ted_lista_p1.html"))[:2] == ["9142", "9141"]
    assert cliente.pedidos == [m.BASE + "/", m.BASE + "/page/679/"]  # a página 1 é a raiz: /page/1/ redireciona
    m._info.clear()


def test_go_conselho_esperado_e_slugs_da_lista():
    from ted_search.collectors.sites import go_ementarios_conselho as m
    from ted_search.collectors.sites import go_ementarios_ted as ted

    ted._info.clear()
    cliente = _ClienteGO({"/ementarios-conselho-seccional/": _html("go_ementarios_conselho_lista_p1.html"),
                          "/page/16/": _html("go_ementarios_conselho_lista_ultima.html")})
    assert asyncio.run(m.esperado(cliente, asyncio.Semaphore(1))) == 15 * 12 + 5
    assert ted.itens_da_pagina(_html("go_ementarios_conselho_lista_p1.html"), m.SLUG_SITE)[0] == "processo-no-201506370"
    ted._info.clear()


def test_go_ted_formato_2025_processo_data_relator_e_camara():
    from ted_search.collectors.sites import go_ementarios_ted as m

    item = _go_ted("9142", "9142")
    assert item["url"].endswith("/ementarios-ted/9142/") and item["no_sitemap"] is True
    rec = m.normalizar(item)
    assert rec["id"] == "SITE_GO_EMENTARIOS_TED:9142" and rec["fonte"] == "SITE_GO_EMENTARIOS_TED"
    assert rec["numero_processo"] == "202442442" and rec["data_decisao"] == "2025-10-23" and rec["ano_ementa"] == 2025
    assert rec["publicacao_str"] == "Site da OAB/GO - data da publicação"  # só a publicação consta: a data não é da sessão
    assert rec["relator_principal"] == "ANDRÉ SOUSA CARNEIRO"
    assert rec["sigla_orgao"] == "OABGO-TED-C7" and rec["orgao_julgador_norm"] == "Tribunal de Ética e Disciplina da OAB/GO/7ª Câmara Julgadora"
    assert rec["descricao_ementa"].startswith("REPRESENTAÇÃO DISCIPLINAR") and rec["descricao_acordao"].startswith("Vistos, relatados")
    assert rec["is_ted_disciplinar"] == 1 and rec["tem_inteiro_teor"] == 0 and rec["pdf_url"] is None
    assert rec["contexto"] == "EMENTA" and rec["uf_origem"] == "GO"


def test_go_ted_formato_2003_ementa_e_assinatura_no_mesmo_paragrafo():
    from ted_search.collectors.sites import go_ementarios_ted as m

    rec = m.normalizar(_go_ted("2", "2"))
    assert rec["numero_processo"] == "2.825/2000" and rec["data_decisao"] == "2003-02-05"
    assert rec["relator_principal"] == "CARLOS RABELO"  # 'Relator – Juiz Carlos Rabelo.'
    assert rec["sigla_orgao"] == "OABGO-TED-T2" and rec["orgao_julgador_raw"] == "2ª Turma"
    assert rec["descricao_ementa"].startswith("Representação. Fatos não comprovados") and rec["descricao_acordao"] == ""


def test_go_ted_formatos_intermediarios_sem_turma_numerada_nao_inventam_orgao():
    from ted_search.collectors.sites import go_ementarios_ted as m

    rec = m.normalizar(_go_ted("2500", "2500"))  # 'Presidente: X' sem número de turma, nem fórmula 'acordam'
    assert (rec["numero_processo"], rec["data_decisao"], rec["relator_principal"]) == ("2010/04522", "2013-05-22", "THIAGO FERREIRA DE SOUZA")
    assert rec["sigla_orgao"] == "OABGO-TED" and rec["orgao_julgador_norm"] == "Tribunal de Ética e Disciplina da OAB/GO"
    rec = m.normalizar(_go_ted("3000", "3000"))  # tudo num parágrafo só: 'Proc. nº ... Presidente da 4ª Turma: Dr. ... Relator: Dr. ...'
    assert (rec["numero_processo"], rec["data_decisao"], rec["relator_principal"]) == ("2013/00564", "2014-04-22", "NELSON RODRIGUES MARTINS JÚNIOR")
    assert rec["sigla_orgao"] == "OABGO-TED-T4"
    rec = m.normalizar(_go_ted("7947", "7947"))  # 'EMENTE:' (erro do site) e 'ACÓRDÃO.' com ponto
    assert rec["sigla_orgao"] == "OABGO-TED-C7" and rec["data_decisao"] == "2020-12-09"
    assert rec["descricao_ementa"].startswith("PROCESSO ÉTICO-DISCIPLINAR") and rec["descricao_acordao"].startswith("Vistos, relatados")


def test_go_ted_ordinal_por_extenso_e_slug_nao_numerico():
    from ted_search.collectors.sites import go_ementarios_ted as m

    item = _go_ted("9019-2", "9019-2")
    rec = m.normalizar(item)
    assert rec["id"] == "SITE_GO_EMENTARIOS_TED:9019-2" and rec["sigla_orgao"] == "OABGO-TED-C1"  # 'primeira câmara'
    assert rec["relator_principal"] == "SÉRGIO DOURADO FRANÇA"  # 'Relçator(a)' (erro de digitação do site)
    for txt, esperado in (("os integrantes da Décima Primeira Turma", 11), ("integrantes da __5___ª Câmara", 5),
                          ("integrantes da 9.ª Câmara", 9), ("integrantes da Vigésima Câmara", 20)):
        assert m.ordinal_antes(txt, txt.index("Turma") if "Turma" in txt else txt.index("Câmara")) == esperado


def test_go_ted_sem_data_no_texto_fica_nula_e_item_sem_texto_e_rejeitado():
    from ted_search.collectors.sites import go_ementarios_ted as m

    base = {"id": "1", "url": "u", "paragrafos": ["EMENTA: Representação improcedente por falta de provas dos fatos alegados na inicial pelo representante. ACÓRDÃO: Improcedente."]}
    rec = m.normalizar(base)
    assert rec["data_decisao"] is None and rec["ano_ementa"] is None and rec["numero_processo"] is None and rec["relator_principal"] is None
    assert m.normalizar({**base, "paragrafos": []}) is None
    assert m.normalizar({**base, "paragrafos": ["Parece que não encontramos o que você está procurando. Utilize o menu superior para navegar pelo site."]}) is None
    assert m.extrair_data("Data da sessão: 13 09 2022")[0] == "2022-09-13"
    assert m.extrair_data("Data da sessão: 13.09.2052")[0] is None  # ano impossível: erro de digitação
    assert m.extrair_data("Data da Sessão: 10/11/216 Ementa")[0] is None  # ano com 3 dígitos (site): não se adivinha 2016
    assert m.extrair_data("o fato ocorreu em 12.03.2001, conforme o laudo. Ementa longa que segue sem mais datas por um bom trecho do texto " * 3)[0] is None


def test_go_conselho_detalhe_disciplinar_camara_relator_e_data_da_sessao():
    from ted_search.collectors.sites import go_ementarios_conselho as m

    cliente = _ClienteGO({"/processo-no-201508801/": _html("go_ementarios_conselho_detalhe_201508801.html")})
    item = asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), {"id": "processo-no-201508801"}))
    rec = m.normalizar(item)
    assert rec["id"] == "SITE_GO_EMENTARIOS_CONSELHO:processo-no-201508801" and rec["numero_processo"] == "201508801"
    assert rec["data_decisao"] == "2018-11-07" and rec["relator_principal"] == "JOSÉ HUMBERTO ABRÃO MEIRELES"
    assert rec["sigla_orgao"] == "OABGO-1CR" and rec["is_ted_disciplinar"] == 1 and rec["tem_inteiro_teor"] == 0
    assert rec["descricao_ementa"].startswith("RECURSO AO CONSELHO SECCIONAL") and rec["descricao_acordao"].startswith("Vistos, relatados")
    assert "–" in item["texto"] and "\x96" not in item["texto"]  # travessão do Word (C1) lido como cp1252


def test_go_conselho_inscricao_e_materia_administrativa_e_redator_nao_e_relator():
    from ted_search.collectors.sites import go_ementarios_conselho as m

    def rec(nome: str, slug: str) -> dict:
        cliente = _ClienteGO({f"/{slug}/": _html(f"go_ementarios_conselho_detalhe_{nome}.html")})
        return m.normalizar(asyncio.run(m.baixar(cliente, asyncio.Semaphore(1), {"id": slug})))

    r = rec("201804179", "processo-no-201804179")  # inscrição originária
    assert r["is_ted_disciplinar"] == 0 and r["sigla_orgao"] == "OABGO-PLENO" and r["data_decisao"] == "2018-12-14"
    assert r["relator_principal"] == "ARCÊNIO PIRES DA SILVEIRA"
    r = rec("201406462", "processo-no-201406462")  # só 'Redator' assina: relator não identificado
    assert r["relator_principal"] is None and r["is_ted_disciplinar"] == 1 and r["sigla_orgao"] == "OABGO-1CR"
    r = rec("2005-5808", "processo-no-2005-5808")  # 'Relator: Conselheira Alyne Cristine Lopes' no cabeçalho
    assert r["relator_principal"] == "ALYNE CRISTINE LOPES" and r["numero_processo"] == "2005/5808" and r["data_decisao"] == "2015-05-20"
    r = rec("proesso-201507382", "proesso-no-201507382")  # título do site com erro de digitação ('Proesso')
    assert r["numero_processo"] == "201507382" and r["data_decisao"] == "2018-06-13"
    assert m.normalizar({"id": "x", "url": "u", "paragrafos": ["Processo nº 1"]}) is None


# ----------------------------------------------------------------------------------------------------------------------
# PR / jurisprudencia - buscador de jurisprudência da OAB/PR (API JSON, sem rede aqui)
# ----------------------------------------------------------------------------------------------------------------------

def _pr_itens() -> list[dict]:
    return [{"id": str(i["ind"]), **i} for i in _fixture("pr_jurisprudencia_pagina_amostra.json")["data"]]


def test_pr_advogado_igual_ao_relator_nao_e_parte():
    from ted_search.collectors.sites import pr_jurisprudencia as m

    it = {**next(i for i in _pr_itens() if i["id"] == "66526")}
    it["advogado"] = {"nome": "LILIANE GRUHN"}  # o mesmo nome do relator do item
    rec = m.normalizar(it)
    assert rec["partes_json"] == "[]" and rec["relator_principal"] == "LILIANE GRUHN"
    assert m._data("2041-10-13T03:00:00.000Z") is None  # data no futuro
    assert m._data("2002-10-22T02:00:00.000Z") == "2002-10-22"


def test_pr_registro_campos_e_orgao():
    from ted_search.collectors.sites import pr_jurisprudencia as m

    por_id = {i["id"]: m.normalizar(i) for i in _pr_itens()}
    r = por_id["20400"]
    assert r["id"] == "SITE_PR_JURISPRUDENCIA:20400" and r["fonte"] == "SITE_PR_JURISPRUDENCIA"
    assert r["uf_origem"] == "PR" and r["id_organizacao"] == 16
    assert r["data_decisao"] == "1995-08-07" and r["numero_processo"] == "6152/1993"
    assert r["numero_ementa"] == "1" and r["ano_ementa"] == 1995
    assert r["pdf_url"] is None and r["tem_inteiro_teor"] == 0
    assert r["orgao_julgador_raw"] == "TRIBUNAL DE ÉTICA E DISCIPLINA" and r["is_ted_disciplinar"] == 1
    assert r["inteiro_teor_texto"].startswith(r["descricao_ementa"])
    t = por_id["66526"]
    assert t["orgao_julgador_raw"].endswith("/ 12ª TURMA") and t["sigla_orgao"] == "OABPR-TED-T12"
    assert t["relator_principal"] == "LILIANE GRUHN" and t["data_decisao"] == "2026-10-23"
    assert t["inteiro_teor_texto"].split("\n\n")[-1].startswith("Vistos")  # ementa + linha em branco + dispositivo
    assert por_id["16877"]["orgao_julgador_raw"] == "CONSELHO SECCIONAL"
    assert por_id["20"] is None  # ementa vazia


def test_pr_marca_disciplinar_por_orgao():
    from ted_search.collectors.sites import pr_jurisprudencia as m

    assert m._disciplinar("CÂMARA DE DISCIPLINA", "", "x") == 1
    assert m._disciplinar("TRIBUNAL DE ÉTICA E DISCIPLINA (SPD)", "", "x") == 1
    assert m._disciplinar("CÂMARA DE SELEÇÃO", "ABANDONO DE CAUSA", "representado processo disciplinar") == 0
    assert m._disciplinar("CÂMARA DE DIREITOS E PRERROGATIVAS", "", "x") == 0


def test_pr_sobreposicao_com_o_diario_so_na_janela_do_diario():
    from ted_search.collectors.sites import pr_jurisprudencia as m

    assert m.chave_digitos("4.724/2020") == m.chave_digitos("04724/2020") == "4724/2020"
    diario = {"4724/2020"}
    novo = {"numero_processo": "4724/2020", "data_decisao": "2025-02-03"}
    antigo = {"numero_processo": "4724/2020", "data_decisao": "2005-02-03"}  # outro julgamento do mesmo processo
    outro = {"numero_processo": "4725/2020", "data_decisao": "2025-02-03"}
    assert m.sobrepoe(novo, diario) and not m.sobrepoe(antigo, diario) and not m.sobrepoe(outro, diario)
    m.MOTIVOS.clear()
    m._DIARIO = diario
    try:
        it = next(i for i in _pr_itens() if i["id"] == "66526")
        it = {**it, "documentoProcesso": {"nrProcesso": 4724, "dtProcesso": "2020-07-02T03:00:00.000Z"}}
        assert m.normalizar(it) is None and m.MOTIVOS["66526"] == "sobreposicao DEOAB_PR"
    finally:
        m._DIARIO = None
        m.MOTIVOS.clear()


class _RespostaPR:
    def __init__(self, status: int, corpo: dict | None = None, headers: dict | None = None):
        self.status_code, self._corpo, self.headers = status, corpo, headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self) -> dict:
        if self._corpo is None:
            raise ValueError("não é JSON")
        return self._corpo


class _ClientePR:
    def __init__(self, respostas: list):
        self.respostas, self.params = list(respostas), []

    async def get(self, url, params=None, headers=None, **_k):
        self.params.append(dict(params or {}))
        assert "ted-pesquisa" in headers["User-Agent"]
        return self.respostas.pop(0)


def _ajusta_pr(monkeypatch, tmp_path):
    from ted_search.collectors.sites import pr_jurisprudencia as m

    monkeypatch.setattr(m, "INTERVALO", 0.0)
    monkeypatch.setattr(m, "ESPERAS", (0, 0))
    monkeypatch.setattr(m, "_raw", lambda: tmp_path)
    return m


def test_pr_listar_pagina_cache_e_repete_em_429(monkeypatch, tmp_path):
    m = _ajusta_pr(monkeypatch, tmp_path)
    itens = _fixture("pr_jurisprudencia_pagina_amostra.json")["data"]
    pag = lambda n: {"data": itens[:3] if n == 1 else itens[3:5], "pagination": {"page": n, "limit": 100, "total": 5, "totalPages": 2}}
    cli = _ClientePR([_RespostaPR(429, headers={"retry-after": "0"}), _RespostaPR(200, pag(1)), _RespostaPR(503), _RespostaPR(200, pag(2))])
    sem = asyncio.Semaphore(1)
    lista = asyncio.run(m.listar(cli, sem))
    assert [i["id"] for i in lista] == [str(i["ind"]) for i in itens[:5]]
    assert [p["page"] for p in cli.params] == [1, 1, 2, 2]
    # retomada: tudo no cache, nenhuma requisição nova
    cli2 = _ClientePR([])
    assert len(asyncio.run(m.listar(cli2, sem))) == 5 and cli2.params == []


def test_pr_esperado_e_parada_limpa_em_403_ou_nao_json(monkeypatch, tmp_path):
    import pytest

    m = _ajusta_pr(monkeypatch, tmp_path)
    sem = asyncio.Semaphore(1)
    ok = _ClientePR([_RespostaPR(200, {"data": [], "pagination": {"total": 54135, "totalPages": 54135}})])
    assert asyncio.run(m.esperado(ok, sem)) == 54135
    with pytest.raises(m.ApiBloqueada):
        asyncio.run(m.esperado(_ClientePR([_RespostaPR(403)]), sem))
    with pytest.raises(m.ApiBloqueada):
        asyncio.run(m.esperado(_ClientePR([_RespostaPR(200, None)]), sem))


def test_pr_marca_orgaos_fora_do_nucleo_por_assunto():
    from ted_search.collectors.sites import pr_jurisprudencia as m

    assert m._marca("CONSELHO SECCIONAL", "ELEIÇÕES", "Eleições 2015. Processo disciplinar antecedente de censura.", "") == 0
    assert m._marca("CÂMARA ESPECIAL", "PEDIDO DE REVISÃO DE PENA DISCIPLINAR", "Fica prejudicado.", "") == 1
    assert m._marca("CÂMARA ESPECIAL", "EMBARGOS DE DECLARAÇÃO", "A análise do pedido de revisão de processo é restrita", "") == 1
    assert m._marca("CONSELHO SECCIONAL", "EXCLUSÃO", "Inteligência do artigo 38, I, da Lei 8.906.", "") == 1
    assert m._marca("CÂMARA DE SELEÇÃO", "EXCLUSÃO", "x", "") == 0


def test_pr_texto_e_nomes_entram_como_publicados():
    """Regra do projeto: o texto vai para o banco exatamente como a fonte publica; nenhum nome é abreviado."""
    import json as _json

    from ted_search.collectors.sites import pr_jurisprudencia as m

    item = {
        "id": 900001, "ementa": "REPRESENTAÇÃO. O advogado Fulano de Tal Souza reteve valores da cliente Maria Beltrana Lima.",
        "titulo": "Vistos, relatados e discutidos, acordam os membros da Primeira Turma, por unanimidade.",
        "assunto": "RETENÇÃO DE VALORES", "advogado": {"nome": "Fulano de Tal Souza"}, "relator": "Sicrano Relator Pereira",
        "orgao": {"descricao": "TRIBUNAL DE ÉTICA E DISCIPLINA"}, "subOrgao": {"descricao": "PRIMEIRA TURMA"},
        "dtJulgamento": "2010-05-10T00:00:00", "documentoProcesso": {"nrProcesso": 1234, "dtProcesso": "2009-03-01T00:00:00"},
        "nrAcordao": 77, "anoAcordao": 2010,
    }
    rec = m.normalizar(item)
    assert rec is not None
    assert "Fulano de Tal Souza" in rec["inteiro_teor_texto"] and "Maria Beltrana Lima" in rec["inteiro_teor_texto"]
    assert rec["texto_publicado"] == item["ementa"]
    assert _json.loads(rec["partes_json"])[0]["nome"] == "Fulano de Tal Souza"


# ----------------------------------------------------------------------------------------------------------------------
# DF / ementario-ted, SE / ementario e BA / consultivo - bases pequenas de sites (sem rede: trechos reais em fixtures/)
# ----------------------------------------------------------------------------------------------------------------------

class _RespBytes:
    def __init__(self, corpo: bytes, status: int = 200):
        self.content, self.status_code = corpo, status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _ClienteBytes:
    """Devolve a resposta da URL pedida (ou a única); guarda URLs e cabeçalhos."""

    def __init__(self, respostas: dict | bytes, status: int = 200):
        self.respostas, self.status, self.pedidos = respostas, status, []

    async def get(self, url, headers=None, **_k):
        self.pedidos.append((url, headers))
        corpo = self.respostas if isinstance(self.respostas, bytes) else self.respostas[url]
        return _RespBytes(corpo, self.status)


def _sem_pausa(monkeypatch):
    from ted_search.collectors.sites import coleta_educada as ce

    monkeypatch.setattr(ce, "PAUSA", 0.0)
    ce.reiniciar()
    return ce


def test_coleta_educada_user_agent_e_parada_limpa_em_403_e_captcha(monkeypatch):
    import pytest

    ce = _sem_pausa(monkeypatch)
    sem = asyncio.Semaphore(1)
    ok = _ClienteBytes(b"<html>ok</html>")
    assert asyncio.run(ce.baixar_texto(ok, sem, "https://x/", marca="</html>")) == "<html>ok</html>"
    assert "TEDSearch" in ok.pedidos[0][1]["User-Agent"] and "coleta educada" in ok.pedidos[0][1]["User-Agent"]
    ruim = _ClienteBytes(b"", status=403)
    with pytest.raises(ce.BloqueioSite):
        asyncio.run(ce.baixar_texto(ruim, sem, "https://x/a"))
    assert len(ruim.pedidos) == 1  # sem repetição em 403
    outro = _ClienteBytes(b"<html>ok</html>")
    with pytest.raises(ce.BloqueioSite):  # depois do bloqueio, nenhuma requisição nova
        asyncio.run(ce.baixar_texto(outro, sem, "https://x/b"))
    assert outro.pedidos == []
    ce.reiniciar()
    cap = _ClienteBytes(b"<html><div class='g-recaptcha'></div></html>")
    with pytest.raises(ce.BloqueioSite):
        asyncio.run(ce.baixar_texto(cap, sem, "https://x/c"))
    ce.reiniciar()


def test_df_blocos_do_pdf_processo_relator_data_orgao_e_tema():
    from ted_search.collectors.sites import df_ementario_ted as m

    texto = (FIXTURES / "df_ementario_trecho.txt").read_text(encoding="utf-8")
    assert m.contar_rotulos(texto) == 4
    b = m.blocos(texto)
    assert [x["processo"] for x in b] == ["26568/2008", "29294/2008", "9/2007", "29453/2008"]
    assert b[0]["tema"] == "ABANDONO DE CAUSA" and b[1]["tema"] == "ATO EXCEDENTE DE ESTAGIÁRIO"  # 'A    TO' -> 'ATO'
    assert b[2]["tema"] == "AUSÊNCIA DE PROVAS" and b[3]["tema"] == "INADIMPLÊNCIA"
    assert b[0]["relator"] == "Rita de Cássia Nascimento Palma Gastaldi" and b[0]["data"] == "2013-04-24"
    assert b[0]["orgao"] == "1ª Turma do TED/OAB-DF" and b[2]["orgao"] == "Pleno do TED/OAB-DF"
    # rodapé fora do padrão: 'Relator Ismail Gomes, 9ª Turma, 26.11.2013, do TED/OAB-DF'
    assert b[3]["relator"] == "Ismail Gomes" and m.orgao_df(b[3]["orgao"])[0] == "OABDF-TED-T9"
    assert b[0]["texto"].startswith("“CONTRATAÇÃO PACTUADA") and b[0]["texto"].endswith("1ª Turma do TED/OAB-DF)")


def test_df_registro_ementa_dispositivo_sem_inteiro_teor(monkeypatch, tmp_path):
    from ted_search.collectors.sites import df_ementario_ted as m

    texto = (FIXTURES / "df_ementario_trecho.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    monkeypatch.setattr(m, "texto_do_pdf", lambda _p: texto)
    m._cache.clear()
    _sem_pausa(monkeypatch)
    cli, sem = _ClienteBytes(b"%PDF-1.4 falso"), asyncio.Semaphore(1)
    assert asyncio.run(m.esperado(cli, sem)) == 4
    itens = asyncio.run(m.listar(cli, sem))
    assert [i["id"] for i in itens] == [f"TED_Ementario_Rev04.pdf#{n}" for n in range(1, 5)]
    assert len(cli.pedidos) == 1  # o PDF único é baixado uma vez e fica em cache
    comp = asyncio.run(m.baixar(cli, sem, itens[0]))
    r = m.normalizar(comp)
    assert r["id"] == "SITE_DF_EMENTARIO_TED:TED_Ementario_Rev04.pdf#1" and r["fonte"] == "SITE_DF_EMENTARIO_TED"
    assert r["uf_origem"] == "DF" and r["numero_processo"] == "26568/2008" and r["data_decisao"] == "2013-04-24"
    assert r["sigla_orgao"] == "OABDF-TED-T1" and r["relator_principal"].startswith("Rita de Cássia")
    assert r["pdf_url"] == m.PDF_URL and r["is_ted_disciplinar"] == 1 and r["tem_inteiro_teor"] == 0
    assert r["contexto"] == "EMENTA" and r["ano_ementa"] == 2013
    assert r["descricao_ementa"].endswith("ADVERTÊNCIA EM OFÍCIO RESERVADO.") and r["descricao_acordao"].startswith("Acordam os membros da Turma")
    assert "Rita de Cássia Nascimento Palma Gastaldi" in r["inteiro_teor_texto"]  # o nome entra completo, como publicado
    assert m.normalizar(asyncio.run(m.baixar(cli, sem, itens[2])))["sigla_orgao"] == "OABDF-TED-PLENO"


def test_se_ementas_rodape_varios_processos_e_tese_solta():
    from ted_search.collectors.sites import se_ementario as m

    h = (FIXTURES / "se_ementario_abas.html").read_text(encoding="utf-8")
    assert m.esperado_da_pagina(h) == 6
    e = m.ementas(h)
    assert len(e) == 6 and len({x["id"] for x in e}) == 6
    assert [x["tema"] for x in e[:2]] == ["CONDUTA INCOMPÁTIVEL COM ADVOCACIA"] * 2
    proc = next(x for x in e if x["tema"] == "PROCURAÇÃO")
    assert proc["id"] == "T1-011-2016-20160330" and proc["relator"] == "RAFAEL ALMEIDA BRITO" and proc["data"] == "2016-03-30"
    assert proc["texto"].endswith("seu representante legal.")  # parágrafo solto depois do rodapé fica na ementa
    assert "Publicação: 06/05/2016)" in proc["texto"] and proc["publicacao"] == "06/05/2016"
    assert all(x["processos"] and x["relator"] and x["data"].startswith("2016") for x in e)


def test_se_varios_processos_primeiro_em_numero_e_todos_no_texto():
    from ted_search.collectors.sites import se_ementario as m

    h = (
        '<div class="gb-tabs__buttons"><button class="gb-tabs__button">ANUIDADE</button></div><div class="gb-tabs__items">'
        '<div class="gb-container gb-container-aa gb-tabs__item gb-tabs__item-open"><p><strong>PRIMEIRA TURMA</strong></p>'
        "<p>Anuidade. Inadimplência. Suspensão. (TED OAB/SE, Primeira Turma; Proc. TED nº. 255/2016, 263/2016, 271/2016 e 287/2016; "
        "Relator(a): TARCISIO ANDRE TARGINO MATOS; Julgamento: 14/11/2016; Publicação: 07/12/2016)</p></div></div>"
    )
    (e,) = m.ementas(h)
    assert e["processos"] == ["255/2016", "263/2016", "271/2016", "287/2016"]
    r = m.normalizar({**e, "id": e["id"]})
    assert r["numero_processo"] == "255/2016" and "287/2016" in r["inteiro_teor_texto"] and "TARCISIO ANDRE TARGINO MATOS" in r["inteiro_teor_texto"]
    assert r["id"] == "SITE_SE_EMENTARIO:T1-255-2016-20161114" and r["sigla_orgao"] == "OABSE-TED-T1"
    assert r["orgao_julgador_norm"].endswith("/1ª Turma Disciplinar") and r["data_decisao"] == "2016-11-14"
    assert r["tem_inteiro_teor"] == 0 and r["is_ted_disciplinar"] == 1 and r["pdf_url"] is None


def test_se_listar_confere_contra_a_contagem_independente(monkeypatch, tmp_path):
    from ted_search.collectors.sites import se_ementario as m

    h = (FIXTURES / "se_ementario_abas.html").read_bytes()
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    m._cache.clear()
    _sem_pausa(monkeypatch)
    cli, sem = _ClienteBytes(h), asyncio.Semaphore(1)
    assert asyncio.run(m.esperado(cli, sem)) == len(asyncio.run(m.listar(cli, sem))) == 6
    assert len(cli.pedidos) == 1 and (tmp_path / "site_se_ementario" / "pagina.html").exists()
    m._cache.clear()


def _ba_itens() -> dict:
    from ted_search.collectors.sites import ba_consultivo as m

    html = (FIXTURES / "ba_consultivo_texto_trecho.html").read_text(encoding="utf-8")
    return html, {i["id"]: i for i in m.itens_da_pagina(html)}


def test_ba_itens_da_pagina_juntam_inline_lista_e_2026_e_ignoram_o_resto():
    from ted_search.collectors.sites import ba_consultivo as m

    html, it = _ba_itens()
    # o que não é consulta (orientação técnica, ementário disciplinar 2007-2009) e a citação '236/2019' no meio de ementa ficam de fora
    assert sorted(it) == ["00297-2019", "00303-2026", "00408-2020", "00467-2020", "00578-2018", "00666-2020", "00784-2024", "00862-2022", "00932-2023", "00977-2022"]
    assert m.consultas_citadas(html) == set(it) and "00236-2019" not in it
    assert it["00932-2023"]["href"].endswith("Processo_Consulta_n_00932_2023.pdf") and it["00932-2023"]["ementa_pagina"].startswith("ASSUNTO: Violência processual")
    assert it["00784-2024"]["texto_lista"].startswith("ASSUNTO: Publicidade")  # cabeçalho e ASSUNTO em parágrafos separados
    assert it["00467-2020"]["ementa_inline"].startswith("PROCESSO DISCIPLINAR. PRESCRIÇÃO. COVID-19")  # 'Processo Disciplinar' sem 'nº' não encerra a lista
    assert it["00297-2019"]["data_pagina"] == "2019-08-08" and it["00297-2019"]["relator_pagina"] == "LISIANE MARIA GUIMARÃES SOARES"
    assert it["00303-2026"]["href"] is None and it["00303-2026"]["data_pagina"] == "2026-04-28"
    assert "Processo Disciplinar" not in it["00977-2022"]["ementa_pagina"]  # a lista 2007-2009 não vaza para a consulta anterior
    assert m.numero("00578-2018") == "00578/2018" and m.url_pdf("/fotos/x/PROCESSO CONSULTA Nº 578-2018.pdf").endswith("PROCESSO%20CONSULTA%20N%C2%BA%20578-2018.pdf")


def test_ba_data_e_relator_do_texto():
    from ted_search.collectors.sites import ba_consultivo as m

    assert m.data_do_texto("... Consulta respondida. Sessão 21.11.2019 – RELATOR: JOÃO ROSA.") == "2019-11-21"
    assert m.data_do_texto("em Sessão Ordinária realizada em 28/4/2026, por unanimidade") == "2026-04-28"
    assert m.data_do_texto("texto\nSalvador, 29 de setembro de 2023.\nConselheiro Relator") == "2023-09-29"
    assert m.data_do_texto("Salvador, 03/3/2026, Deraldo Barbosa Brandão Filho") == "2026-03-03"
    assert m.data_do_texto("sem data") is None
    assert m.relator_do_texto("Sessão 21.11.2019 – RELATOR: JOÃO ROSA.") == "JOÃO ROSA"


def test_ba_cpf_so_com_digitos_reais_e_placeholder_a_parte():
    from ted_search.collectors.sites import ba_consultivo as m

    # números sintéticos (nada de pessoa real em fixture)
    capa = "Processo: 1/2018 / Ético / CONSULTA\nRepresentado: (SEM REPRESENTADO) - 111.111.111-11\nRepresentante: FULANO DE TAL - 12345 - 123.456.789-09\nAssunto"
    corpo = "RELATÓRIO\nProcesso 0000123-45.2018.8.05.0001, telefone (71) 3329-8921, 1.234.567/2018."
    r = m.achar_cpf([capa, corpo, "Fulano, CPF 987.654.321-00, advogado", "CPF: 98765432100", "Inscrito no CPF sob o número ilegível", "só placeholder 111.111.111-11"])
    assert r["cpf"] == [1, 3, 4] and r["placeholder"] == [6] and r["palavra"] == [5]
    assert m.achar_cpf([corpo]) == {"cpf": [], "placeholder": [], "palavra": []}  # número de processo/telefone não é CPF


def test_ba_ler_pdf_usa_camada_de_texto_e_ocr_so_onde_a_pagina_e_imagem(monkeypatch):
    from pathlib import Path

    from ted_search.collectors.sites import ba_consultivo as m

    paginas = {1: "Folha 1/3\n" + "texto com camada própria " * 10, 2: "", 3: "  \n"}
    monkeypatch.setattr(m, "_paginas_pdf", lambda _p: 3)
    monkeypatch.setattr(m, "_camada_texto", lambda _p, n: paginas[n])
    monkeypatch.setattr(m, "_ocr", lambda _p, n: f"OCR da página {n} CPF 123.456.789-09")
    r = m.ler_pdf(Path("x.pdf"))
    assert r["paginas"] == 3 and r["paginas_ocr"] == [2, 3] and r["cpf_paginas"] == [2, 3]
    assert r["texto_pdf"].split("\n\n")[0].startswith("Folha 1/3") and "OCR da página 3" in r["texto_pdf"]


def test_ba_registro_consulta_pdf_selo_pelo_criterio_e_adiamento_por_cpf():
    from ted_search.collectors.sites import ba_consultivo as m

    _html, it = _ba_itens()
    ok = {**it["00932-2023"], "pdf_url": "https://www.oab-ba.org.br/fotos/oab_institucionais/19/mg/Processo_Consulta_n_00932_2023.pdf",
          "texto_pdf": "Folha 1/2\nProcesso Consulta n°: 932/2023\nRelator: Eurípedes Brito Cunha Junior, OAB/BA 11.433\n\nRELATÓRIO\nOs fatos.\n\nVOTO\nO voto.\n"
                       "Salvador, 29 de setembro de 2023.\n", "cpf_paginas": []}
    r = m.normalizar(ok, liberar=set())
    assert r["id"] == "SITE_BA_CONSULTIVO:00932-2023" and r["numero_processo"] == "00932/2023" and r["contexto"] == "EMENTA_CONSULTA"
    assert r["tipo_ementa"] == "CONSULTA" and r["is_ted_disciplinar"] == 1 and r["sigla_orgao"] == "OABBA-TED-OCEP"
    assert r["data_decisao"] == "2023-09-29" and r["ano_ementa"] == 2023 and r["pdf_url"].endswith("Processo_Consulta_n_00932_2023.pdf")
    assert r["descricao_ementa"].startswith("ASSUNTO: Violência processual") and r["inteiro_teor_texto"] == ok["texto_pdf"].strip()
    assert r["sancao_identificada"] is None and r["resultado_julgamento"] is None
    assert r["tem_inteiro_teor"] == 0  # texto curto demais para o critério do projeto: o selo não é forçado
    # texto de verdade com relatório e voto passa no critério do projeto
    longo = ok["texto_pdf"] + "\n" + ("Fundamentação do voto do relator. " * 80)
    from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

    assert texto_tem_relatorio_e_voto(longo)
    assert m.normalizar({**ok, "texto_pdf": longo}, liberar=set())["tem_inteiro_teor"] == 1
    # sem PDF: acórdão inline; selo 0 sem relatório e voto
    sem_pdf = m.normalizar(it["00303-2026"], liberar=set())
    assert sem_pdf["pdf_url"] is None and sem_pdf["data_decisao"] == "2026-04-28" and sem_pdf["inteiro_teor_texto"].startswith("EMENTA N. 1193/2026")
    # CPF no PDF: a carga é adiada (o texto não é alterado) até o id ser liberado
    m.MOTIVOS.clear()
    com_cpf = {**ok, "id": "00578-2018", "cpf_paginas": [1]}
    assert m.normalizar(com_cpf, liberar=set()) is None and "pág. 1" in m.MOTIVOS["00578-2018"]
    assert m.normalizar(com_cpf, liberar={"00578-2018"}) is not None
    assert m.normalizar({**com_cpf, "cpf_paginas": []}, liberar=set()) is not None
    m.MOTIVOS.clear()


def test_ba_texto_e_nomes_entram_como_publicados():
    """Regra do projeto: nenhum nome é abreviado, mascarado ou cortado."""
    from ted_search.collectors.sites import ba_consultivo as m

    item = {"id": "00357-2026", "numero": "00357/2026", "ano": 2026, "href": None, "ementa_inline": None, "texto_lista": "ACÓRDÃO\nConsulente: Dra. Fulana de Tal Beltrana, OAB-BA 1.234 – RELATORA: CAMILA VASCONCELOS DE OLIVEIRA. Sessão 26/8/2026.",
            "ementa_pagina": "ACÓRDÃO\nConsulente: Dra. Fulana de Tal Beltrana, OAB-BA 1.234 – RELATORA: CAMILA VASCONCELOS DE OLIVEIRA. Sessão 26/8/2026.",
            "data_pagina": "2026-08-26", "relator_pagina": "CAMILA VASCONCELOS DE OLIVEIRA"}
    r = m.normalizar(item, liberar=set())
    assert "Fulana de Tal Beltrana" in r["inteiro_teor_texto"] and r["relator_principal"] == "CAMILA VASCONCELOS DE OLIVEIRA"


def test_ba_data_do_pdf_so_no_fim_e_dentro_da_janela_de_anos():
    from ted_search.collectors.sites import ba_consultivo as m

    capa = "Protocolo 15201 05/04/2019 14:53\nSessão 08.8.2019 citada em outra consulta\n" + ("x " * 4000)
    fim_sessao = "ACÓRDÃO ... Sessão Ordinária realizada no dia 02/05/2019 ...\nSalvador, 03 de junho de 2019\n"
    assert m.data_do_pdf(capa + fim_sessao, 2018) == "2019-05-02"  # a sessão do acórdão vence a data da assinatura
    assert m.data_do_pdf(capa + "Salvador, 29 de setembro de 2023.\nConselheiro Relator", 2023) == "2023-09-29"
    assert m.data_do_pdf(capa + "Sessão 08.8.2019 citada\n", 2023) is None  # fora da janela (citação de consulta antiga) e sem outra data
    assert m.data_do_pdf(capa + "Salvador, 31 de março de 20283\n", 2022) is None  # erro de OCR: nada é inventado
    assert m.relator_do_texto("Processo Consulta n°: 932/2023\nRelator: Eurípedes Brito Cunha Junior, OAB/BA 11.433\n") == "Eurípedes Brito Cunha Junior"
    assert m.relator_do_texto("RELATOR (A): EDUARDO LIMA SODRÉ - Nº Registro16391") == "EDUARDO LIMA SODRÉ"
    assert m.relator_do_texto("Relator: João Rosa (OAB/BA 17.023)") == "João Rosa"
    assert m.relator_do_texto("sem relator") is None


# ----------------------------------------------------------------------------------------------------------------------
# RJ / jurisprudencias, MA / ementario-ted, MT / ementario-1996-2005, PB / jurisprudencia
# ----------------------------------------------------------------------------------------------------------------------

def test_rj_modais_texto_como_publicado_e_decisao_vazia():
    from ted_search.collectors.sites import rj_jurisprudencias as m

    h = (FIXTURES / "rj_jurisprudencias_2020_trecho.html").read_text(encoding="utf-8")
    assert m.linhas_da_tabela(h) == 3
    ms = m.modais(h, 2020)
    assert [x["id"] for x in ms] == ["2020-0000", "2020-0001", "2020-0002"]
    a = ms[0]
    assert a["processo"] == "009.501/18" and a["data"] == "03/02/2020" and a["relator"] == "STÉFANO EGMONT BALTZ"
    # hifenização quebrada, palavras coladas e espaços duplos: entram como estão
    assert "judi-cial  e não faz o repasse" in a["ementa"] and "darepresentação" in a["ementa"] and "suspen-são  por  30" in a["ementa"]
    assert a["decisao"] == "SUSPENSAO POR  30 DIAS E ATE QUE PRESTE CONTAS" and ms[1]["decisao"] == ""


def test_rj_registro_orgao_marca_e_sem_inteiro_teor():
    from ted_search.collectors.sites import rj_jurisprudencias as m

    h = (FIXTURES / "rj_jurisprudencias_2020_trecho.html").read_text(encoding="utf-8")
    ms = m.modais(h, 2020)
    m.MOTIVOS.clear()
    m._DIARIO = None
    r = m.normalizar(ms[0])
    assert r["id"] == "SITE_RJ_JURISPRUDENCIAS:2020-0000" and r["fonte"] == "SITE_RJ_JURISPRUDENCIAS" and r["uf_origem"] == "RJ"
    assert r["sigla_orgao"] == "OABRJ-TED-T1" and r["orgao_julgador_norm"].endswith("/1ª Turma Disciplinar") and r["orgao_julgador_raw"] == "1a. TURMA DO TED"
    assert r["data_decisao"] == "2020-02-03" and r["numero_processo"] == "009.501/18" and r["relator_principal"] == "STÉFANO EGMONT BALTZ"
    assert r["descricao_ementa"] == ms[0]["ementa"] and r["descricao_acordao"] == ms[0]["decisao"]
    assert r["pdf_url"] is None and r["tem_inteiro_teor"] == 0 and r["is_ted_disciplinar"] == 1 and r["contexto"] == "EMENTA"
    assert m.normalizar(ms[1])["descricao_acordao"] == ""  # decisão vazia não impede a carga
    # marca: Turmas do TED 1; Câmaras Especializadas 0; Turma Única 1; Pleno do T.E.D. e demais pela regra do Diário
    assert m.orgao_rj("10a.TURMA DO TED")[0] == "OABRJ-TED-T10" and m.orgao_rj("11a TURMA DO TED")[0] == "OABRJ-TED-T11"
    assert m.orgao_rj("1a. CÂMARA ESPECIALIZADA")[0] == "OABRJ-CE1" and m.orgao_rj("TURMA ÚNICA DO TED")[0] == "OABRJ-TED-TU"
    assert m.orgao_rj("PLENO DO T.E.D.")[0] == "OABRJ-TED-PLENO" and m.orgao_rj("ÓRGÃO ESPECIAL")[0] == "OABRJ-OE"
    assert m.marca_disciplinar("1a. CAMARA ESPECIALIZADA", "REPRESENTAÇÃO DISCIPLINAR", "") == 0
    assert m.marca_disciplinar("7a. TURMA DO TED", "pedido de inscrição", "") == 1
    assert m.marca_disciplinar("TURMA ÚNICA DO TED", "", "") == 1


def test_rj_sobreposicao_exige_mesmo_processo_e_ementa_no_texto_do_diario():
    from ted_search.collectors.sites import rj_jurisprudencias as m

    assert m.chave_processo("009.501/18") == m.chave_processo("9.501/2018") == "9501/2018"
    assert m.chave_processo("088.709/87") == "88709/1987"
    ementa = "Representar  disciplinar.  Levantamento de alvará judi-cial  e não faz o repasse a seu cliente. Procedência darepresentação."
    item = {"processo": "009.501/18", "ementa": ementa}
    texto_diario = m.so_letras("Publicado. EMENTA: Representar disciplinar. Levantamento de alvará judicial e não faz o repasse a seu cliente. Procedência da representação. Fim.")
    assert m.sobrepoe(item, {"9501/2018": [texto_diario]})
    assert not m.sobrepoe(item, {"9501/2018": [m.so_letras("outra decisão do mesmo processo, ementa diferente")]})  # mesmo processo, outro texto
    assert not m.sobrepoe(item, {"1/2018": [texto_diario]})  # outro processo
    m.MOTIVOS.clear()
    m._DIARIO = {"9501/2018": [texto_diario]}
    try:
        assert m.normalizar({"id": "2020-0000", "ano_pagina": 2020, "orgao": "1a. TURMA DO TED", "data": "03/02/2020", "decisao": "", **item}) is None
        assert m.MOTIVOS["2020-0000"].startswith("sobreposicao DEOAB_RJ")
    finally:
        m._DIARIO = None
        m.MOTIVOS.clear()


def test_rj_listar_uma_pagina_por_ano_com_pausa_e_confere_tabela(monkeypatch, tmp_path):
    from ted_search.collectors.sites import rj_jurisprudencias as m

    h = (FIXTURES / "rj_jurisprudencias_2020_trecho.html").read_bytes()
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    monkeypatch.setattr(m, "ANOS", range(2020, 2021))
    m._cache.clear()
    _sem_pausa(monkeypatch)
    pausas = []
    monkeypatch.setattr(m, "PAUSA_RJ", 10.0)
    real_sleep = asyncio.sleep

    async def _sono(s, *a, **k):
        pausas.append(s)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sono)
    cli, sem = _ClienteBytes(h), asyncio.Semaphore(1)
    assert asyncio.run(m.esperado(cli, sem)) == len(asyncio.run(m.listar(cli, sem))) == 3
    assert len(cli.pedidos) == 1 and cli.pedidos[0][0].endswith("?year=2020&orgao=&relator=&keyword=")
    assert 10.0 in pausas  # Crawl-delay do robots.txt
    assert (tmp_path / "site_rj_jurisprudencias" / "pagina_2020.html").exists()
    m._cache.clear()


def test_ma_blocos_processo_acordao_relator_data_e_texto():
    from ted_search.collectors.sites import ma_ementario_ted as m

    h = (FIXTURES / "ma_ementario_ted_trecho.html").read_text(encoding="utf-8")
    assert h.count('class="ementa"') == 3
    b = m.blocos(h)
    assert [x["id"] for x in b] == ["10.0000.2014.004575-0-ac233", "0966-2013-ac178", "10.0000.2015.003851-0-ac188"]
    assert b[0]["relator"] == "CRISTIANE ROSE SOARES RIBEIRO" and b[0]["relatora"] and b[0]["data"] == "2016-09-14"  # data que fecha o texto
    assert b[1]["data"] == "2016-06-15" and b[2]["data"] == "2016-05-18"  # 'Sessao Plenaria do dia ...' e 'sessao do dia ...'
    assert "Repdo: A.J.P." in b[0]["texto"]  # iniciais como o site publica
    r = m.normalizar(b[0])
    assert r["id"] == "SITE_MA_EMENTARIO_TED:10.0000.2014.004575-0-ac233" and r["numero_ementa"] == "233/2016" and r["ano_ementa"] == 2016
    assert r["sigla_orgao"] == "OABMA-TED" and r["is_ted_disciplinar"] == 1 and r["tem_inteiro_teor"] == 0 and r["pdf_url"] is None
    assert r["descricao_ementa"] == b[0]["texto"] and "Sao Luis, 14 de setembro de 2016." in r["descricao_ementa"]


def test_ma_listar_confere_contra_contagem_de_blocos(monkeypatch, tmp_path):
    from ted_search.collectors.sites import ma_ementario_ted as m

    h = (FIXTURES / "ma_ementario_ted_trecho.html").read_bytes()
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    m._cache.clear()
    _sem_pausa(monkeypatch)
    cli, sem = _ClienteBytes(h), asyncio.Semaphore(1)
    assert asyncio.run(m.esperado(cli, sem)) == len(asyncio.run(m.listar(cli, sem))) == 3
    assert len(cli.pedidos) == 1 and (tmp_path / "site_ma_ementario_ted" / "pagina.html").exists()
    m._cache.clear()


def test_mt_itens_do_pdf_assunto_quebrado_pagina_e_rodape():
    from ted_search.collectors.sites import mt_ementario_1996_2005 as m

    texto = (FIXTURES / "mt_ementario_1996_2005_trecho.txt").read_text(encoding="utf-8")
    linhas = m.linhas_sem_paginacao(texto)
    assert len(m.cabecalhos(linhas, 2)) == 4 and m.cabecalhos(linhas, 1) == []  # a sequência tem de começar no número pedido
    it = m.itens_do_texto(texto, primeiro=2)
    assert [x["id"] for x in it] == [f"PDF1644.pdf#{n}" for n in (2, 3, 4, 5)]
    assert it[0]["assunto"] == "LOCUPLETAMENTO À CUSTA DO CLIENTE" and it[0]["processo"] == "551/99" and it[0]["data"] == "2000-04-27"
    assert it[0]["relator"] == "ÁDILA ARRUDA SAFI" and it[0]["votacao"] is None and it[0]["turma"] is None
    assert "\n15\n" not in it[0]["corpo"] and not it[0]["corpo"].rstrip().endswith("15")  # número de página fora do texto
    assert it[0]["corpo"].endswith("(Proc. N. 551/99, Rel. ÁDILA ARRUDA SAFI. J. 27/04/2000)")
    assert it[2]["assunto"] == "FALTA DE PAGAMENTO DAS CONTRIBUIÇÕES, MULTAS E PREÇOS DE SERVIÇOS"  # título em duas linhas, link na seguinte
    assert it[2]["votacao"] == "V.U" and it[2]["relator"] == "IVO MATIAS" and it[3]["processo"] == "2.030/01"
    assert "VOLTAR" not in " ".join(x["corpo"] for x in it).upper()


def test_mt_registro_ementa_consulta_e_turma():
    from ted_search.collectors.sites import mt_ementario_1996_2005 as m

    texto = (FIXTURES / "mt_ementario_1996_2005_trecho.txt").read_text(encoding="utf-8")
    it = m.itens_do_texto(texto, primeiro=2)
    r = m.normalizar({**it[1], "pdf_url": m.PDF_URL})
    assert r["id"] == "SITE_MT_EMENTARIO_1996_2005:PDF1644.pdf#3" and r["numero_processo"] == "025/97" and r["data_decisao"] == "2003-08-28"
    assert r["pdf_url"] == m.PDF_URL and r["diario_url"] == m.PDF_URL and r["sigla_orgao"] == "OABMT-TED" and r["is_ted_disciplinar"] == 1
    assert r["contexto"] == "EMENTA" and r["tem_inteiro_teor"] == 0 and r["numero_ementa"] == "3"
    assert r["descricao_ementa"].startswith("EMENTA: PROCESSO DISCIPLINAR.") and r["inteiro_teor_texto"].startswith("RETENÇÃO ABUSIVA DE AUTOS\n\nEMENTA:")
    assert m.orgao_mt("Quarta") == ("Quarta Turma", "OABMT-TED-T4", "Tribunal de Ética e Disciplina da OAB/MT/4ª Turma Disciplinar")
    c = m.normalizar({"id": "PDF1644.pdf#9", "n": 9, "assunto": "CONSULTA. CASO CONCRETO", "corpo": "EMENTA: " + "x" * 60, "processo": "1/99", "relator": "FULANO", "turma": "Terceira", "data": "1999-01-02"})
    assert c["contexto"] == "EMENTA_CONSULTA" and c["sigla_orgao"] == "OABMT-TED-T3"


def test_mt_relatorio_e_parecer_em_linha_propria_valem_inteiro_teor():
    from ted_search.collectors.sites import mt_ementario_1996_2005 as m
    from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

    linhas = (FIXTURES / "mt_ementario_1996_2005_consulta_trecho.txt").read_text(encoding="utf-8").split("\n")[3:]
    corpo = m.paragrafos(linhas)
    paras = corpo.split("\n\n")
    assert paras[0] == "RELATÓRIO" and paras[1].startswith("ANA LÚCIA RICARTE protocolizou")  # 'RELATÓRIO' colado no parágrafo seguinte no PDF
    longo = "RELATÓRIO\n\n" + "a " * 800 + "\n\nPARECER\n\n" + "b " * 800
    assert texto_tem_relatorio_e_voto(longo) and not texto_tem_relatorio_e_voto(corpo)  # o trecho curto não passa do mínimo: não se força


def test_mt_listar_confere_indice_e_corpo(monkeypatch, tmp_path):
    from ted_search.collectors.sites import mt_ementario_1996_2005 as m

    texto = (FIXTURES / "mt_ementario_1996_2005_trecho.txt").read_text(encoding="utf-8")
    linhas = m.linhas_sem_paginacao(texto)
    assert {5, 22, 29, 244, 161} <= m.numeros_do_indice(linhas, m.cabecalhos(linhas, 2)[0])  # números do índice remissivo (inclui linha de continuação)
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    monkeypatch.setattr(m, "texto_do_pdf", lambda _p: texto)
    m._cache.clear()
    _sem_pausa(monkeypatch)
    cli, sem = _ClienteBytes(b"%PDF-1.4 " + b"x" * 20000), asyncio.Semaphore(1)
    assert asyncio.run(m.listar(cli, sem)) == []  # o trecho não começa no 1: nada é inventado
    assert len(cli.pedidos) == 1 and (tmp_path / "site_mt_ementario_1996_2005" / "PDF1644.pdf").exists()
    m._cache.clear()


def test_pb_secao_arquivos_lista_so_os_pdfs_de_julgado_e_o_registro():
    from ted_search.collectors.sites import pb_jurisprudencia as m

    h = (FIXTURES / "pb_jurisprudencia_trecho.html").read_text(encoding="utf-8")
    assert m.esperado_da_pagina(h) == 1  # o PDF de regimento antes da seção não conta
    (item,) = m.itens_da_secao(h)
    assert item["id"] == "a8daef_998a10fefb4d4c6f8355c59f9c758c64.pdf" and item["publicacao"] == "2018-04-19"
    assert item["pdf_url"].endswith("a8daef_998a10fefb4d4c6f8355c59f9c758c64.pdf") and item["rotulo"].startswith("19/04/2018 - Processo")
    texto = m.paragrafos((FIXTURES / "pb_jurisprudencia_consulta.txt").read_text(encoding="utf-8"))
    r = m.normalizar({**item, "texto": texto})
    assert r["id"] == f"SITE_PB_JURISPRUDENCIA:{item['id']}" and r["contexto"] == "EMENTA_CONSULTA" and r["uf_origem"] == "PB"
    assert r["numero_processo"] == "150000.2017.011869-3" and r["numero_ementa"] == "004/2017" and r["data_decisao"] == "2018-04-13"
    assert r["relator_principal"] == "Carlos Neves Dantas Freire" and r["sigla_orgao"] == "OABPB-TED" and r["is_ted_disciplinar"] == 1
    assert r["pdf_url"] == item["pdf_url"] and r["inteiro_teor_texto"] == texto and "Lorine Sanches Vieira" in texto  # nome completo, como publicado
    assert r["tem_inteiro_teor"] == 0  # texto curto, sem cabeçalhos de relatório e voto: o critério do projeto decide
    assert r["descricao_ementa"].startswith("PROCESSO CONSULTA – CONSULENTE") and r["descricao_acordao"].startswith("Acordam os membros")
    assert texto.startswith("Processo - Consulta nº 150000.2017.011869-3") and texto.endswith("Relator Designado")  # tudo o que o PDF publica


# ----------------------------------------------------------------------------------------------------------------------
# RO / ementario-pdf e ementarios (HTML) e RN / consultas - texto e nomes entram como o site publica
# ----------------------------------------------------------------------------------------------------------------------

def _ro_pdf_texto() -> str:
    return (FIXTURES / "ro_ementario_trecho.txt").read_text(encoding="utf-8")


def test_ro_pdf_entradas_rodape_quebrado_tema_processo_relator_data_e_orgao():
    from ted_search.collectors.sites import ro_ementario_pdf as m

    texto = _ro_pdf_texto()
    es = m.entradas(texto)
    assert m.contar_citacoes(texto) == len(es) == 6  # contagem independente (citações no texto corrido) x entradas pelos rodapés
    assert [e["processo"] for e in es] == ["22.0000.2018.002737-6", "22.0000.2018.006208-2", "22.0000.2019.008237-6",
                                           "22.0000.2017.004772-0", "22.0000.2018.000481-5", "22.0000.2020.003873-0"]
    assert es[0]["tema"] == "LOCUPLETAMENTO" and es[0]["ementa"].startswith("R E P R E S E N TA Ç Ã O .")  # tema fora do texto; cabeçalho como no PDF
    assert es[0]["relator"] == "FILIPE CAIO BATISTA CARVALHO" and es[0]["orgao"] == "4ª Turma do TED/OAB/RO" and es[0]["data"] is None
    assert es[1]["rodape"].endswith("Página: 105).") and "DA SILVA" in es[1]["rodape"]  # rodapé partido pela quebra de página volta inteiro
    assert es[2]["data"] == "2020-12-15" and es[2]["relator"] == "Vinícius Pompeu da Silva Gordon"  # data do julgamento, não a do DEOAB
    assert es[3]["data"] == "2022-07-08" and es[3]["rodape"].endswith("p. 101)")
    assert es[4]["rodape"].startswith("Proc. 22.0000.2018.000481-5") and es[4]["data"] == "2022-12-09"  # rodapé sem parêntese de abertura
    assert es[5]["consulta"] is True and es[5]["orgao"] == "Conselho Pleno do TED/OAB/RO" and es[5]["data"] == "2020-07-06"
    assert not any("Ementário Tribunal de ética" in e["ementa"] or "(Proc" in e["ementa"] for e in es)  # cabeçalho corrido e rodapé fora da ementa
    assert m._ligaduras("Pai e lha, conito") == "Pai e filha, conflito"
    assert m.data_do_rodape("Julgado aos vinte e nove dias do mês de maio do ano de dois mil e vinte).") == "2020-05-29"
    assert m.data_do_rodape("(Proc. 22.0000.2018.002737-6/TED/OAB/RO. Rel. Dr. X da 4ª Turma. DEOAB, 13 de setembro de 2022)") is None


def test_ro_pdf_listar_confere_contra_a_contagem_independente(monkeypatch):
    from ted_search.collectors.sites import ro_ementario_pdf as m

    m._cache.clear()
    m._cache["texto"] = _ro_pdf_texto()
    sem = asyncio.Semaphore(1)
    itens = asyncio.run(m.listar(None, sem))
    assert asyncio.run(m.esperado(None, sem)) == len(itens) == 6
    assert itens[0]["id"] == "Ementario-OAB.pdf#1" and asyncio.run(m.baixar(None, sem, itens[0]))["pdf_url"] == m.PDF_URL
    m._cache.clear()


def test_ro_pdf_registro_texto_integral_marca_selo_e_sobreposicao_com_o_diario():
    from ted_search.collectors.sites import ro_ementario_pdf as m

    es = m.entradas(_ro_pdf_texto())
    item = {"id": "Ementario-OAB.pdf#3", **es[2]}
    m._DIARIO = None
    r = m.normalizar(item)
    assert r["fonte"] == "SITE_RO_EMENTARIO_PDF" and r["contexto"] == "EMENTA" and r["pdf_url"] == m.PDF_URL
    assert r["numero_processo"] == "22.0000.2019.008237-6" and r["data_decisao"] == "2020-12-15" and r["relator_principal"] == "Vinícius Pompeu da Silva Gordon"
    assert r["sigla_orgao"] == "OABRO-TED-T2" and r["is_ted_disciplinar"] == 1 and r["tem_inteiro_teor"] == 0  # só ementa + dispositivo
    assert r["descricao_ementa"] == item["ementa"] and r["descricao_acordao"] == item["rodape"] and item["rodape"] in r["inteiro_teor_texto"]
    assert normalizar_consulta(m, es[5])["contexto"] == "EMENTA_CONSULTA"
    # sobreposição: mesmo processo (o Diário traz espaços espúrios) E ementa normalizada no texto do registro do Diário
    assert m.digitos("22.0000.2019. 008237-6") == m.digitos("22.0000.2019.008237-6")
    no_diario = m.sem_acento_letras("ORDEM DOS ADVOGADOS " + item["ementa"] + " Processo 22.0000.2019.008237-6")
    m._DIARIO = {m.digitos("22.0000.2019. 008237-6"): [no_diario]}
    m.MOTIVOS.clear()
    assert m.normalizar(item) is None and "DEOAB_RO" in m.MOTIVOS[item["id"]]
    m._DIARIO = {m.digitos(item["processo"]): [m.sem_acento_letras("outra decisão do mesmo processo, com outra ementa")]}
    assert m.normalizar(item) is not None  # mesmo processo, ementa diferente: carrega
    m._DIARIO = {m.digitos("22.0000.2018.002737-6"): [no_diario]}
    assert m.normalizar(item) is not None  # mesma ementa, outro processo: carrega
    m._DIARIO = None
    m.MOTIVOS.clear()


def normalizar_consulta(m, entrada):
    return m.normalizar({"id": "Ementario-OAB.pdf#6", **entrada})


def test_ro_ementarios_cartoes_paginacao_e_detalhe():
    from ted_search.collectors.sites import ro_ementarios as m

    p1 = (FIXTURES / "ro_ementarios_lista_p1.html").read_text(encoding="utf-8")
    p2 = (FIXTURES / "ro_ementarios_lista_p2.html").read_text(encoding="utf-8")
    c1, c2 = m.cartoes(p1), m.cartoes(p2)
    assert [c["id"] for c in c1][0] == "ementario-do-tribunal-de-etica-e-disciplina" and len(c1) == 3 and len(c2) == 2
    assert c1[1]["ano_rotulo"] == 2019 and c1[1]["tema"] == "Abandono de Causa e Ausência em Audiência" and c1[0]["ano_rotulo"] == 2024
    assert m.proxima(p1).endswith("/ementarios/page/2/") and m.proxima(p2) is None
    d = m.detalhe((FIXTURES / "ro_ementarios_detalhe_abandono.html").read_text(encoding="utf-8"))
    assert d["titulo_post"].startswith("ABANDONAR A CAUSA SEM JUSTO MOTIVO") and d["data_post"] == "2020-07-13"
    assert d["texto"].startswith("– ARTIGO 34, INCISO XI DA LEI 8.906/94 – AUSÊNCIA DE PROVAS – INOCORRÊNCIA DA PRATICA DE INFRAÇÃO. NÃO CONFIGURADA")
    assert d["texto"].endswith("Rel. Dra. Cristhianne Paula Cremonese).") and "Terceira Turma Disciplinar" in d["texto"]


def test_ro_ementarios_listar_percorre_ate_a_ultima_pagina_e_confere_a_seguinte(monkeypatch, tmp_path):
    from ted_search.collectors.sites import ro_ementarios as m

    p1 = (FIXTURES / "ro_ementarios_lista_p1.html").read_bytes()
    p2 = (FIXTURES / "ro_ementarios_lista_p2.html").read_bytes()
    cli = _ClienteBytes({m.URL: p1, m.URL + "page/2/": p2})  # a página 3 não existe: o cliente falso levanta KeyError
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    m._cache.clear()
    _sem_pausa(monkeypatch)
    sem = asyncio.Semaphore(1)
    assert asyncio.run(m.esperado(cli, sem)) == len(asyncio.run(m.listar(cli, sem))) == 5  # 3 por página x 1 + 2 da última
    assert [u for u, _ in cli.pedidos] == [m.URL, m.URL + "page/2/", m.URL + "page/3/"]
    assert (tmp_path / "site_ro_ementarios" / "pagina_2.html").exists()
    m._cache.clear()


def test_ro_ementarios_registro_orgao_consulta_e_descartes():
    from ted_search.collectors.sites import ro_ementarios as m
    from ted_search.collectors.sites import ro_ementario_pdf as pdf

    consulta, sem_numeral, abandono = json.loads((FIXTURES / "ro_ementarios_itens.json").read_text(encoding="utf-8"))
    m._DIARIO = m._PDF = None
    m.MOTIVOS.clear()
    r = m.normalizar(abandono)
    assert r["fonte"] == "SITE_RO_EMENTARIOS" and r["numero_processo"] == "22.0000.2018.003763-0" and r["relator_principal"] == "Fátima Santos Fulber"
    assert r["orgao_julgador_raw"] == "1ª Turma Disciplinar do TED/OAB/RO" and r["sigla_orgao"] == "OABRO-TED-T1" and r["is_ted_disciplinar"] == 1
    assert r["data_decisao"] is None and r["ano_ementa"] == 2019 and r["publicacao_str"].endswith("(post de 13/07/2020)")  # a data do post não é a do julgamento
    assert r["descricao_acordao"].startswith("Vistos, relatados") and r["inteiro_teor_texto"] == abandono["texto"].strip() and r["tem_inteiro_teor"] == 0
    c = m.normalizar(consulta)
    assert c["contexto"] == "EMENTA_CONSULTA" and c["numero_processo"] == "22.0000.2019.010491-8" and c["orgao_julgador_raw"] == "Pleno do TED/OAB/RO"
    assert c["relator_principal"] == "Vinícius Pompeu da Silva Gordon"
    assert m.normalizar(sem_numeral)["relator_principal"] == "Antonio Pereira da Silva"  # citação '(OAB/RO, Tribunal de Ética, Processo ..., Rel. ...)'
    assert m.normalizar({**abandono, "id": "ementario-do-tribunal-de-etica-e-disciplina"}) is None and "PDF" in m.MOTIVOS["ementario-do-tribunal-de-etica-e-disciplina"]
    # descartes: Diário (processo + ementa) e a fonte do PDF (mesmo processo + começo da ementa)
    ementa = m._ementa_e_acordao(abandono["texto"])[0]
    m._DIARIO = {pdf.digitos("22.0000.2018.003763-0"): [pdf.sem_acento_letras(ementa)]}
    m._PDF = {}
    assert m.normalizar(abandono) is None and "DEOAB_RO" in m.MOTIVOS[abandono["id"]]
    m._DIARIO = {}
    m._PDF = {pdf.digitos("22.0000.2018.003763-0"): [pdf.sem_acento_letras("R E P R E S E N T A Ç Ã O " + ementa)]}
    assert m.normalizar(abandono) is None and "SITE_RO_EMENTARIO_PDF" in m.MOTIVOS[abandono["id"]]
    m._PDF = {pdf.digitos("22.0000.2018.003763-0"): [pdf.sem_acento_letras("outra decisão sem relação com este texto de ementa qualquer")]}
    assert m.normalizar(abandono) is not None
    m._DIARIO = m._PDF = None
    m.MOTIVOS.clear()


def test_rn_consultas_links_da_pagina_e_registro_com_texto_integral(monkeypatch, tmp_path):
    from ted_search.collectors.sites import rn_consultas as m

    pagina = (FIXTURES / "rn_consultas_pagina_trecho.html").read_bytes()
    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    m._cache.clear()
    _sem_pausa(monkeypatch)
    cli, sem = _ClienteBytes(pagina), asyncio.Semaphore(1)
    itens = asyncio.run(m.listar(cli, sem))
    assert asyncio.run(m.esperado(cli, sem)) == len(itens) == 4
    assert [i["id"] for i in itens] == ["133562018", "135792019", "40332019", "25342020"]  # por ano e número
    assert itens[0]["pdf_url"] == "https://www.oabrn.org.br/2017/arquivos/Consultas/CONSULTA-133562018-ESCRIToRIO-DE-ADVOCACIA-EM-SHOPPING-CENTER.pdf"
    assert itens[0]["numero"] == "13356/2018" and itens[0]["titulo_link"].startswith("Consulta 133562018-0: Escrit")
    assert (tmp_path / "site_rn_consultas" / "pagina.html").exists()
    m._cache.clear()
    texto = (FIXTURES / "rn_consultas_122682018.txt").read_text(encoding="utf-8")
    item = {"id": "122682018", "numero": "12268/2018", "ano": 2018, "pdf_url": "https://www.oabrn.org.br/2017/arquivos/Consultas/x.pdf", "texto_pdf": texto}
    r = m.normalizar(item)
    assert r["fonte"] == "SITE_RN_CONSULTAS" and r["contexto"] == "EMENTA_CONSULTA" and r["pdf_url"] == item["pdf_url"]
    assert r["inteiro_teor_texto"] == texto.strip() and "Hidalgo Xavier de Souza Rebouças OAB/RN 15.347" in r["inteiro_teor_texto"]  # texto e nomes como publicados
    assert r["numero_processo"] == "12268/2018" and r["numero_ementa"] == "122682018-0" and r["data_decisao"] == "2020-07-24"
    assert r["relator_principal"] == "José Heldison Carvalho de Aquino" and r["is_ted_disciplinar"] == 1 and r["sigla_orgao"] == "OABRN-TED"
    assert r["descricao_ementa"].startswith("Exercício da advocacia – Impedimentos.") and r["tem_inteiro_teor"] == 1  # RELATÓRIO e VOTO em linha própria
    assert m.relator_nome("Juiz Revisor José Héldison Carvalho de Aquino") == "José Héldison Carvalho de Aquino" and m.relator_nome("Juiz Pedro Avelino Neto") == "Pedro Avelino Neto"
    assert m.orgao_do_texto("Este Pleno não tem competência")[1] == "OABRN-TED-PLENO"
    m.MOTIVOS.clear()
    assert m.normalizar({**item, "texto_pdf": "   "}) is None and "sem texto" in m.MOTIVOS["122682018"]
    m.MOTIVOS.clear()


# ----------------------------------------------------------------------------------------------------------------------
# SC / ementarios-ted - acórdãos do TED/SC no site da OAB/SC (Next.js, HTML no servidor)
# ----------------------------------------------------------------------------------------------------------------------

def _pagina_sc() -> str:
    return (FIXTURES / "sc_ementarios_ted_pagina.html").read_text(encoding="utf-8")


def test_sc_cartoes_total_de_paginas_e_cartao_sem_texto():
    from ted_search.collectors.sites import sc_ementarios_ted as m

    h = _pagina_sc()
    assert m.total_paginas(h) == 826
    cs = m.cartoes(h)
    assert [c["ano_secao"] for c in cs] == ["2026", "2018", "1995"]
    assert cs[0]["titulo"] == "Processo de Representação nº 209/2026"
    assert cs[0]["acordao_rotulo"] == "Acórdão nº 400/2026"
    assert cs[1]["texto"] == "" and m._num_acordao(cs[1]["acordao_rotulo"]) == "9/2018"  # o site publica cartão vazio: conta como item


def test_sc_registro_recente_fica_como_o_site_publica():
    from ted_search.collectors.sites import sc_ementarios_ted as m

    c = m.cartoes(_pagina_sc())[0]
    rec = m.normalizar({"id": "400/2026", "pagina": 1, **c})
    assert rec["fonte"] == "SITE_SC_EMENTARIOS_TED"
    assert rec["numero_processo"] == "209/2026" and rec["numero_ementa"] == "400" and rec["ano_ementa"] == 2026
    assert rec["data_decisao"] == "2026-09-25"
    assert rec["relator_principal"] == "Ramon Luis Bianchi"
    assert rec["orgao_julgador_raw"] == "2ª Turma do Tribunal de Ética e Disciplina"
    assert rec["descricao_ementa"].startswith("“PROCESSO DE EXCLUSÃO. ARTIGO 38") and rec["descricao_ementa"].endswith("PROCEDÊNCIA”.")
    assert rec["descricao_acordao"].startswith("Vistos, relatados e discutidos")
    assert rec["inteiro_teor_texto"] == rec["descricao_ementa"] + "\n\n" + rec["descricao_acordao"]
    assert rec["pdf_url"] is None and rec["tem_inteiro_teor"] == 0 and rec["is_ted_disciplinar"] == 1
    assert "L. R. M. A." in json.loads(rec["partes_json"])[0]  # partes como publicadas (iniciais), sem mascarar nem abreviar


def test_sc_registro_antigo_usa_julgado_em_e_cartao_vazio_e_rejeitado():
    from ted_search.collectors.sites import sc_ementarios_ted as m

    cs = m.cartoes(_pagina_sc())
    rec = m.normalizar({"id": "2/1995", "pagina": 826, **cs[2]})
    assert rec["data_decisao"] == "1995-10-27" and rec["numero_processo"] == "192/93" and rec["numero_ementa"] == "2"
    assert rec["relator_principal"] == "Dr. Hélio Roque Rubick"
    assert rec["descricao_acordao"] == "" and rec["descricao_ementa"].startswith("Retenção abusiva de autos processuais")
    m.MOTIVOS.clear()
    assert m.normalizar({"id": "9/2018", "pagina": 302, **cs[1]}) is None
    assert m.MOTIVOS["9/2018"] == "ementa vazia"


def test_sc_data_relator_e_orgao_nas_variantes_do_texto():
    from ted_search.collectors.sites import sc_ementarios_ted as m

    assert m.extrair_data("... ACORDAM os Membros da 5ª Turma. Itajaí, 13 novembro de 2020. Elídia, Presidente.") == "2020-11-13"
    assert m.extrair_data("... Florianópolis, 1º de outubro de 2021. Francisco, Presidente.") == "2021-10-01"
    assert m.extrair_data("Ementa: texto que cita 3 de maio de 2010 no meio e termina sem assinatura " + "x" * 400) is None
    assert m.extrair_relator("Repdo: D. T.Relator do voto divergente: Wanderlei Deretti. Acórdão nº 057/2023. Ementa: x") == "Wanderlei Deretti"
    assert m.orgao_sc("ACORDAM os Membros da 3ª Turma do Tribunal")[1] == "OABSC-TED-T3"
    assert m.orgao_sc("texto truncado sem dispositivo")[1] == "OABSC-TED"
    assert m.eh_consulta("Pedido de Consulta nº 437/2018") and not m.eh_consulta("Processo de Representação nº 1/2020")


def test_sc_descartes_duplicata_interna_e_sobreposicao_com_o_diario():
    from ted_search.collectors.sites import sc_ementarios_ted as m

    def it(i, pagina, proc, ementa, ano="2020", fim="Joinville, 25 de setembro de 2020. X, Relator."):
        return {"id": i, "pagina": pagina, "titulo": f"Processo de Representação nº {proc}", "ano_secao": ano,
                "acordao_rotulo": f"Acórdão nº {i}", "texto": f"Repte: A. Relator: B. Ementa: “{ementa}”. Vistos, relatados e discutidos, ACORDAM os Membros da 1ª Turma. {fim}"}

    comum = "EMENTA UM ÁGIL " * 20
    itens = [it("1/2020", 1, "10/2019", comum), it("2/2020", 2, "10/2019", comum),   # duplicata: mesmo processo e mesma ementa
             it("3/2020", 3, "11/2019", comum),                                     # outro processo: não é duplicata
             it("4/2017", 4, "5/2016", "OUTRA EMENTA DE 2017 " * 20, ano="2017", fim="Florianópolis, 10 de março de 2017. X, Relator."),
             it("5/2020", 5, "12/2019", "EMENTA DISTINTA SO NO SITE " * 20)]
    diario = "|".join([m._letras("... " + comum + " ..."), m._letras("OUTRA EMENTA DE 2017 " * 20)])
    d = m.calcular_descartes(itens, diario)
    assert d["2/2020"].startswith("duplicata interna")
    assert d["1/2020"].startswith("sobreposição")           # julgado depois de 2018-10-01 e a ementa está no Diário
    assert d["3/2020"].startswith("sobreposição")
    assert "4/2017" not in d                                  # anterior ao Diário: nunca descartado, mesmo com texto igual
    assert "5/2020" not in d


def test_sc_esperado_e_listar_conferem_pagina_seguinte_vazia(monkeypatch, tmp_path):
    from ted_search.collectors.sites import coleta_educada as ce
    from ted_search.collectors.sites import sc_ementarios_ted as m

    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    monkeypatch.setattr(m, "PAUSA", 0)
    m._info.clear()
    ce.reiniciar()
    h = _pagina_sc()
    vazia = '<html>"totalPages\\":2,</html>'

    class Resp:
        status_code = 200
        headers: dict = {}

        def __init__(self, corpo):
            self.content = corpo.encode("utf-8")

        def raise_for_status(self):
            return None

    class Cli:
        def __init__(self):
            self.urls = []

        async def get(self, url, headers=None, **_k):
            self.urls.append(url)
            n = int(url.split("page=")[1].split("&")[0])
            return Resp(h.replace("826", "2") if n <= 2 else vazia)

    async def rodar():
        sem = asyncio.Semaphore(1)
        c = Cli()
        return await m.esperado(c, sem), await m.listar(c, sem), c.urls

    esperado, itens, urls = asyncio.run(rodar())
    assert esperado == 3 + 3 and len(itens) == 6                      # 2 páginas x 3 cartões
    assert [i["id"] for i in itens][:3] == ["400/2026", "9/2018", "2/1995"]
    assert itens[3]["id"] == "400/2026#2"                              # nº de acórdão repetido: não perde item
    assert any("page=3" in u for u in urls)                            # conferiu a página depois da última
    assert len(list((tmp_path / "site_sc_ementarios_ted" / "paginas").glob("*.html.gz"))) == 3  # cache bruto por página
    m._info.clear()


def test_sc_get_para_em_403_sem_insistir(monkeypatch, tmp_path):
    import pytest

    from ted_search.collectors.sites import coleta_educada as ce
    from ted_search.collectors.sites import sc_ementarios_ted as m

    monkeypatch.setattr(m, "RAW_DIR", tmp_path)
    monkeypatch.setattr(m, "PAUSA", 0)
    ce.reiniciar()

    class Resp:
        status_code = 403
        headers: dict = {}
        content = b""

    class Cli:
        n = 0

        async def get(self, url, headers=None, **_k):
            Cli.n += 1
            return Resp()

    async def rodar():
        sem = asyncio.Semaphore(1)
        with pytest.raises(ce.BloqueioSite):
            await m._get(Cli(), sem, m.url_pagina(1))
        with pytest.raises(ce.BloqueioSite):
            await m._get(Cli(), sem, m.url_pagina(2))

    asyncio.run(rodar())
    assert Cli.n == 1  # depois do primeiro bloqueio nenhuma outra requisição
    ce.reiniciar()


def test_sc_julgado_em_so_vale_no_cabecalho():
    from ted_search.collectors.sites import sc_ementarios_ted as m

    corpo = "Ementa: " + "x" * 300 + " (Julgado em 26/07/2002) " + "y" * 50
    assert m.extrair_data(corpo + " Florianópolis, 18 de outubro de 2024. A, Presidente.") == "2024-10-18"
    assert m.extrair_data("Acórdão nº 002/95 - Julgado em 27.10.95\n\nProcesso de Representação nº 192/93") == "1995-10-27"
