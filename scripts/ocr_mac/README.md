# OCR em lote dos PDFs de inteiro teor (para rodar no Mac)

Lê `data/inteiro_teor/ocr_lista.tsv` (2.098 PDFs) e grava um JSON por PDF com o texto de cada página,
**exatamente como o motor leu** (sem máscara, abreviação, anonimização ou corte). Não grava no banco.

- **1.989 PDFs do Portal de Ética** (25.133 páginas): escaneados. Usam o motor escolhido (Vision ou Tesseract).
- **109 PDFs do portal de jurisprudência**: PDFs digitais com texto real. Usam a camada de texto do próprio PDF (sem OCR).
- É **retomável**: o que já está em `--saida` é pulado. Pode interromper com Ctrl+C e rodar de novo.

## No Mac (Apple Vision)

```bash
git fetch origin claude/eager-newton-g15rjj && git checkout claude/eager-newton-g15rjj
python3 -m venv .venv && source .venv/bin/activate
pip install pyobjc-framework-Vision pyobjc-framework-Quartz pypdf

# 1) teste em 3 PDFs: confira se o texto da amostra está bom
python scripts/ocr_mac/ocr_lote.py --teste --saida ~/ted_ocr_teste

# 2) lote completo (ajuste --workers; o padrão é núcleos - 2)
python scripts/ocr_mac/ocr_lote.py --saida ~/ted_ocr
```

- O caminho do Vision (`paginas_vision` em `src/ted_search/ocr_lote.py`) **não foi executado ainda**, porque foi escrito em ambiente Linux.
  Por isso o passo 1 é obrigatório. Se falhar, a mensagem de erro aparece no terminal e em `~/ted_ocr_teste/erros.tsv`.
- `--sem-correcao` desliga a correção de idioma do Vision, se você preferir o texto mais cru.
- Com Tesseract (qualquer sistema): `--motor tesseract` (precisa de `tesseract` com idioma `por` e `pdftoppm`).

## Devolver os resultados

Compacte a pasta de saída (`zip -r ted_ocr.zip ~/ted_ocr`) e coloque o `.zip` no Drive ou no Dropbox.
Não suba para o repositório, porque ele é público.
