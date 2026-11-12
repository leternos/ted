"""Script completo de diagnóstico das 27 Seccionais da OAB no Diário Eletrônico (DEOAB).

Mapeia:
- Volume anual (2020 a 2026)
- Setores de publicação do TED / Câmaras Recursais
- Tipos de publicação (Acórdão, Ementa, Decisão, Notificação, Edital)
- Presença de Ementa / Acórdão / Inteiro teor no conteúdo
- Classificação de viabilidade imediata de coleta
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ted_search.config import (
    DEOAB_API_BASE_URL,
    SECCIONAIS,
    create_async_http_client,
)
from ted_search.collectors.deoab_sp import html_to_paragraphs

YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
TERMS_TEST = ["ACORDAO", "EMENTA", "TED", "DISCIPLINAR"]

OUTPUT_FILE = Path("/home/antigravity/leternos/ted/data/diagnostic_27_seccionais.json")


async def get_search_count(
    client,
    org_id: int,
    year: int,
    termo: str | None = None,
    sem: asyncio.Semaphore | None = None,
) -> int:
    params = {
        "pagina": "1",
        "idOrganizacao": str(org_id),
        "inicio": f"01/01/{year}",
        "fim": f"31/12/{year}",
    }
    if termo:
        params["termo"] = termo
    
    if sem:
        async with sem:
            try:
                r = await client.get(f"{DEOAB_API_BASE_URL}/diario/pesquisa", params=params)
                if r.status_code == 200:
                    return int((r.json() or {}).get("quantidade") or 0)
            except Exception:
                pass
    return 0


async def sample_seccional_publications(
    client,
    org_id: int,
    uf: str,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    """Inspeciona publicações recentes para identificar setores, tipos e padrão de texto."""
    found_samples = []
    seen_ids = set()
    identified_sectors = set()
    identified_types = set()
    has_full_acordaos = False
    has_ementas = False

    # Procura candidatos entre 2023 e 2025 com termos chave
    for yr in [2024, 2025, 2023]:
        for term in ["ACORDAO", "EMENTA", "TED"]:
            params = {
                "pagina": "1",
                "idOrganizacao": str(org_id),
                "termo": term,
                "inicio": f"01/01/{yr}",
                "fim": f"31/12/{yr}",
            }
            async with sem:
                try:
                    r = await client.get(f"{DEOAB_API_BASE_URL}/diario/pesquisa", params=params)
                    if r.status_code == 200:
                        itens = (r.json() or {}).get("itens") or []
                        for it in itens[:4]:
                            if it["id"] not in seen_ids:
                                seen_ids.add(it["id"])
                                found_samples.append((it["id"], term, yr))
                except Exception:
                    pass
            if len(found_samples) >= 8:
                break
        if len(found_samples) >= 8:
            break

    # Baixar detalhes dos itens amostrados
    detailed_samples = []
    for pid, term, yr in found_samples[:10]:
        async with sem:
            try:
                pr = await client.get(f"{DEOAB_API_BASE_URL}/publicacao/{pid}/publicado")
                if pr.status_code == 200:
                    p = pr.json()
                    tipo_desc = ((p.get("tipoPublicacao") or {}).get("descricao") or "").strip()
                    setor_info = p.get("setorPublicacao") or {}
                    setor_nome = (setor_info.get("nomeAlternativo") or "").strip()
                    titulo = (p.get("titulo") or "").strip()
                    txt = html_to_paragraphs(p.get("conteudoCodificado") or p.get("conteudo") or "")

                    if setor_nome:
                        identified_sectors.add(setor_nome)
                    if tipo_desc:
                        identified_types.add(tipo_desc)

                    txt_lower = txt.lower()
                    is_acordao = "acordam" in txt_lower or "acórdão" in txt_lower or "vistos" in txt_lower
                    is_ementa = "ementa" in txt_lower
                    is_notificacao_only = (
                        "notifica" in txt_lower
                        or "intimação" in txt_lower
                        or "de ordem do presidente" in txt_lower
                        or "fica notificado" in txt_lower
                    ) and not (is_acordao or is_ementa)

                    if is_acordao:
                        has_full_acordaos = True
                    if is_ementa:
                        has_ementas = True

                    detailed_samples.append({
                        "id": pid,
                        "termo_busca": term,
                        "ano": yr,
                        "tipo": tipo_desc,
                        "setor": setor_nome,
                        "titulo": titulo[:100],
                        "tamanho_texto": len(txt),
                        "is_acordao": is_acordao,
                        "is_ementa": is_ementa,
                        "is_notificacao_only": is_notificacao_only,
                        "snippet": txt[:250].replace("\n", " "),
                    })
            except Exception:
                pass

    return {
        "setores_identificados": sorted(list(identified_sectors)),
        "tipos_identificados": sorted(list(identified_types)),
        "publica_acordao_inteiro_teor": has_full_acordaos,
        "publica_ementa": has_ementas,
        "amostras": detailed_samples,
    }


async def diagnose_seccional(
    client,
    sec: dict[str, Any],
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    org_id = sec["id_organizacao"]
    uf = sec["uf"]
    nome = sec["nome"]

    # 1. Obter contagens anuais
    # Para estimar volume ético-disciplinar, medimos ACORDAO e EMENTA
    volume_por_ano = {}
    for yr in YEARS:
        c_acordao = await get_search_count(client, org_id, yr, "ACORDAO", sem)
        c_ementa = await get_search_count(client, org_id, yr, "EMENTA", sem)
        c_ted = await get_search_count(client, org_id, yr, "TED", sem)
        volume_por_ano[yr] = {
            "acordao": c_acordao,
            "ementa": c_ementa,
            "ted": c_ted,
        }

    # 2. Inspecionar setores e conteúdo
    sample_analysis = await sample_seccional_publications(client, org_id, uf, sem)

    # 3. Calcular volume total aproximado de julgados ético-disciplinares
    # Usa max(acordao, ementa) ou combinação conforme os tipos identificados
    total_acordaos = sum(v["acordao"] for v in volume_por_ano.values())
    total_ementas = sum(v["ementa"] for v in volume_por_ano.values())
    total_ted = sum(v["ted"] for v in volume_por_ano.values())

    # Viabilidade imediata:
    # ALTA: Publica Acórdãos ou Ementas completas com volume mensurável no DEOAB
    # MÉDIA: Publica no DEOAB mas mistura com editais/notificações ou volume modesto
    # PORTAL PRÓPRIO / BAIXA DEOAB: Não publica acórdãos no DEOAB (só notificações) ou volume zerado
    if sample_analysis["publica_acordao_inteiro_teor"] or sample_analysis["publica_ementa"]:
        if total_acordaos > 200 or total_ementas > 200:
            viabilidade = "ALTA"
        else:
            viabilidade = "MÉDIA"
    elif total_ted > 0:
        viabilidade = "BAIXA_DEOAB (Apenas Notificações/Editais de TED)"
    else:
        viabilidade = "INEXISTENTE_NO_DEOAB"

    res = {
        "id_organizacao": org_id,
        "uf": uf,
        "nome": nome,
        "regiao": sec["regiao"],
        "viabilidade_imediata": viabilidade,
        "total_estimado_acordao": total_acordaos,
        "total_estimado_ementa": total_ementas,
        "total_estimado_ted": total_ted,
        "volume_por_ano": volume_por_ano,
        "setores_identificados": sample_analysis["setores_identificados"],
        "tipos_identificados": sample_analysis["tipos_identificados"],
        "publica_acordao_inteiro_teor": sample_analysis["publica_acordao_inteiro_teor"],
        "publica_ementa": sample_analysis["publica_ementa"],
        "amostras": sample_analysis["amostras"],
    }
    print(f"[{uf}] Diagnosticado: Viabilidade={viabilidade} | Acórdãos={total_acordaos} | Ementas={total_ementas} | TED={total_ted} | Setores={sample_analysis['setores_identificados']}")
    return res


async def run_full_diagnosis():
    sem = asyncio.Semaphore(15)
    async with create_async_http_client(timeout=30.0) as client:
        print(f"Iniciando diagnóstico completo das {len(SECCIONAIS)} Seccionais...")
        tasks = [diagnose_seccional(client, sec, sem) for sec in SECCIONAIS]
        results = await asyncio.gather(*tasks)

    # Ordenar por id_organizacao
    results.sort(key=lambda x: x["id_organizacao"])
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] Diagnóstico salvo com sucesso em {OUTPUT_FILE} ({len(results)} Seccionais analisadas).")


if __name__ == "__main__":
    asyncio.run(run_full_diagnosis())
