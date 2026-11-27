"""OCR em lote, retomável, dos PDFs de data/inteiro_teor/ocr_lista.tsv.

Exemplos:
    python scripts/ocr_mac/ocr_lote.py --teste                    # 3 PDFs, mostra uma amostra
    python scripts/ocr_mac/ocr_lote.py --saida ~/ted_ocr          # lote completo (retoma de onde parou)
    python scripts/ocr_mac/ocr_lote.py --motor tesseract --limite 5

Grava um JSON por PDF em <saida>/<id>.json com o texto de cada página, exatamente como o motor leu.
Nada é gravado no banco. Para o que já existe em <saida>, o PDF é pulado (retomável).
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ted_search.ocr_lote import MOTORES  # noqa: E402

LISTA = Path(__file__).resolve().parents[2] / "data" / "inteiro_teor" / "ocr_lista.tsv"


def nome_arquivo(id_: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", id_) + ".json"


def baixar(url: str, tentativas: int = 4) -> bytes:
    ultimo: Exception | None = None
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ted-ocr/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(2**t * 2)
    raise RuntimeError(f"download falhou: {ultimo}")


def processar(linha: dict[str, str], motor: str, saida: str, correcao: bool) -> tuple[str, int, float, str | None]:
    """Roda em processo separado. Devolve (id, n_paginas, segundos, erro)."""
    t0 = time.time()
    id_ = linha["id"]
    try:
        # motor_sugerido=texto (PDF digital) usa a camada de texto; o resto usa o motor escolhido
        motor_real = "texto" if linha["motor_sugerido"] == "texto" else motor
        pdf = baixar(linha["pdf_url"])
        fn = MOTORES[motor_real]
        paginas = fn(pdf, correcao) if motor_real == "vision" else fn(pdf)
        registro = {
            "id": id_, "origem": linha["origem"], "numero_processo": linha["numero_processo"],
            "titulo": linha["titulo"], "pdf_url": linha["pdf_url"], "motor": motor_real,
            "n_paginas": len(paginas), "paginas": paginas, "texto": "\n".join(p["texto"] for p in paginas),
        }
        destino = Path(saida) / nome_arquivo(id_)
        tmp = destino.with_suffix(".tmp")
        tmp.write_text(json.dumps(registro, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, destino)
        return id_, len(paginas), time.time() - t0, None
    except Exception as e:  # noqa: BLE001
        return id_, 0, time.time() - t0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lista", default=str(LISTA))
    ap.add_argument("--saida", default="ocr_saida")
    ap.add_argument("--motor", choices=["vision", "tesseract"], default="vision" if sys.platform == "darwin" else "tesseract")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--limite", type=int, default=0, help="processa só os N primeiros pendentes")
    ap.add_argument("--ids", default="", help="ids separados por vírgula")
    ap.add_argument("--teste", action="store_true", help="3 PDFs de Ética e mostra uma amostra do texto")
    ap.add_argument("--sem-correcao", action="store_true", help="Vision sem correção de idioma")
    a = ap.parse_args()

    with open(a.lista, encoding="utf-8", newline="") as f:
        todas = list(csv.DictReader(f, delimiter="\t"))
    if a.ids:
        quer = {x.strip() for x in a.ids.split(",")}
        todas = [l for l in todas if l["id"] in quer]
    if a.teste:
        todas = [l for l in todas if l["motor_sugerido"] == "ocr"][:3]
        a.workers = 1
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    pendentes = [l for l in todas if not (saida / nome_arquivo(l["id"])).exists()]
    if a.limite:
        pendentes = pendentes[: a.limite]
    pag_total = sum(int(l["paginas"] or 0) for l in pendentes)
    print(f"{len(todas)} na lista, {len(todas) - len(pendentes)} já feitos, {len(pendentes)} a fazer (~{pag_total} páginas) | motor={a.motor} workers={a.workers}", flush=True)
    if not pendentes:
        return 0

    erros = saida / "erros.tsv"
    ok = 0
    t0 = time.time()
    pag_feitas = 0
    ctx = mp.get_context("spawn")  # pyobjc/Vision não gosta de fork
    with ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx) as ex:
        futs = {ex.submit(processar, l, a.motor, str(saida), not a.sem_correcao): l for l in pendentes}
        for i, fut in enumerate(as_completed(futs), start=1):
            id_, n, seg, erro = fut.result()
            if erro:
                with open(erros, "a", encoding="utf-8") as f:
                    f.write(f"{id_}\t{erro}\n")
                print(f"[{i}/{len(pendentes)}] ERRO {id_}: {erro}", flush=True)
                continue
            ok += 1
            pag_feitas += n
            dec = time.time() - t0
            falta = (pag_total - pag_feitas) * (dec / max(pag_feitas, 1))
            print(f"[{i}/{len(pendentes)}] {id_}: {n} págs em {seg:.0f}s | decorrido {dec / 60:.1f} min | faltam ~{falta / 60:.0f} min", flush=True)
    print(f"FIM: {ok} ok, {len(pendentes) - ok} com erro (ver {erros})", flush=True)

    if a.teste:
        for l in todas:
            p = saida / nome_arquivo(l["id"])
            if p.exists():
                r = json.loads(p.read_text(encoding="utf-8"))
                print(f"\n--- {r['id']} ({r['motor']}, {r['n_paginas']} págs) primeiras linhas da página 1 ---")
                print("\n".join(r["paginas"][0]["texto"].splitlines()[:12]))
    return 0 if ok == len(pendentes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
