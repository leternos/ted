"""CLI oficial do projeto ted-search para Backfill, Enriquecimento, Extração de PDFs e Estatísticas."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from ted_search.collectors.deoab_sp import (
    DEOAB_SP_YEARS,
    fetch_deoab_sp_pilot,
    load_deoab_sp_raw,
    normalize_deoab_sp_item,
)
from ted_search.collectors.etica_strapi import (
    build_canonical_ementa_key,
    fetch_etica_strapi_all,
    normalize_etica_strapi_item,
    sync_pdfs_inteiro_teor,
)
from ted_search.collectors.oab_juris import (
    fetch_context_all_pages,
    normalize_oab_juris_item,
)
from ted_search.config import DB_PATH, OAB_JURIS_CONTEXTS, RAW_DIR
from ted_search.db import get_connection, get_global_stats, init_db, rebuild_fts
from ted_search.parser import detect_orgao_julgador_oabsp
from ted_search import fontes as fontes_site
from ted_search import varredura


def _insert_record(conn, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO julgados (
            id, fonte, contexto, tipo_ementa, numero_processo, numero_ementa,
            ano_ementa, id_organizacao, nome_organizacao, orgao_julgador_raw,
            orgao_julgador_norm, sigla_orgao, uf_origem, relator_principal,
            relatores_json, partes_json, dispositivos_json, dispositivos_texto,
            sancao_identificada, resultado_julgamento, descricao_ementa,
            descricao_acordao, inteiro_teor_texto, pdf_url, diario_url,
            publicacao_str, data_decisao, is_ted_disciplinar, tem_inteiro_teor
        ) VALUES (
            :id, :fonte, :contexto, :tipo_ementa, :numero_processo, :numero_ementa,
            :ano_ementa, :id_organizacao, :nome_organizacao, :orgao_julgador_raw,
            :orgao_julgador_norm, :sigla_orgao, :uf_origem, :relator_principal,
            :relatores_json, :partes_json, :dispositivos_json, :dispositivos_texto,
            :sancao_identificada, :resultado_julgamento, :descricao_ementa,
            :descricao_acordao, :inteiro_teor_texto, :pdf_url, :diario_url,
            :publicacao_str, :data_decisao, :is_ted_disciplinar, :tem_inteiro_teor
        )
        """,
        rec,
    )
    dispositivos = json.loads(rec.get("dispositivos_json") or "[]")
    for disp in dispositivos:
        conn.execute(
            "INSERT OR IGNORE INTO julgado_dispositivos (julgado_id, dispositivo) VALUES (?, ?)",
            (rec["id"], disp),
        )


