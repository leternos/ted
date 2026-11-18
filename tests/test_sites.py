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
