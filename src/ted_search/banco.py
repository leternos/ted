"""Empacota e desempacota o banco SQLite para caber no Git: `.xz` cortado em partes de até 90 MB.

O GitHub recusa arquivo acima de 100 MB. Até a onda 2 o banco coube inteiro num `.xz`; daí em diante ele
vai em partes (`ted_jurisprudencia.db.xz.parte-00`, `-01`, ...) com um manifesto que guarda o tamanho e o
SHA-256 do banco, conferidos ao desempacotar.

    python -m ted_search.banco empacotar      # depois de `ted-cli cobertura --fts`
    python -m ted_search.banco desempacotar   # no build da imagem e na primeira execução local
    python -m ted_search.banco conferir
"""

from __future__ import annotations

import hashlib
import json
import lzma
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ted_search.config import DB_PATH

TAMANHO_DA_PARTE = 90 * 1000 * 1000
BLOCO = 1 << 20


def _partes(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}.xz.parte-*"))


def _manifesto(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.manifesto.json")


def tem_pacote(db_path: Path = DB_PATH) -> bool:
    return bool(_partes(db_path)) or db_path.with_name(f"{db_path.name}.xz").exists()


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        while bloco := f.read(BLOCO):
            h.update(bloco)
    return h.hexdigest()


def empacotar(db_path: Path = DB_PATH, nivel: int = 9) -> dict:
    """Comprime o banco e grava as partes e o manifesto ao lado dele. Apaga o pacote anterior."""
    with sqlite3.connect(db_path) as conn:
        julgados = conn.execute("SELECT COUNT(*) FROM julgados").fetchone()[0]
    for velho in _partes(db_path) + [db_path.with_name(f"{db_path.name}.xz")]:
        velho.unlink(missing_ok=True)
    compressor = lzma.LZMACompressor(preset=nivel | lzma.PRESET_EXTREME)
    partes: list[dict] = []
    atual = None
    escrito = 0

    def gravar(dados: bytes) -> None:
        nonlocal atual, escrito
        while dados:
            if atual is None or escrito >= TAMANHO_DA_PARTE:
                if atual:
                    atual.close()
                caminho = db_path.with_name(f"{db_path.name}.xz.parte-{len(partes):02d}")
                partes.append({"arquivo": caminho.name})
                atual = caminho.open("wb")
                escrito = 0
            cabe = TAMANHO_DA_PARTE - escrito
            atual.write(dados[:cabe])
            escrito += len(dados[:cabe])
            dados = dados[cabe:]

    if shutil.which("xz"):
        # o xz do sistema usa todos os núcleos; em Python puro a compressão máxima leva dezenas de minutos
        with subprocess.Popen(["xz", f"-{nivel}e", "-T0", "-c", str(db_path)], stdout=subprocess.PIPE) as proc:
            while bloco := proc.stdout.read(BLOCO):
                gravar(bloco)
        if proc.returncode:
            raise RuntimeError(f"xz terminou com código {proc.returncode}")
    else:
        with db_path.open("rb") as f:
            while bloco := f.read(BLOCO):
                gravar(compressor.compress(bloco))
        gravar(compressor.flush())
    if atual:
        atual.close()
    for p in partes:
        p["bytes"] = (db_path.parent / p["arquivo"]).stat().st_size
    manifesto = {
        "banco": db_path.name,
        "bytes": db_path.stat().st_size,
        "sha256": _sha256(db_path),
        "julgados": julgados,
        "empacotado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "partes": partes,
    }
    _manifesto(db_path).write_text(json.dumps(manifesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifesto


def desempacotar(db_path: Path = DB_PATH, conferir_sha: bool = True) -> dict:
    """Recompõe o banco a partir das partes (ou do `.xz` único, formato antigo) e confere tamanho e SHA-256."""
    partes = _partes(db_path)
    unico = db_path.with_name(f"{db_path.name}.xz")
    fontes = partes or ([unico] if unico.exists() else [])
    if not fontes:
        raise FileNotFoundError(f"Nenhum pacote do banco em {db_path.parent}")
    destino = db_path.with_name(f"{db_path.name}.tmp")
    descompressor = lzma.LZMADecompressor()
    h = hashlib.sha256()
    total = 0
    with destino.open("wb") as saida:
        for fonte in fontes:
            with fonte.open("rb") as f:
                while bloco := f.read(BLOCO):
                    dados = descompressor.decompress(bloco)
                    saida.write(dados)
                    h.update(dados)
                    total += len(dados)
    if not descompressor.eof:
        destino.unlink()
        raise ValueError("Pacote do banco incompleto: falta alguma parte")
    resultado = {"bytes": total, "sha256": h.hexdigest(), "partes": len(fontes)}
    arq = _manifesto(db_path)
    if conferir_sha and partes and arq.exists():
        esperado = json.loads(arq.read_text(encoding="utf-8"))
        if (esperado["bytes"], esperado["sha256"]) != (total, resultado["sha256"]):
            destino.unlink()
            raise ValueError("O banco recomposto não confere com o manifesto (tamanho ou SHA-256)")
        resultado["julgados"] = esperado.get("julgados")
    destino.replace(db_path)
    return resultado


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    comando = argv[0] if argv else ""
    if comando == "empacotar":
        print(json.dumps(empacotar(), indent=2, ensure_ascii=False))
    elif comando == "desempacotar":
        print(json.dumps(desempacotar(), indent=2, ensure_ascii=False))
    elif comando == "conferir":
        m = json.loads(_manifesto(DB_PATH).read_text(encoding="utf-8"))
        ok = DB_PATH.exists() and (DB_PATH.stat().st_size, _sha256(DB_PATH)) == (m["bytes"], m["sha256"])
        print(json.dumps({"confere": ok, "julgados": m.get("julgados"), "partes": len(m["partes"])}, ensure_ascii=False))
        return 0 if ok else 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