async def run_backfill(
    use_cache: bool = True,
    sync_pdfs_limit: int | None = 150,
    sp_pages_per_year: int | None = None,
    concurrency: int = 15,
) -> None:
    t0 = time.perf_counter()
    init_db(DB_PATH)

    # 1. Coletar todos os contextos do OAB Jurisprudência
    raw_by_ctx: dict[str, list[dict]] = {}
    for ctx in OAB_JURIS_CONTEXTS:
        raw_by_ctx[ctx] = await fetch_context_all_pages(ctx, page_size=1000, use_cache=use_cache)

    # 2. Coletar os 1.402 acórdãos com PDF do Portal Ética e Disciplina (Strapi CFOAB)
    strapi_raw = await fetch_etica_strapi_all(use_cache=use_cache)

    # 3. Coletar Piloto Seccional OAB/SP (Turmas Disciplinares, Deontológica e Câmaras Recursais via DEOAB)
    deoab_sp_raw = await fetch_deoab_sp_pilot(
        pages_per_year=sp_pages_per_year,
        concurrency=min(concurrency, 14),
        use_cache=use_cache,
    )

    # 4. Normalizar, enriquecer e reconciliar registros no SQLite
    print("[pipeline] Normalizando, aplicando Parser Jurídico (CFOAB + Piloto OAB/SP) e reconciliando bases...")
    canonical_index: dict[str, str] = {}  # canonical_ementa_key -> julgado_id
    diario_url_index: dict[str, str] = {}  # diario_url -> julgado_id

    with get_connection(DB_PATH) as conn:
        # Preservar inteiro_teor_texto já extraído de PDFs externos anteriormente
        existing_texts = {
            r["id"]: (r["inteiro_teor_texto"], r["pdf_url"])
            for r in conn.execute(
                "SELECT id, inteiro_teor_texto, pdf_url FROM julgados WHERE tem_inteiro_teor = 1 AND id NOT LIKE 'DEOAB_SP:%'"
            ).fetchall()
        }

        conn.execute("DELETE FROM julgado_dispositivos;")

        with conn:
            for ctx, items in raw_by_ctx.items():
                for raw_item in items:
                    rec = normalize_oab_juris_item(raw_item, ctx)
                    if rec["id"] in existing_texts and not rec["inteiro_teor_texto"]:
                        prev_txt, prev_pdf = existing_texts[rec["id"]]
                        rec["inteiro_teor_texto"] = prev_txt
                        rec["tem_inteiro_teor"] = 1
                        if not rec["pdf_url"]:
                            rec["pdf_url"] = prev_pdf
                    _insert_record(conn, rec)
                    ckey = build_canonical_ementa_key(rec["numero_ementa"], rec["sigla_orgao"])
                    if ckey:
                        canonical_index[ckey] = rec["id"]
                    if rec.get("diario_url"):
                        diario_url_index[rec["diario_url"]] = rec["id"]

            reconciled_count = 0
            inserted_strapi_count = 0
            for s_item in strapi_raw:
                s_rec = normalize_etica_strapi_item(s_item)
                ckey = build_canonical_ementa_key(s_rec["numero_ementa"], s_rec["sigla_orgao"])
                ckey_short = build_canonical_ementa_key(s_rec["numero_ementa"], None)

                matched_id = canonical_index.get(ckey or "") or canonical_index.get(ckey_short or "")
                if matched_id and s_rec["pdf_url"]:
                    conn.execute(
                        """
                        UPDATE julgados
                        SET pdf_url = ?,
                            fonte = 'AMBOS',
                            is_ted_disciplinar = 1,
                            sigla_orgao = CASE WHEN sigla_orgao = 'CFOAB' THEN ? ELSE sigla_orgao END,
                            orgao_julgador_norm = CASE WHEN sigla_orgao = 'CFOAB' THEN ? ELSE orgao_julgador_norm END
                        WHERE id = ?
                        """,
                        (s_rec["pdf_url"], s_rec["sigla_orgao"], s_rec["orgao_julgador_norm"], matched_id),
                    )
                    reconciled_count += 1
                else:
                    if s_rec["id"] in existing_texts:
                        prev_txt, _ = existing_texts[s_rec["id"]]
                        s_rec["inteiro_teor_texto"] = prev_txt
                        s_rec["tem_inteiro_teor"] = 1
                    _insert_record(conn, s_rec)
                    inserted_strapi_count += 1

            # Inserir Acórdãos do Piloto OAB/SP (Turmas do TED e Câmaras Recursais via DEOAB)
            inserted_sp_deoab = 0
            for sp_pub in deoab_sp_raw:
                sp_rec = normalize_deoab_sp_item(sp_pub)
                if not sp_rec:
                    continue
                if sp_rec["diario_url"] in diario_url_index:
                    # Já existe via OAB_JURIS (ex: Câmara Recursal), apenas garante inteiro_teor_texto e pdf_url
                    existing_id = diario_url_index[sp_rec["diario_url"]]
                    conn.execute(
                        """
                        UPDATE julgados
                        SET inteiro_teor_texto = COALESCE(inteiro_teor_texto, ?),
                            tem_inteiro_teor = 1,
                            pdf_url = COALESCE(pdf_url, ?)
                        WHERE id = ?
                        """,
                        (sp_rec["inteiro_teor_texto"], sp_rec["pdf_url"], existing_id),
                    )
                else:
                    _insert_record(conn, sp_rec)
                    inserted_sp_deoab += 1

        print(
            f"[pipeline] Reconciliação concluída: {reconciled_count} ementas CFOAB vinculadas a PDFs, "
            f"{inserted_strapi_count} acórdãos exclusivos Strapi, "
            f"{inserted_sp_deoab} acórdãos de Turmas/Câmaras OAB/SP adicionados do DEOAB."
        )

        # 5. Baixar e extrair PDFs externos de inteiro teor se solicitado
        if sync_pdfs_limit is None or sync_pdfs_limit > 0:
            await sync_pdfs_inteiro_teor(conn, limit=sync_pdfs_limit, concurrency=concurrency)

        # 6. Reconstruir índice FTS5 BM25
        print("[pipeline] Construindo índice Full-Text Search (SQLite FTS5 unicode61)...")
        with conn:
            rebuild_fts(conn)

        stats = get_global_stats(conn)

    elapsed = time.perf_counter() - t0
    print(f"\n=== BACKFILL CONCLUÍDO EM {elapsed:.1f}s ===")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


