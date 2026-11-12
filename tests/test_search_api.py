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
        assert any("art. 43" in s for s in res.get("sinonimos_sugeridos", []))

        # 1.1 Teste do Thesaurus disciplinar para Tergiversação
        from ted_search.db import get_thesaurus_suggestions
        sugs_tergiversacao = get_thesaurus_suggestions("tergiversação")
        assert "patrocínio simultâneo" in sugs_tergiversacao
        assert "patrocínio infiel" in sugs_tergiversacao
        assert "art. 355" in sugs_tergiversacao

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

        # 5. Escopo nacional generalizado: SECCIONAIS + UF + Instância
        from ted_search.db import get_seccionais_status

        # Busca por todas as Seccionais
        res_secc = search_julgados(conn, esfera="SECCIONAIS")
        assert res_secc["total"] == 2

        # Busca por Seccional específica (SP)
        res_sp_sec = search_julgados(conn, esfera="SECCIONAIS", seccional="SP")
        assert res_sp_sec["total"] == 2

        # Filtro de 1ª instância (TED / Turmas Disciplinares)
        res_ted = search_julgados(conn, esfera="SECCIONAIS", seccional="SP", instancia="TED")
        assert res_ted["total"] == 1
        assert res_ted["items"][0]["sigla_orgao"] == "OABSP-4TD"

        # Filtro de 2ª instância (Câmaras Recursais)
        res_cr = search_julgados(conn, esfera="SECCIONAIS", seccional="SP", instancia="RECURSAL")
        assert res_cr["total"] == 1
        assert res_cr["items"][0]["sigla_orgao"] == "OABSP-8CR"

        # Facetas nacionais e instâncias
        facets_national = get_facets(conn, esfera="TODAS")
        assert facets_national["instancias"]["cfoab"] == 2
        assert facets_national["instancias"]["seccionais"] == 2
        assert facets_national["instancias"]["ted"] == 1
        assert facets_national["instancias"]["recursal"] == 1

        # Status das 27 Seccionais
        status_seccionais = get_seccionais_status(conn)
        assert len(status_seccionais) == 27
        sp_status = next(s for s in status_seccionais if s["uf"] == "SP")
        assert sp_status["status"] == "ativa"
        assert sp_status["total"] == 2
        assert sp_status["ted"] == 1
        assert sp_status["recursal"] == 1


def test_fastapi_endpoints_national_scope(tmp_path: Path):
    from starlette.testclient import TestClient
    import ted_search.api as api_mod
    import ted_search.config as cfg_mod
    from ted_search.api import app
    from ted_search.cli import _insert_record
    from ted_search.collectors.deoab_sp import normalize_deoab_sp_item
    from ted_search.collectors.oab_juris import normalize_oab_juris_item

    test_db = tmp_path / "api_test.db"
    init_db(test_db)

    # Inserir 1 CFOAB e 1 Seccional SP
    cfoab_rec = normalize_oab_juris_item(
        {
            "tipoEmenta": "PROCESSO_DISCIPLINAR",
            "numeroProcesso": "24.0000.2023.000001-6",
            "orgaoJulgador": {"idOrganizacao": 49, "nomeOrganizacao": "Conselho Federal"},
            "numeroEmenta": "100/2026/SCA",
            "descricaoEmenta": "EMENTA TESTE CFOAB.",
            "dataDecisao": "2026-01-01T00:00:00.000Z",
            "relatores": [],
            "dispositivosNormativos": [],
            "partes": [],
        },
        "EMENTA",
    )
    sp_rec = normalize_deoab_sp_item(
        {
            "objeto": {
                "id": 888222,
                "edicao": 1900,
                "titulo": "ACÓRDÃO Nº 999 - 25.0886.2024.999999-0",
                "dataDisponibilizacao": "10/04/2026 00:00",
                "organizacao": {"id": 25, "uf": "SP", "nome": "Conselho Seccional - São Paulo"},
                "setorPublicacao": {"nomeAlternativo": "Tribunal de Ética e Disciplina"},
                "tipoPublicacao": {"descricao": "Acórdão"},
                "conteudoCodificado": (
                    "<p>EMENTA: PRESTAÇÃO DE CONTAS. ART. 34, XXI DO EAOAB. TESTE NACIONAL.</p>"
                    "<p>ACÓRDÃO: Vistos, relatados e discutidos estes autos, a Primeira Turma Disciplinar "
                    "do Tribunal de Ética e Disciplina da OAB/SP decide julgar procedente a representação.</p>"
                ),
            }
        }
    )

    with get_connection(test_db) as conn:
        with conn:
            _insert_record(conn, cfoab_rec)
            _insert_record(conn, sp_rec)
            rebuild_fts(conn)

    orig_cfg_db = cfg_mod.DB_PATH
    orig_api_db = api_mod.DB_PATH
    cfg_mod.DB_PATH = test_db
    api_mod.DB_PATH = test_db
    try:
        client = TestClient(app)

        # GET /health (público)
        health_resp = client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok", "version": "0.3.0"}

        # Tentativa de acesso sem autenticação -> 401
        unauth_resp = client.get("/api/stats")
        assert unauth_resp.status_code == 401
        assert "Acesso restrito" in unauth_resp.json()["detail"]

        # Status de autenticação inicial -> authenticated: false
        status_resp = client.get("/api/auth/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["authenticated"] is False

        # Tentativa de login com senha errada -> 401
        bad_login = client.post("/api/auth/login", json={"password": "wrongpassword"})
        assert bad_login.status_code == 401

        # Login com a senha correta (609099) -> 200 e recebe cookie
        login_resp = client.post("/api/auth/login", json={"password": "609099"})
        assert login_resp.status_code == 200
        assert login_resp.json()["authenticated"] is True
        token = login_resp.json()["token"]
        assert token

        # GET /api/stats (agora autenticado via cookie de sessão)
        stats_resp = client.get("/api/stats")
        assert stats_resp.status_code == 200
        stats = stats_resp.json()
        assert stats["total_geral"] == 2
        assert stats["total_cfoab"] == 1
        assert stats["total_seccionais"] == 1
        assert stats["seccionais_ativas"] == 1

        # GET /api/seccionais
        secc_resp = client.get("/api/seccionais")
        assert secc_resp.status_code == 200
        seccionais = secc_resp.json()
        assert len(seccionais) == 27
        sp_item = next(s for s in seccionais if s["uf"] == "SP")
        assert sp_item["status"] == "ativa"
        assert sp_item["total"] == 1

        # GET /api/search com escopo nacional
        search_resp = client.get("/api/search?esfera=SECCIONAIS&seccional=SP&instancia=TED")
        assert search_resp.status_code == 200
        data = search_resp.json()
        assert data["total"] == 1
        assert data["items"][0]["sigla_orgao"] == "OABSP-1TD"

        # GET /api/facets com instâncias
        facets_resp = client.get("/api/facets?esfera=TODAS")
        assert facets_resp.status_code == 200
        facets_data = facets_resp.json()
        assert facets_data["instancias"]["cfoab"] == 1
        assert facets_data["instancias"]["seccionais"] == 1
        assert facets_data["instancias"]["ted"] == 1

        # POST /api/auth/logout -> encerra sessão
        logout_resp = client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json()["authenticated"] is False

        # Nova requisição sem cookie -> 401
        after_logout = client.get("/api/stats")
        assert after_logout.status_code == 401
    finally:
        cfg_mod.DB_PATH = orig_cfg_db
        api_mod.DB_PATH = orig_api_db



