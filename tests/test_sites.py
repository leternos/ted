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
