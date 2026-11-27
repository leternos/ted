from ted_search.ocr_lote import agrupar_linhas


def _o(texto, x, y, h=0.02):
    return {"texto": texto, "x": x, "y": y, "w": 0.1, "h": h}


def test_agrupar_linhas_ordena_de_cima_para_baixo_e_da_esquerda_para_a_direita():
    obs = [
        _o("Voto", 0.1, 0.50),
        _o("Ordem dos Advogados", 0.10, 0.90),
        _o("Brasil", 0.45, 0.90),  # mesma linha, mais à direita
        _o("do", 0.30, 0.90),
        _o("Relatório", 0.1, 0.70),
    ]
    assert agrupar_linhas(obs) == "Ordem dos Advogados do Brasil\nRelatório\nVoto"


def test_agrupar_linhas_nao_altera_o_texto_nem_remove_nada():
    obs = [_o("[tarja] 123.456-7", 0.1, 0.5), _o("R$ 1.234,56", 0.5, 0.5)]
    assert agrupar_linhas(obs) == "[tarja] 123.456-7 R$ 1.234,56"


def test_agrupar_linhas_vazio():
    assert agrupar_linhas([]) == ""
