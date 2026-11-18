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
    # Nova coleta, incompleta, com uma única decisão: o ano é preservado
    _escrever_bruto(raw, 2024, [_pub(301, "2024-03-05")], skips=[])
    rel = varredura.carregar_seccional("AC", anos=[2024], db_path=db, raw_dir=raw)
    assert rel["anos_preservados"] == [{"ano": 2024, "havia": 2, "coletados": 1}]
    with get_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM julgados WHERE fonte = 'DEOAB_AC'").fetchone()[0] == 2


def test_resumo_aceita_o_formato_antigo_da_coleta_de_sp(tmp_path):
    out = varredura.raw_dir_uf("SP", tmp_path)
    out.mkdir(parents=True)
    (out / "2023.index.json").write_text(json.dumps({"2023-02-01": [1, 2], "2023-02-02": [3]}), encoding="utf-8")
    (out / "2023.summary.json").write_text(json.dumps({"esperado_por_termo": {"ACORDAO": 3}, "paginas_com_falha": [], "publicacoes_com_falha": []}), encoding="utf-8")
    (out / "2023.jsonl").write_text('{"id": 1}\n{"id": 2}\n{"id": 3, "_skip": "Edital"}\n', encoding="utf-8")
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
