import json
import sqlite3

from ted_search import inteiro_teor as it
from ted_search.db import SCHEMA_SQL


def test_chave_ementa_e_igual_nos_dois_portais():
    assert it.chave_ementa("Ementa 001/2024/SCA-PTU ", "PTU") == "1/2024/PTU"
    assert it.chave_ementa("1/2024/SCA-PTU", "SCA-PTU") == "1/2024/PTU"
    assert it.chave_ementa("Ementa 014/2017/SCA", "SCA") == "14/2017/SCA"
    assert it.chave_ementa("064/2026/OEP", "Órgão Especial") == "64/2026/OEP"
    assert it.chave_ementa(None) is None


def test_recorte_do_diario_nao_e_inteiro_teor():
    acordao = ("EMENTA: Representação. Locupletamento. Procedência.\n\nACÓRDÃO\nVistos, relatados e discutidos, acordam os membros "
               "da Turma, por unanimidade, nos termos do relatório e voto do relator, que integram o presente.\n") * 30
    assert not it.texto_tem_relatorio_e_voto(acordao)


def test_texto_com_secoes_de_relatorio_e_voto_e_inteiro_teor():
    texto = "EMENTA: x\n\nRELATÓRIO\n\n" + "Trata-se de representação. " * 80 + "\n\nÉ o relatório.\n\nVOTO\n\n" + "Conheço do recurso. " * 80
    assert it.texto_tem_relatorio_e_voto(texto)
    parecer = "RELATÓRIO\n" + "Consulta formulada. " * 100 + "\nPARECER\n" + "A Turma responde. " * 100
    assert it.texto_tem_relatorio_e_voto(parecer)


def _banco():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    it.garantir_esquema(conn)
    base = dict(contexto="EMENTA", tem_inteiro_teor=1)  # como os normalizadores antigos gravam
    linhas = [
        ("EMENTA:cf", "OAB_JURIS", "25.0000.2022.000120-0", "ementa do recurso"),
        ("EMENTA:embargos", "OAB_JURIS", "25.0000.2022.000120-0", "embargos no mesmo processo"),
        ("DEOAB_SP:1", "DEOAB_SP", "25.0000.2022.000120-0", "acórdão da câmara recursal"),
        ("DEOAB_SP:2", "DEOAB_SP", "25.0000.2021.999999-9", "outro processo"),
    ]
    for jid, fonte, proc, texto in linhas:
        conn.execute(
            "INSERT INTO julgados (id, fonte, contexto, numero_processo, inteiro_teor_texto, pdf_url, tem_inteiro_teor) VALUES (?,?,?,?,?,?,?)",
            (jid, fonte, base["contexto"], proc, texto, "/api/diario-pdf/1", base["tem_inteiro_teor"]),
        )
    conn.execute(
        "INSERT INTO inteiro_teor_pdfs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ETICA_PDF:1", "PORTAL_ETICA_CFOAB", "Ementa 001/2025/SCA", "1/2025/SCA", "SCA", 2025, "25.0000.2022.000120-0",
         "pdf_e_ementa", "https://exemplo/ementa_001.pdf", 9, "relatorio_e_voto", json.dumps(["EMENTA:cf"])),
    )
    return conn


def test_selo_so_para_quem_tem_inteiro_teor():
    conn = _banco()
    n = it.recalcular_selos(conn)
    linhas = {r[0]: r[1:] for r in conn.execute("SELECT id, tem_inteiro_teor, inteiro_teor_relacionado, inteiro_teor_pdfs_json FROM julgados")}
    assert linhas["EMENTA:cf"][:2] == (1, 0)
    assert json.loads(linhas["EMENTA:cf"][2])[0]["relacao"] == "proprio"
    # outra decisão do mesmo processo: não ganha o selo, mas aponta para o inteiro teor que existe
    assert linhas["EMENTA:embargos"][:2] == (0, 1)
    assert linhas["DEOAB_SP:1"][:2] == (0, 1)
    assert json.loads(linhas["DEOAB_SP:1"][2])[0]["relacao"] == "mesmo_processo"
    # só a página do Diário: sem selo nenhum
    assert linhas["DEOAB_SP:2"] == (0, 0, None)
    assert (n["proprio_pdf"], n["relacionado"]) == (1, 2)


def test_recalcular_e_idempotente():
    conn = _banco()
    a = it.recalcular_selos(conn)
    b = it.recalcular_selos(conn)
    assert a == b
