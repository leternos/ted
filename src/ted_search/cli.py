"""CLI oficial do projeto ted-search para Backfill, Enriquecimento, Extração de PDFs e Estatísticas."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

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
from ted_search.config import DB_PATH, OAB_JURIS_CONTEXTS
from ted_search.db import get_connection, get_global_stats, init_db, rebuild_fts


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
    concurrency: int = 10,
) -> None:
    t0 = time.perf_counter()
    init_db(DB_PATH)

    # 1. Coletar todos os contextos do OAB Jurisprudência
    raw_by_ctx: dict[str, list[dict]] = {}
    for ctx in OAB_JURIS_CONTEXTS:
        raw_by_ctx[ctx] = await fetch_context_all_pages(ctx, page_size=1000, use_cache=use_cache)

    # 2. Coletar os 1.402 acórdãos com PDF do Portal Ética e Disciplina (Strapi)
    strapi_raw = await fetch_etica_strapi_all(use_cache=use_cache)

    # 3. Normalizar, enriquecer e reconciliar registros no SQLite
    print("[pipeline] Normalizando, aplicando Parser Jurídico (1994-2026) e reconciliando bases...")
    canonical_index: dict[str, str] = {}  # canonical_ementa_key -> julgado_id

    with get_connection(DB_PATH) as conn:
        # Preservar inteiro_teor_texto já extraído anteriormente se re-executando backfill
        existing_texts = {
            r["id"]: (r["inteiro_teor_texto"], r["pdf_url"])
            for r in conn.execute(
                "SELECT id, inteiro_teor_texto, pdf_url FROM julgados WHERE tem_inteiro_teor = 1"
            ).fetchall()
        }

        conn.execute("DELETE FROM julgado_dispositivos;")

        with conn:
            for ctx, items in raw_by_ctx.items():
                for raw_item in items:
                    rec = normalize_oab_juris_item(raw_item, ctx)
                    if rec["id"] in existing_texts:
                        prev_txt, prev_pdf = existing_texts[rec["id"]]
                        rec["inteiro_teor_texto"] = prev_txt
                        rec["tem_inteiro_teor"] = 1
                        if not rec["pdf_url"]:
                            rec["pdf_url"] = prev_pdf
                    _insert_record(conn, rec)
                    ckey = build_canonical_ementa_key(rec["numero_ementa"], rec["sigla_orgao"])
                    if ckey:
                        canonical_index[ckey] = rec["id"]

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
                        SET pdf_url = COALESCE(pdf_url, ?),
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

        print(
            f"[pipeline] Reconciliação concluída: {reconciled_count} ementas vinculadas a PDFs existentes, "
            f"{inserted_strapi_count} acórdãos exclusivos adicionados do portal Ética e Disciplina."
        )

        # 4. Baixar e extrair PDFs de inteiro teor se solicitado
        if sync_pdfs_limit is None or sync_pdfs_limit > 0:
            await sync_pdfs_inteiro_teor(conn, limit=sync_pdfs_limit, concurrency=concurrency)

        # 5. Reconstruir índice FTS5 BM25
        print("[pipeline] Construindo índice Full-Text Search (SQLite FTS5 unicode61)...")
        with conn:
            rebuild_fts(conn)

        stats = get_global_stats(conn)

    elapsed = time.perf_counter() - t0
    print(f"\n=== BACKFILL CONCLUÍDO EM {elapsed:.1f}s ===")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI do Buscador de Jurisprudências CFOAB / TED")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bf = sub.add_parser("backfill", help="Executa o backfill das APIs da OAB, enriquece dados e indexa FTS5")
    p_bf.add_argument("--no-cache", action="store_true", help="Força novo download das APIs ignorando data/raw/")
    p_bf.add_argument(
        "--pdf-limit",
        type=int,
        default=150,
        help="Quantidade de PDFs de inteiro teor para baixar nesta execução (0=nenhum, -1=todos os 1402)",
    )
    p_bf.add_argument("--concurrency", type=int, default=10, help="Concorrência no download de PDFs")

    p_pdf = sub.add_parser("sync-pdfs", help="Baixa e extrai o texto integral dos PDFs de acórdãos")
    p_pdf.add_argument("--limit", type=int, default=0, help="Limite de PDFs (0 = todos os pendentes)")
    p_pdf.add_argument("--concurrency", type=int, default=12, help="Concorrência de download")

    sub.add_parser("stats", help="Exibe estatísticas completas do banco de dados e enriquecimento")

    args = parser.parse_args()
    if args.cmd == "backfill":
        pdf_lim = None if args.pdf_limit < 0 else args.pdf_limit
        asyncio.run(run_backfill(use_cache=not args.no_cache, sync_pdfs_limit=pdf_lim, concurrency=args.concurrency))
    elif args.cmd == "sync-pdfs":
        init_db(DB_PATH)
        with get_connection(DB_PATH) as conn:
            lim = None if args.limit <= 0 else args.limit
            count = asyncio.run(sync_pdfs_inteiro_teor(conn, limit=lim, concurrency=args.concurrency))
            if count > 0:
                with conn:
                    rebuild_fts(conn)
            print(f"Total de PDFs extraídos e indexados: {count}")
    elif args.cmd == "stats":
        init_db(DB_PATH)
        with get_connection(DB_PATH) as conn:
            print(json.dumps(get_global_stats(conn), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