async def run_backfill_sp(
    years: list[int] | None = None,
    pages_per_year: int | None = None,
    concurrency: int = 12,
    use_cache: bool = True,
    collect: bool = True,
) -> dict:
    """Backfill completo e isolado da OAB/SP no DEOAB: só toca registros de São Paulo.

    1. Coleta (ou relê do cache) todos os acórdãos de SP dos anos pedidos;
    2. Substitui os registros DEOAB_SP desses anos pelos recém-normalizados;
    3. Reclassifica o órgão julgador dos registros OAB_JURIS da Seccional SP com o parser atual;
    4. Reconstrói o índice FTS5.
    """
    t0 = time.perf_counter()
    target_years = sorted(years or DEOAB_SP_YEARS)
    init_db(DB_PATH)

    if collect:
        pubs = await fetch_deoab_sp_pilot(
            pages_per_year=pages_per_year, years=target_years, concurrency=concurrency, use_cache=use_cache
        )
    else:
        pubs = load_deoab_sp_raw(RAW_DIR, target_years)
    if not pubs:
        raise SystemExit("[backfill-sp] Nenhum acórdão coletado; banco não foi alterado.")

    recs = [r for r in (normalize_deoab_sp_item(p) for p in pubs) if r]
    report = {"anos": target_years, "publicacoes_coletadas": len(pubs), "normalizadas": len(recs)}

    with get_connection(DB_PATH) as conn:
        with conn:
            juris_by_diario = {
                r["diario_url"]: r["id"]
                for r in conn.execute(
                    "SELECT id, diario_url FROM julgados WHERE fonte != 'DEOAB_SP' AND id_organizacao = 25 AND diario_url IS NOT NULL"
                )
            }
            marks = ",".join("?" * len(target_years))
            conn.execute(
                f"DELETE FROM julgado_dispositivos WHERE julgado_id IN "
                f"(SELECT id FROM julgados WHERE fonte = 'DEOAB_SP' AND ano_ementa IN ({marks}))",
                target_years,
            )
            removed = conn.execute(
                f"DELETE FROM julgados WHERE fonte = 'DEOAB_SP' AND ano_ementa IN ({marks})", target_years
            ).rowcount

            inserted = merged = 0
            for rec in recs:
                existing_id = juris_by_diario.get(rec["diario_url"])
                if existing_id:
                    # Mesma publicação já veio do OAB Jurisprudência: mantém o registro estruturado e garante o teor
                    conn.execute(
                        "UPDATE julgados SET inteiro_teor_texto = COALESCE(NULLIF(inteiro_teor_texto, ''), ?), "
                        "tem_inteiro_teor = 1, pdf_url = COALESCE(pdf_url, ?) WHERE id = ?",
                        (rec["inteiro_teor_texto"], rec["pdf_url"], existing_id),
                    )
                    merged += 1
                else:
                    _insert_record(conn, rec)
                    inserted += 1

            relabeled = 0
            rows = conn.execute(
                "SELECT id, orgao_julgador_raw, numero_ementa, descricao_ementa, descricao_acordao, sigla_orgao "
                "FROM julgados WHERE fonte != 'DEOAB_SP' AND id_organizacao = 25"
            ).fetchall()
            for r in rows:
                raw = r["orgao_julgador_raw"] or ""
                setor = raw.split(" / ", 1)[1] if " / " in raw else ""
                text = f"{r['descricao_ementa'] or ''}\n{r['descricao_acordao'] or ''}"
                sigla, norm = detect_orgao_julgador_oabsp(setor, r["numero_ementa"] or "", text)
                if sigla != r["sigla_orgao"]:
                    conn.execute(
                        "UPDATE julgados SET sigla_orgao = ?, orgao_julgador_norm = ? WHERE id = ?", (sigla, norm, r["id"])
                    )
                    relabeled += 1

            print("[backfill-sp] Reconstruindo índice FTS5...")
            rebuild_fts(conn)
        conn.execute("VACUUM")

    report.update(
        {
            "deoab_sp_removidos": removed,
            "deoab_sp_inseridos": inserted,
            "fundidos_com_oab_juris": merged,
            "oab_juris_sp_reclassificados": relabeled,
            "segundos": round(time.perf_counter() - t0, 1),
        }
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI do Buscador de Jurisprudências CFOAB & Seccionais (Piloto OAB/SP)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bf = sub.add_parser("backfill", help="Executa o backfill das APIs da OAB (CFOAB + Piloto OAB/SP), enriquece e indexa FTS5")
    p_bf.add_argument("--no-cache", action="store_true", help="Força novo download das APIs ignorando data/raw/")
    p_bf.add_argument(
        "--pdf-limit",
        type=int,
        default=0,
        help="Quantidade adicional de PDFs externos do CFOAB para baixar nesta execução",
    )
    p_bf.add_argument(
        "--sp-pages-per-year",
        type=int,
        default=0,
        help="Amostra da OAB/SP no DEOAB: 1ª página de N dias espaçados por ano (0 = coleta completa)",
    )
    p_bf.add_argument("--concurrency", type=int, default=15, help="Concorrência nas requisições assíncronas")

    p_pdf = sub.add_parser("sync-pdfs", help="Baixa e extrai o texto integral dos PDFs de acórdãos")
    p_pdf.add_argument("--limit", type=int, default=0, help="Limite de PDFs (0 = todos os pendentes)")
    p_pdf.add_argument("--concurrency", type=int, default=12, help="Concorrência de download")

    p_sp = sub.add_parser("backfill-sp", help="Backfill completo só da OAB/SP no DEOAB (retomável; não toca CFOAB nem outras Seccionais)")
    p_sp.add_argument("--years", type=str, default="", help="Anos separados por vírgula (padrão: 2019 em diante)")
    p_sp.add_argument("--pages-per-year", type=int, default=0, help="Amostra: 1ª página de N dias espaçados por ano (0 = coleta completa)")
    p_sp.add_argument("--concurrency", type=int, default=12, help="Concorrência nas requisições ao DEOAB")
    p_sp.add_argument("--no-cache", action="store_true", help="Descarta data/raw/deoab_sp/ e baixa tudo de novo")
    p_sp.add_argument("--no-collect", action="store_true", help="Não acessa a rede: regrava o banco a partir de data/raw/deoab_sp/")

    def _uf_parser(nome: str, ajuda: str) -> argparse.ArgumentParser:
        sp_ = sub.add_parser(nome, help=ajuda)
        sp_.add_argument("--uf", required=True, help="UF da Seccional (ex.: RS) ou lista separada por vírgula")
        sp_.add_argument("--concurrency", type=int, default=10, help="Requisições simultâneas ao DEOAB (o servidor é um só: não some coletas em paralelo)")
        return sp_

    p_perfil = _uf_parser("sec-perfil", "Varredura 1/4: censo de dias-amostra -> proposta de perfil em data/perfis/UF.json")
    p_perfil.add_argument("--dias", type=int, default=6, help="Dias-amostra do censo")
    p_coletar = _uf_parser("sec-coletar", "Varredura 2/4: índice completo + inspeção das publicações (retomável)")
    p_coletar.add_argument("--years", type=str, default="", help="Anos separados por vírgula (padrão: 2019 em diante)")
    p_carregar = _uf_parser("sec-carregar", "Varredura 3/4: normaliza e grava no banco, atualizando o livro-razão")
    p_carregar.add_argument("--years", type=str, default="", help="Anos separados por vírgula (padrão: todos os coletados)")
    p_carregar.add_argument("--no-fts", action="store_true", help="Não reconstrói o FTS (use ao carregar várias UFs e rode 'cobertura --fts' no fim)")
    p_auditar = _uf_parser("sec-auditar", "Varredura 4/4: censo independente de dias sorteados contra a coleta e o banco")
    p_auditar.add_argument("--dias", type=int, default=4, help="Dias sorteados para o censo")
    p_auditar.add_argument("--seed", type=int, default=None, help="Semente do sorteio (para repetir uma auditoria)")
    p_cob = sub.add_parser("cobertura", help="Livro-razão da varredura: situação de cada Seccional")
    p_cob.add_argument("--sync", type=str, default="", help="Recalcula o livro-razão dessas UFs a partir dos arquivos brutos e do banco")
    p_cob.add_argument("--fts", action="store_true", help="Reconstrói o índice FTS5 e compacta o banco (VACUUM)")
    p_cob.add_argument("--json", action="store_true", help="Saída em JSON")

    p_fontes = sub.add_parser("fontes", help="Bases próprias nos sites das Seccionais: registro (data/fontes/<UF>.json) e situação")
    p_fontes.add_argument("--uf", type=str, default="", help="Filtra por UF")
    p_fontes.add_argument("--json", action="store_true", help="Saída em JSON")
    for nome, ajuda in (("site-coletar", "Coleta uma base de site com o adaptador em collectors/sites/ (retomável)"),
                        ("site-carregar", "Carrega no banco uma base de site já coletada e fecha a linha no livro-razão")):
        p_site = sub.add_parser(nome, help=ajuda)
        p_site.add_argument("--uf", required=True)
        p_site.add_argument("--fonte", required=True, help="slug da base em data/fontes/<UF>.json")
        p_site.add_argument("--concurrency", type=int, default=6)
        p_site.add_argument("--no-fts", action="store_true")

    p_it = sub.add_parser(
        "inteiro-teor",
        help="Atualiza o catálogo de PDFs de inteiro teor (relatório + voto) e recalcula o selo de todos os julgados",
    )
    p_it.add_argument("--recoletar", action="store_true", help="Baixa de novo as listagens dos dois portais do Conselho Federal antes")

    sub.add_parser("stats", help="Exibe estatísticas completas do banco de dados e enriquecimento")

    p_sec = sub.add_parser("collect-seccionais", help="Coleta acórdãos e ementas do DEOAB de Seccionais estaduais")
    p_sec.add_argument("--ufs", type=str, default="RS,PR,BA,MG,AC,SC,GO,PE", help="UFs a coletar separadas por vírgula (ou 'ALL')")
    p_sec.add_argument("--years", type=str, default="2024,2025,2026", help="Anos a coletar separados por vírgula")
    p_sec.add_argument("--pages", type=int, default=3, help="Páginas por ano/termo")
    p_sec.add_argument("--concurrency", type=int, default=15, help="Concorrência assíncrona")

    args = parser.parse_args()
    if args.cmd == "backfill":
        pdf_lim = None if args.pdf_limit < 0 else args.pdf_limit
        asyncio.run(
            run_backfill(
                use_cache=not args.no_cache,
                sync_pdfs_limit=pdf_lim,
                sp_pages_per_year=args.sp_pages_per_year or None,
                concurrency=args.concurrency,
            )
        )
    elif args.cmd in ("sec-perfil", "sec-coletar", "sec-carregar", "sec-auditar"):
        anos = [int(y) for y in getattr(args, "years", "").split(",") if y.strip()] or None
        for uf in [u.strip().upper() for u in args.uf.split(",") if u.strip()]:
            if args.cmd == "sec-perfil":
                perfil = asyncio.run(varredura.perfilar_seccional(uf, n_dias=args.dias, concurrency=args.concurrency))
                ev = perfil["evidencia"]
                print(json.dumps({"uf": uf, "termos": perfil["termos"], "tipos_decisao": perfil["tipos_decisao"], "recall_por_termo": ev["recall_por_termo"],
                                  "decisoes_no_censo": ev["decisoes_no_censo"], "sem_termo": len(ev["decisoes_que_nenhum_termo_encontra"]),
                                  "tipos_vistos": {k: [v["publicacoes"], v["parecem_decisao"]] for k, v in ev["tipos"].items()}}, ensure_ascii=False, indent=2))
            elif args.cmd == "sec-coletar":
                if uf == "SP":
                    asyncio.run(fetch_deoab_sp_pilot(years=anos, concurrency=args.concurrency))
                else:
                    asyncio.run(varredura.coletar_seccional(uf, anos=anos, concurrency=args.concurrency))
            elif args.cmd == "sec-carregar":
                if uf == "SP":
                    asyncio.run(run_backfill_sp(years=anos, collect=False))
                    varredura.registrar_rejeitados("SP")
                    varredura.sincronizar_cobertura("SP")
                else:
                    print(json.dumps(varredura.carregar_seccional(uf, anos=anos, rebuild=not args.no_fts), ensure_ascii=False, indent=2))
            else:
                aud = asyncio.run(varredura.auditar_seccional(uf, n_dias=args.dias, concurrency=args.concurrency, seed=args.seed))
                print(json.dumps(aud, ensure_ascii=False, indent=2))
    elif args.cmd == "cobertura":
        for uf in [u.strip().upper() for u in args.sync.split(",") if u.strip()]:
            if uf == "SP":
                varredura.registrar_rejeitados("SP")
            varredura.sincronizar_cobertura(uf)
        if args.fts:
            with get_connection(DB_PATH) as conn:
                with conn:
                    rebuild_fts(conn)
                # Sem VACUUM as páginas livres guardam o índice antigo e o banco compactado quase dobra de tamanho
                conn.execute("VACUUM")
        linhas = varredura.relatorio_cobertura()
        if args.json:
            print(json.dumps(linhas, ensure_ascii=False, indent=2))
        else:
            print(f"{'UF':3} {'situação':17} {'perfil':7} {'anos':>4} {'ok':>3} {'parc':>4} {'decisões':>9} {'no banco':>9} {'auditada':>8} {'faltantes':>9}")
            for l in linhas:
                print(f"{l['uf']:3} {l['situacao']:17} {'sim' if l['perfil_validado'] else 'não':7} {l['anos_no_livro']:>4} {l['completos']:>3} {l['parciais']:>4} "
                      f"{l['decisoes_coletadas']:>9} {l['no_banco']:>9} {'sim' if l['auditada'] else 'não':>8} {l['faltantes_na_auditoria']:>9}")
    elif args.cmd == "fontes":
        linhas = [l for l in fontes_site.relatorio_fontes() if not args.uf or l["uf"] == args.uf.upper()]
        if args.json:
            print(json.dumps(linhas, ensure_ascii=False, indent=2))
        else:
            print(f"{'UF':3} {'base':24} {'situação':11} {'excl.':5} {'livro':9} {'no banco':>8}  conteúdo / período")
            for l in linhas:
                excl = {True: "sim", False: "não"}.get(l["exclusiva"], "?")
                print(f"{l['uf']:3} {l['slug'][:24]:24} {l['situacao']:11} {excl:5} {l['livro']:9} {l['no_banco']:>8}  {l['conteudo'][:70]} [{l['periodo']}]")
    elif args.cmd == "site-coletar":
        print(json.dumps(asyncio.run(fontes_site.coletar_fonte(args.uf, args.fonte, concurrency=args.concurrency)), ensure_ascii=False, indent=2)[:3000])
    elif args.cmd == "site-carregar":
        print(json.dumps(fontes_site.carregar_fonte(args.uf, args.fonte, rebuild=not args.no_fts), ensure_ascii=False, indent=2))
    elif args.cmd == "backfill-sp":
        asyncio.run(
            run_backfill_sp(
                years=[int(y) for y in args.years.split(",") if y.strip()] or None,
                pages_per_year=args.pages_per_year or None,
                concurrency=args.concurrency,
                use_cache=not args.no_cache,
                collect=not args.no_collect,
            )
        )
    elif args.cmd == "collect-seccionais":
        from ted_search.collectors.deoab_nacional import fetch_seccional_sample, save_records_to_database
        from ted_search.config import create_async_http_client, SECCIONAIS

        init_db(DB_PATH)
        target_ufs = [s["uf"] for s in SECCIONAIS] if args.ufs.upper() == "ALL" else [u.strip().upper() for u in args.ufs.split(",") if u.strip()]
        target_years = [int(y.strip()) for y in args.years.split(",") if y.strip()]

        async def _run_collect():
            sem = asyncio.Semaphore(args.concurrency)
            async with create_async_http_client(timeout=35.0) as client:
                total_salvos = 0
                for uf in target_ufs:
                    print(f"[collect-seccionais] Coletando Seccional OAB/{uf}...")
                    for yr in target_years:
                        recs = await fetch_seccional_sample(
                            client, uf, max_pages=args.pages, year=yr, concurrency=args.concurrency, sem=sem
                        )
                        if recs:
                            salvos = save_records_to_database(recs, rebuild_fts_after=False)
                            total_salvos += salvos
                            print(f"  -> OAB/{uf} ({yr}): {salvos} acórdãos salvos.")
                if total_salvos > 0:
                    print("[collect-seccionais] Reconstruindo índice FTS5...")
                    with get_connection(DB_PATH) as conn:
                        with conn:
                            rebuild_fts(conn)
                print(f"[OK] Coleta Seccionais concluída! Total salvo no banco: {total_salvos} julgados.")

        asyncio.run(_run_collect())
    elif args.cmd == "sync-pdfs":
        init_db(DB_PATH)
        with get_connection(DB_PATH) as conn:
            lim = None if args.limit <= 0 else args.limit
            count = asyncio.run(sync_pdfs_inteiro_teor(conn, limit=lim, concurrency=args.concurrency))
            if count > 0:
                with conn:
                    rebuild_fts(conn)
            print(f"Total de PDFs extraídos e indexados: {count}")
    elif args.cmd == "inteiro-teor":
        from ted_search import inteiro_teor

        init_db(DB_PATH)
        if args.recoletar:
            asyncio.run(fetch_etica_strapi_all(use_cache=False))
            asyncio.run(fetch_context_all_pages("EMENTA", page_size=1000, use_cache=False))
            print("Listagens atualizadas. PDFs novos do portal: rode scripts/etica_pdfs_scan.py e scripts/etica_pdfs_ocr.py.")
        with get_connection(DB_PATH) as conn:
            print(json.dumps(inteiro_teor.aplicar(conn), indent=2, ensure_ascii=False))
    elif args.cmd == "stats":
        init_db(DB_PATH)
        with get_connection(DB_PATH) as conn:
            print(json.dumps(get_global_stats(conn), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
