# Buscador de Jurisprudências do CFOAB / TED (1994–2026)

Buscador unificado, enriquecido e de alta performance para a jurisprudência do **Conselho Federal da Ordem dos Advogados do Brasil (CFOAB)** com foco em **Ética e Disciplina (TED / Segunda Câmara, Turmas Recursais, Órgão Especial, Conselho Pleno, Consultas e Súmulas)**.

---

## 1. Diagnóstico de Viabilidade e Arquitetura de Backfill

A coleta de dados e backfill foram projetados com base em duas APIs REST oficiais públicas e um repositório de documentos em nuvem do CFOAB:

| Fonte Oficial | Endpoint | Conteúdo | Estratégia de Backfill |
| :--- | :--- | :--- | :--- |
| **OAB Jurisprudência** | `https://jurisprudencia.oab.org.br/api/jurisprudencia/v1/portal/pesquisa/contexto/{CTX}` | **23.953** Ementas (sendo **22.839** do CFOAB), **48** Consultas e **39** Súmulas (1994–2026) | Paginação em lotes de `size=1000` (sem bloqueio de *deep pagination*). Todo o histórico de 32 anos é baixado em ~25 requisições HTTP (~25s). |
| **Portal Ética e Disciplina (Strapi)** | `https://services.oab.org.br/etica-disciplina/api/ementarios?populate=*` | **1.402** Acórdãos selecionados da 2ª Câmara (`SCA`) e 1ª, 2ª e 3ª Turmas (`PTU`, `STU`, `TTU`) | Paginação em lotes de `pageSize=500` (~3 requisições HTTP). |
| **Inteiro Teor em PDF (Azure Blob)** | `https://cfoabstoragestrapi.blob.core.windows.net/etica-disciplina/assets/*.pdf` | **1.402** PDFs contendo Relatório, Voto e Acórdão na íntegra | Download assíncrono concorrente + cache em `data/pdfs/` + extração de texto via `pypdf` + extração *on-demand* ao abrir qualquer julgado na UI. |

### Enriquecimento do Legado (1994–2018)

Na base oficial da OAB, cerca de **86% dos julgados do Conselho Federal (`19.702` registros)** migrados do antigo *Ementário Global* estavam categorizados genericamente como `Órgão Julgador = "Conselho Federal"` e `Classe = "OUTRO"`, sem indicação de Câmara/Turma, artigos violados ou resultado.

O módulo [`src/ted_search/parser.py`](file:///home/antigravity/leternos/ted/src/ted_search/parser.py) aplica expressões regulares jurídicas sobre o texto bruto das ementas e acórdãos para extrair e normalizar:
- **Órgão Julgador e Sigla:** `Segunda Câmara (SCA)`, `1ª Turma da 2ª Câmara (SCA-PTU)`, `2ª Turma da 2ª Câmara (SCA-STU)`, `3ª Turma da 2ª Câmara (SCA-TTU)`, `Órgão Especial (OEP)`, `Conselho Pleno (COP)`, `Primeira Câmara (PCA)`, `Terceira Câmara (TCA)`.
- **Dispositivos Normativos:** Todos os incisos do `Art. 34 do EAOAB (I a XXX)`, `Art. 36 (Censura)`, `Art. 37 (Suspensão)`, `Art. 38 (Exclusão)`, `Art. 41 (Reabilitação)`, `Art. 43 (Prescrição)`, `Art. 70, § 3º (Suspensão Preventiva)`, `Art. 73, § 5º (Revisão Disciplinar)`, além do `CED` e `Regulamento Geral`.
- **Sanção Disciplinar:** `Censura`, `Advertência (Ofício Reservado)`, `Suspensão`, `Suspensão Preventiva`, `Exclusão`, `Multa`.
- **Resultado do Julgamento:** `Provido`, `Parcialmente Provido`, `Improvido`, `Não Conhecido`, `Prescrição Reconhecida`, `Embargos Rejeitados/Acolhidos`, `Anulado / Nulidade`.
- **UF / Seccional de Origem:** Mapeada pelo prefixo numérico do processo OAB (`01` a `27`) e pelas partes/cabeçalho.

---

## 2. Como Executar

### Instalação

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

### Executar o Backfill e Indexação FTS5

```bash
# Baixa todas as ementas/súmulas/consultas + Strapi Ética e extrai um lote inicial de PDFs
.venv/bin/ted-cli backfill --pdf-limit 150 --concurrency 12

# Para baixar e indexar o restante dos 1.402 PDFs de inteiro teor:
.venv/bin/ted-cli sync-pdfs --limit 0 --concurrency 12

# Consultar estatísticas de enriquecimento do banco:
.venv/bin/ted-cli stats
```

### Iniciar o Servidor Web e API (`FastAPI`)

```bash
.venv/bin/uvicorn ted_search.api:app --host 0.0.0.0 --port 8000
```

Acesse `http://localhost:8000` para utilizar a interface web ou `http://localhost:8000/docs` para a documentação interativa OpenAPI/Swagger.

### Executar os Testes Automatizados

```bash
.venv/bin/pytest -v
```

---

## 3. Publicação no Google Cloud Run

O repositório já inclui o banco SQLite + FTS5 pré-indexado compactado em [`data/ted_jurisprudencia.db.gz`](file:///home/antigravity/leternos/ted/data/ted_jurisprudencia.db.gz) (`~38 MB`, com os `25.222` julgados e índice `BM25` prontos) e um [`Dockerfile`](file:///home/antigravity/leternos/ted/Dockerfile) otimizado que descompacta o banco no build da imagem (custo zero de banco externo e *cold start* instantâneo).

### Opção A — Deploy Contínuo pelo Console do Google Cloud (Sem CLI)
1. Acesse o [Console do Google Cloud Run](https://console.cloud.google.com/run).
2. Clique em **Create Service** (Criar serviço) → selecione **Continuously deploy from a repository** (Implantar continuamente de um repositório).
3. Conecte sua conta GitHub e selecione o repositório `leternos/ted` (branch `main`, tipo de build **Dockerfile**).
4. Em **Authentication**, selecione **Allow unauthenticated invocations** (Permitir invocações não autenticadas).
5. Em **Container(s), Volumes, Networking, Security**, ajuste a memória para **1 GiB** e clique em **Create**.

### Opção B — Deploy via 1 comando (`gcloud` / Cloud Shell)
Abra o [Google Cloud Shell](https://shell.cloud.google.com/) ou seu terminal autenticado com `gcloud` e execute:

```bash
git clone https://github.com/leternos/ted.git
cd ted
gcloud run deploy ted-jurisprudencia \
  --source . \
  --region southamerica-east1 \
  --memory 1Gi \
  --cpu 1 \
  --allow-unauthenticated
```