"""Testes de integração da camada SQLite FTS5 e dos endpoints de busca."""

from pathlib import Path

from ted_search.cli import _insert_record
from ted_search.collectors.oab_juris import normalize_oab_juris_item
from ted_search.db import (
    get_connection,
    get_facets,
    get_global_stats,
    init_db,
    rebuild_fts,
    search_julgados,
)


def test_sqlite_fts5_search_and_facets(tmp_path: Path):
    db_file = tmp_path / "test_ted.db"
    init_db(db_file)

    sample_1 = normalize_oab_juris_item(
        {
            "tipoEmenta": "PROCESSO_DISCIPLINAR",
            "numeroProcesso": "24.0000.2023.000001-6",
            "orgaoJulgador": {
                "idOrganizacao": 49,
                "nomeOrganizacao": "Conselho Federal",
                "nomeSetor": "Segunda Câmara",
            },
            "numeroEmenta": "005/2026/SCA",
            "descricaoEmenta": (
                "EMENTA N. 005/2026/SCA. PROCESSO DISCIPLINAR. PRESCRIÇÃO INTERCORRENTE. "
                "Ausência de paralisação por mais de três anos. Infração ao art. 34, inciso XXI do EAOAB. "
                "Pena de suspensão mantida. Recurso improvido."
            ),
            "descricaoAcordao": "Acordam os membros da Segunda Câmara em negar provimento ao recurso.",
            "dataDecisao": "2026-02-03T03:00:00.000+0000",
            "relatores": [{"nome": "CRISTIANE RODRIGUES DE SA"}],
            "dispositivosNormativos": [{"descricao": "EAOAB - Art. 34 inciso XXI"}],
            "partes": [],
        },
        "EMENTA",
    )

    sample_2 = normalize_oab_juris_item(
        {
            "tipoEmenta": "OUTRO",
            "numeroProcesso": "25.0000.2014.001234-5",
            "orgaoJulgador": {
                "idOrganizacao": 49,
                "nomeOrganizacao": "Conselho Federal",
            },
            "numeroEmenta": None,
            "descricaoEmenta": (
                "RECURSO 25.0000.2014.001234-5/SCA-PTU. EMENTA 012/2015/SCA-PTU. "
                "Prestação de contas e locupletamento ilícito. Art. 34, incisos XX e XXI do EAOAB. "
                "Recurso parcialmente provido."
            ),
            "descricaoAcordao": "",
            "dataDecisao": "2015-06-10T03:00:00.000+0000",
            "relatores": [{"nome": "JOAO DA SILVA"}],
            "dispositivosNormativos": [],
            "partes": [],
        },
        "EMENTA",
    )

    with get_connection(db_file) as conn:
        with conn:
            _insert_record(conn, sample_1)
            _insert_record(conn, sample_2)
            rebuild_fts(conn)

        # 1. Busca sem acento ("prescricao intercorrente" deve encontrar "PRESCRIÇÃO INTERCORRENTE")
        res = search_julgados(conn, q="prescricao intercorrente", mode="todos")
        assert res["total"] == 1
        assert res["items"][0]["numero_ementa"] == "005/2026/SCA"
        assert "<mark>" in res["items"][0]["ementa_highlighted"]

        # 2. Busca filtrando por órgão normalizado do legado (1ª Turma da 2ª Câmara)
        res_ptu = search_julgados(conn, orgao="1ª Turma da 2ª Câmara")
        assert res_ptu["total"] == 1
        assert res_ptu["items"][0]["uf_origem"] == "SP"
        assert res_ptu["items"][0]["resultado_julgamento"] == "Parcialmente Provido"

        # 3. Verificar facetas e estatísticas
        facets = get_facets(conn)
        assert len(facets["orgaos"]) == 2
        assert any(d["valor"] == "EAOAB - Art. 34 inciso XXI" and d["total"] == 2 for d in facets["dispositivos"])

        stats = get_global_stats(conn)
        assert stats["total_cfoab"] == 2
        assert stats["total_ted_disciplinar"] == 2
