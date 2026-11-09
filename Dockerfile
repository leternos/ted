FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TED_DATA_DIR=/app/data \
    TED_DB_PATH=/app/data/ted_jurisprudencia.db \
    PORT=8080

WORKDIR /app

# Utilitários de OCR em português para fallback de PDFs escaneados com camada corrompida
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-por && \
    rm -rf /var/lib/apt/lists/*

# Instalar pacote e dependências de produção
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Copiar e descompactar o banco SQLite + FTS5 pré-indexado (25.222 julgados) durante o build
COPY data/ted_jurisprudencia.db.gz /app/data/ted_jurisprudencia.db.gz
RUN gunzip -f /app/data/ted_jurisprudencia.db.gz && \
    chmod 666 /app/data/ted_jurisprudencia.db && \
    chmod 777 /app/data

EXPOSE 8080

# Executar servidor FastAPI / Uvicorn na porta exigida pelo Google Cloud Run ($PORT)
CMD ["sh", "-c", "uvicorn ted_search.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
