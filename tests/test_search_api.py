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

        # 4. Inserir julgados do Piloto OAB/SP (Turma Disciplinar + Câmara Recursal)
        from ted_search.collectors.deoab_sp import normalize_deoab_sp_item

        sp_turma = normalize_deoab_sp_item(
            {
                "objeto": {
                    "id": 777111,
                    "edicao": 1839,
                    "titulo": "ACÓRDÃO Nº 14855 - 25.0886.2024.012477-3",
                    "dataDisponibilizacao": "06/04/2026 00:00",
                    "organizacao": {"id": 25, "uf": "SP", "nome": "Conselho Seccional - São Paulo"},
                    "setorPublicacao": {"nomeAlternativo": "Tribunal de Ética e Disciplina"},
                    "tipoPublicacao": {"descricao": "Acórdão"},
                    "conteudoCodificado": (
                        "<p>EMENTA: PRESTAÇÃO DE CONTAS. ART. 34, XXI DO EAOAB.</p>"
                        "<p>ACÓRDÃO: Vistos, relatados e discutidos estes autos, a Quarta Turma Disciplinar "
                        "do Tribunal de Ética e Disciplina da OAB/SP decide julgar procedente a representação.</p>"
                    ),
                }
            }
        )
        sp_recurso = normalize_oab_juris_item(
            {
                "tipoEmenta": "PROCESSO_DISCIPLINAR",
                "numeroProcesso": "25.0886.2023.005602-8",
                "orgaoJulgador": {
                    "idOrganizacao": 25,
                    "nomeOrganizacao": "Conselho Seccional - São Paulo",
                    "nomeSetor": "Oitava Câmara Recursal",
                },
                "numeroEmenta": "28416",
                "descricaoEmenta": "EMENTA: RECURSO ÉTICO-DISCIPLINAR. ART. 34, XXI DO EAOAB. Recurso improvido.",
                "descricaoAcordao": "Vistos, relatados e discutidos estes autos, acordam os membros da Oitava Câmara Recursal em negar provimento.",
                "dataDecisao": "2025-11-27T03:00:00.000+0000",
                "publicacaoDiario": {"_id": "1391378", "edicaoDiario": 1756, "dataPublicacao": "05/12/2025"},
                "relatores": [{"nome": "MARIA CONSELHEIRA SP"}],
                "dispositivosNormativos": [],
                "partes": [],
            },
            "EMENTA",
        )

        with conn:
            _insert_record(conn, sp_turma)
            _insert_record(conn, sp_recurso)
            rebuild_fts(conn)

        res_sp_all = search_julgados(conn, esfera="OABSP", apenas_cfoab=False)
        assert res_sp_all["total"] == 2

        res_sp_turmas = search_julgados(conn, esfera="OABSP_TURMAS", apenas_cfoab=False)
        assert res_sp_turmas["total"] == 1
        assert res_sp_turmas["items"][0]["sigla_orgao"] == "OABSP-4TD"
        assert res_sp_turmas["items"][0]["pdf_url"] == "/api/diario-pdf/777111"

        res_sp_rec = search_julgados(conn, esfera="OABSP_RECURSOS", apenas_cfoab=False)
        assert res_sp_rec["total"] == 1
        assert res_sp_rec["items"][0]["sigla_orgao"] == "OABSP-8CR"
        assert res_sp_rec["items"][0]["pdf_url"] == "/api/diario-pdf/1391378"

