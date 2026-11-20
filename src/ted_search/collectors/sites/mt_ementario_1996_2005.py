"""OAB/MT - Ementário do TED 1996/2005 (ementas e consultas), PDF1644.pdf em https://www.oabmt.org.br/ted/documentos.

Um único PDF (91 páginas, texto nativo, lido com ``pdftotext -layout``): índice alfabético remissivo + 291 ementas numeradas,
cada uma aberta por ``N. ASSUNTO   VOLTAR AO ÍNDICE`` (o assunto pode quebrar em duas linhas). Cada item termina no rodapé
``(Proc. n. 2.558/02. Rel. FULANO. Segunda Turma. J. 29/09/2003. V.M)``. Três consultas (itens 1, 102 e 107) trazem relatório e
voto/parecer antes da ementa.

``esperado`` = quantidade de números de ementa distintos que o índice remissivo cita (contagem independente); ``listar`` parte o
corpo pelos cabeçalhos numerados em sequência (1, 2, 3...) e as duas contagens têm de bater. Id: ``PDF1644.pdf#N``.

O texto fica como o PDF o publica: só se retiram o número de página, o link "VOLTAR AO ÍNDICE" e o recuo/quebras de linha do
layout (linhas do mesmo parágrafo viram uma). O assunto do cabeçalho abre o texto. Nenhum nome é abreviado ou retirado.

NÃO usar o livro PDF44714 (2016/2019): traz aviso de reprodução proibida, aguarda decisão do dono.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any

from ted_search.collectors.sites import coleta_educada as ce
from ted_search.config import RAW_DIR
from ted_search.fontes import montar_registro, raw_dir_fonte
from ted_search.inteiro_teor import texto_tem_relatorio_e_voto

UF, SLUG = "MT", "ementario-1996-2005"
PDF_URL = "https://www.oabmt.org.br/admin2//Arquivos/Documentos/201202/PDF1644.pdf"
ARQUIVO = PDF_URL.rsplit("/", 1)[1]

RE_VOLTAR = re.compile(r"(?i)\bvoltar\s+ao\s+[ií]ndice\b")
RE_RODAPE = re.compile(
    r"\(\s*Proc\.?\s*(?:n[ºo]?\.?)?\s*([^,;)]*?)\s*[.,;]\s*Rel(?:ator|atora)?\.?[:;]?\s*(.+?)[.,]?\s*(?:(\S+)\s+Turma[.,]?\s*)?"
    r"J\.?:?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*[.,]?\s*(?:\(?(V\.\s?[UM])\.?\)?)?[.\s]*\)\s*$",
    re.I,
)
RE_TITULO_SECAO = re.compile(r"(?i)^(?:[IVX]+\s*[-–.)]\s*)?(?:d?o\s+)?(?:relat[óo]rio|voto|parecer)\s*[:.]?$")
TURMAS = {"primeira": 1, "segunda": 2, "terceira": 3, "quarta": 4, "quinta": 5}

_cache: dict[str, list[str]] = {}


def texto_do_pdf(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, timeout=180).stdout.decode("utf-8", "replace")


def linhas_sem_paginacao(texto: str) -> list[str]:
    """Linhas do texto, sem o número de página (última linha só de dígitos de cada página)."""
    saida: list[str] = []
    for pg in texto.split("\f"):
        ls = pg.rstrip().split("\n")
        while ls and not ls[-1].strip():
            ls.pop()
        if ls and re.fullmatch(r"\s*\d{1,3}\s*", ls[-1]):
            ls.pop()
        saida.extend(ls)
        saida.append("")
    return saida


def cabecalhos(linhas: list[str], primeiro: int = 1) -> list[int]:
    """Índices das linhas de cabeçalho ``N. ASSUNTO ... VOLTAR AO ÍNDICE``, em sequência 1, 2, 3..."""
    pos: list[int] = []
    inicio, n = 0, primeiro
    while True:
        rx = re.compile(rf"^\s*{n}\.\s+\S")
        achou = None
        for i in range(inicio, len(linhas)):
            if rx.match(linhas[i]) and any(RE_VOLTAR.search(linhas[j]) for j in range(i, min(i + 3, len(linhas)))):
                achou = i
                break
        if achou is None:
            return pos
        pos.append(achou)
        inicio, n = achou + 1, n + 1


def numeros_do_indice(linhas: list[str], primeiro_cabecalho: int) -> set[int]:
    """Números de ementa que o índice remissivo cita (contagem independente do corpo)."""
    nums: set[int] = set()
    for ln in linhas[:primeiro_cabecalho]:
        if "...." in ln:
            seg = re.split(r"\.{4,}", ln)[-1]
        elif re.fullmatch(r"[\s\d;]+", ln):
            seg = ln
        else:
            continue
        nums.update(int(x) for x in re.findall(r"\d+", seg))
    return nums


def paragrafos(linhas: list[str]) -> str:
    """Junta as linhas de cada parágrafo (linha terminada em hífen cola na seguinte, sem espaço); parágrafos separados por linha em branco."""
    paras: list[str] = []
    atual = ""
    for ln in linhas:
        s = re.sub(r"[ \t]+", " ", ln).strip()
        if not s:
            if atual:
                paras.append(atual)
                atual = ""
            continue
        if RE_TITULO_SECAO.match(s):  # 'RELATÓRIO', 'PARECER', 'VOTO' sozinhos na linha: ficam como parágrafo próprio
            if atual:
                paras.append(atual)
            paras.append(s)
            atual = ""
            continue
        atual = atual + s if atual.endswith("-") else (f"{atual} {s}" if atual else s)
    if atual:
        paras.append(atual)
    return "\n\n".join(paras)


def itens_do_texto(texto: str, primeiro: int = 1) -> list[dict[str, Any]]:
    linhas = linhas_sem_paginacao(texto)
    pos = cabecalhos(linhas, primeiro)
    fim = pos + [len(linhas)]
    saida: list[dict[str, Any]] = []
    for k, ini in enumerate(pos):
        n = primeiro + k
        cab = [linhas[ini]]
        j = ini + 1
        if not RE_VOLTAR.search(linhas[ini]):  # assunto quebrado em duas linhas: o link vem na seguinte
            while j < fim[k + 1] and j < ini + 3:
                cab.append(linhas[j])
                j += 1
                if RE_VOLTAR.search(cab[-1]):
                    break
        titulo = " ".join(RE_VOLTAR.sub("", c).strip() for c in cab)
        titulo = re.sub(rf"^\s*{n}\.\s*", "", re.sub(r"\s+", " ", titulo)).strip()
        corpo = paragrafos([ln for ln in linhas[j:fim[k + 1]] if not RE_VOLTAR.fullmatch(ln.strip())])
        saida.append({"id": f"{ARQUIVO}#{n}", "n": n, "assunto": titulo, "corpo": corpo, **_rodape(corpo)})
    return saida


def _rodape(corpo: str) -> dict[str, Any]:
    ult = re.sub(r"\s+", " ", corpo)
    ini = ult.rfind("(Proc")
    m = RE_RODAPE.match(ult[ini:]) if ini >= 0 else None
    if not m:
        return {"processo": None, "relator": None, "turma": None, "data": None, "votacao": None}
    ano = m.group(6)
    data = f"{int(ano):04d}-{int(m.group(5)):02d}-{int(m.group(4)):02d}" if len(ano) == 4 else None
    return {"processo": m.group(1).strip(), "relator": m.group(2).strip(), "turma": m.group(3), "data": data,
            "votacao": m.group(7).replace(" ", "") if m.group(7) else None}


def ementa_do_corpo(corpo: str) -> str:
    """Do último parágrafo que abre com 'EMENTA:' até o fim do rodapé; sem 'EMENTA:' o corpo inteiro."""
    paras = corpo.split("\n\n")
    for i in range(len(paras) - 1, -1, -1):
        if re.match(r"(?i)\s*EMENTA\s*:", paras[i]):
            return "\n\n".join(paras[i:])
    return corpo


async def _linhas(client: Any, sem: asyncio.Semaphore) -> list[str]:
    if "l" not in _cache:
        pasta = raw_dir_fonte(UF, SLUG, RAW_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / ARQUIVO
        if not arq.exists() or arq.stat().st_size < 10000:
            arq.write_bytes(await ce.baixar_bytes(client, sem, PDF_URL, marca=b"%PDF"))
        texto = texto_do_pdf(arq)
        (pasta / "texto.txt").write_text(texto, encoding="utf-8")
        _cache["l"] = linhas_sem_paginacao(texto)
    return _cache["l"]


async def esperado(client: Any, sem: asyncio.Semaphore) -> int | None:
    linhas = await _linhas(client, sem)
    pos = cabecalhos(linhas)
    return len(numeros_do_indice(linhas, pos[0])) if pos else 0


async def listar(client: Any, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    return itens_do_texto("\f".join(["\n".join(await _linhas(client, sem))]))


async def baixar(client: Any, sem: asyncio.Semaphore, item: dict[str, Any]) -> dict[str, Any] | None:
    return {**item, "pdf_url": PDF_URL}


def orgao_mt(turma: str | None) -> tuple[str, str, str]:
    n = TURMAS.get((turma or "").lower())
    if n:
        return f"{turma} Turma", f"OABMT-TED-T{n}", f"Tribunal de Ética e Disciplina da OAB/MT/{n}ª Turma Disciplinar"
    return "Tribunal de Ética e Disciplina da OAB/MT", "OABMT-TED", "Tribunal de Ética e Disciplina da OAB/MT"


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    corpo = (item.get("corpo") or "").strip()
    if len(corpo) < 40:
        return None
    assunto = item.get("assunto") or ""
    texto = f"{assunto}\n\n{corpo}" if assunto else corpo
    raw, sigla, norm = orgao_mt(item.get("turma"))
    rec = montar_registro(
        UF, SLUG, item["id"],
        ementa=ementa_do_corpo(corpo),
        inteiro_teor=texto,
        url=PDF_URL,
        orgao_raw=raw, sigla_orgao=sigla, orgao_norm=norm,
        data_decisao=item.get("data"),
        numero_processo=item.get("processo"),
        numero_ementa=str(item["n"]),
        relator=item.get("relator"),
        consulta=assunto.upper().startswith("CONSULTA"),
        publicacao="Site da OAB/MT - Ementário do TED 1996/2005 (PDF)",
    )
    rec["pdf_url"] = PDF_URL
    rec["is_ted_disciplinar"] = 1
    rec["tem_inteiro_teor"] = 1 if texto_tem_relatorio_e_voto(texto) else 0
    return rec
