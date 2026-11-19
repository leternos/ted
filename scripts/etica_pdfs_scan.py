"""Varre os PDFs do Portal de Ética e Disciplina: páginas, nº do processo lido do próprio PDF e se há relatório e voto.

Uso: python scripts/etica_pdfs_scan.py   (retomável; grava data/inteiro_teor/etica_pdfs.jsonl, uma linha por PDF)
"""
import asyncio, json, re, subprocess, sys, tempfile, unicodedata
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
ENTRADA = RAIZ / "data/raw/etica_strapi_ementarios.json"
SAIDA = RAIZ / "data/inteiro_teor/etica_pdfs.jsonl"
# nº CNJ-OAB como sai da camada de texto dos PDFs escaneados: dígitos trocados por O/o/l/I e pontuação solta
RE_PROC = re.compile(r"([\dOoIl]{2})[.,]\s?([\dOoIl]{4})[.,]\s?((?:19|20)[\dOoIl]{2})[.,]\s?([\dOoIl]{6})\s?[-—–]\s?([\dOoIl])")


def _dig(s: str) -> str:
    return s.translate(str.maketrans("OoIl", "0011"))


def sem_acento(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", t.upper()) if unicodedata.category(ch) != "Mn")


def analisar(pdf: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf); f.flush()
        info = subprocess.run(["pdfinfo", f.name], capture_output=True, text=True, timeout=60).stdout
        texto = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, timeout=120).stdout
    m = re.search(r"Pages:\s+(\d+)", info)
    plano = sem_acento(texto)
    procs = []
    for g in RE_PROC.finditer(texto[:6000]):
        p = "{}.{}.{}.{}-{}".format(*(_dig(x) for x in g.groups()))
        if p not in procs:
            procs.append(p)
    return {
        "paginas": int(m.group(1)) if m else None,
        "chars": len(texto),
        "processos": procs[:5],
        "tem_relatorio": bool(re.search(r"RELAT[O0]RI[O0]", plano)),
        "tem_voto": bool(re.search(r"\bV\s?[O0]\s?T\s?[O0]\b", plano)),
        "tem_ementa": "EMENTA" in plano,
        "tem_acordao": bool(re.search(r"AC[O0]RD[AÃ][O0M]", plano)),
        "cabecalho": " ".join(texto[:700].split())[:400],
    }


async def main() -> None:
    itens = json.loads(ENTRADA.read_text())
    feitos = set()
    if SAIDA.exists():
        feitos = {json.loads(l)["strapi_id"] for l in SAIDA.read_text().splitlines() if l.strip()}
    pend = [i for i in itens if i["id"] not in feitos]
    print(f"{len(itens)} no portal, {len(feitos)} já lidos, {len(pend)} a ler", flush=True)
    sem = asyncio.Semaphore(6)
    out = SAIDA.open("a")
    n = 0

    async def um(cl, it):
        nonlocal n
        url = (it.get("documento") or {}).get("url")
        reg = {"strapi_id": it["id"], "nome": (it.get("nome") or "").strip(), "ano": it.get("ano"),
               "camara": (it.get("camara") or {}).get("slug"), "pdf_url": url}
        async with sem:
            for tent in range(4):
                try:
                    r = await cl.get(url)
                    r.raise_for_status()
                    if not r.content.startswith(b"%PDF"):
                        raise ValueError("não é PDF")
                    reg.update(await asyncio.to_thread(analisar, r.content))
                    reg["bytes"] = len(r.content)
                    break
                except Exception as e:  # noqa: BLE001
                    reg["erro"] = f"{type(e).__name__}: {e}"[:200]
                    await asyncio.sleep(2 + 3 * tent)
            else:
                pass
            if "paginas" in reg:
                reg.pop("erro", None)
        if "paginas" in reg:  # falha não é gravada: fica para a próxima execução
            out.write(json.dumps(reg, ensure_ascii=False) + "\n"); out.flush()
        n += 1
        if n % 100 == 0:
            print(f"  {n}/{len(pend)}", flush=True)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as cl:
        await asyncio.gather(*(um(cl, it) for it in pend))
    print("FIM", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
