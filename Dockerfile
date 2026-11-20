FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TED_DATA_DIR=/app/data \
    TED_DB_PATH=/app/data/ted_jurisprudencia.db \
    TED_ACCESS_PASSWORD=609099 \
    TED_DB_READONLY=1 \
    PORT=8080

WORKDIR /app

# Utilitários de OCR em português para fallback de PDFs escaneados com camada corrompida
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-por curl && \
    rm -rf /var/lib/apt/lists/*

# Instalar pacote e dependências de produção
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Banco SQLite + FTS5 pré-indexado. Vai no repositório como .xz cortado em partes de 90 MB (o GitHub recusa
# arquivo acima de 100 MB), com um manifesto; o build recompõe o banco e confere tamanho e SHA-256.
COPY data/ted_jurisprudencia.db.* /app/data/
RUN python -m ted_search.banco desempacotar && \
    rm -f /app/data/ted_jurisprudencia.db.xz* && \
    chmod 666 /app/data/ted_jurisprudencia.db && \
    chmod 777 /app/data

EXPOSE 8080

# Verificação de integridade para orquestradores de contêineres e Cloud Run
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Executar servidor FastAPI / Uvicorn na porta exigida pelo Google Cloud Run ($PORT)
CMD ["sh", "-c", "uvicorn ted_search.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
