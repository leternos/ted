FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TED_DATA_DIR=/app/data \
    TED_DB_PATH=/app/data/ted_jurisprudencia.db \
    TED_ACCESS_PASSWORD=609099 \
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

# Copiar e descompactar o banco SQLite + FTS5 pré-indexado (97.220 julgados - CFOAB + 27 Seccionais) durante o build.
# O banco vai em .xz para caber no limite de 100 MB por arquivo do GitHub; o lzma do próprio Python descompacta.
COPY data/ted_jurisprudencia.db.xz /app/data/ted_jurisprudencia.db.xz
RUN python -c "import lzma, shutil; shutil.copyfileobj(lzma.open('/app/data/ted_jurisprudencia.db.xz'), open('/app/data/ted_jurisprudencia.db', 'wb'), 1048576)" && \
    rm /app/data/ted_jurisprudencia.db.xz && \
    chmod 666 /app/data/ted_jurisprudencia.db && \
    chmod 777 /app/data

EXPOSE 8080

# Verificação de integridade para orquestradores de contêineres e Cloud Run
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Executar servidor FastAPI / Uvicorn na porta exigida pelo Google Cloud Run ($PORT)
CMD ["sh", "-c", "uvicorn ted_search.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
