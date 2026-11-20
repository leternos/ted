from ted_search.ocr_documentai import dividir_paginas, texto_por_pagina


def test_dividir_paginas_cobre_tudo_sem_sobra():
    assert dividir_paginas(0) == []
    assert dividir_paginas(15) == [(0, 15)]
    assert dividir_paginas(16) == [(0, 15), (15, 16)]
    assert dividir_paginas(55) == [(0, 15), (15, 30), (30, 45), (45, 55)]


def test_texto_por_pagina_recorta_sem_alterar_nada():
    texto = "Ordem dos Advogados do Brasil\nRelatório [sem nome]\nVoto: acórdão\n"
    corte = texto.index("Relatório")
    doc = {
        "text": texto,
        "pages": [
            {"layout": {"textAnchor": {"textSegments": [{"startIndex": "0", "endIndex": str(corte)}]}}},
            {"layout": {"textAnchor": {"textSegments": [{"startIndex": str(corte), "endIndex": str(len(texto))}]}}},
        ],
    }
    paginas = texto_por_pagina(doc)
    assert "".join(paginas) == doc["text"]
    assert paginas[0].startswith("Ordem dos Advogados")
    assert "[sem nome]" in paginas[1]


def test_texto_por_pagina_primeira_pagina_sem_startindex():
    doc = {"text": "abc", "pages": [{"layout": {"textAnchor": {"textSegments": [{"endIndex": "3"}]}}}]}
    assert texto_por_pagina(doc) == ["abc"]
