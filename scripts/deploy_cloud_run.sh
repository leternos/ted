#!/usr/bin/env bash
# ==============================================================================
# Script de Deploy Automatizado para o Google Cloud Run
# Buscador Nacional de Jurisprudência Ético-Disciplinar da OAB (CFOAB + 27 Seccionais)
# ==============================================================================

set -euo pipefail

# Parâmetros com fallback para as configurações do projeto
PROJECT_ID="${1:-${GCP_PROJECT:-dicionario-503613}}"
REGION="${2:-us-central1}"
SERVICE_NAME="ted-jurisprudencia"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "=================================================================="
echo "🚀 Iniciando Deploy no Google Cloud Run"
echo "   Projeto GCP:  ${PROJECT_ID}"
echo "   Região:       ${REGION}"
echo "   Serviço:      ${SERVICE_NAME}"
echo "   Imagem:       ${IMAGE_NAME}"
echo "=================================================================="

# 1. Checagem do gcloud CLI
if ! command -v gcloud &> /dev/null; then
    echo "❌ Erro: o comando 'gcloud' não foi encontrado."
    echo "Instale o Google Cloud SDK ou execute este script diretamente no Google Cloud Shell:"
    echo "https://shell.cloud.google.com"
    exit 1
fi

# 2. Configurar o projeto no gcloud
echo "👉 Configurando projeto ${PROJECT_ID}..."
gcloud config set project "${PROJECT_ID}" --quiet

# 3. Habilitar APIs necessárias no GCP
echo "👉 Habilitando APIs do Google Cloud (Cloud Run, Cloud Build, Container Registry)..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    containerregistry.googleapis.com \
    --quiet

# 4. Enviar build remoto para o Google Cloud Build (não requer Docker instalado localmente)
echo "📦 Enviando código para compilação remota via Cloud Build..."
gcloud builds submit --tag "${IMAGE_NAME}" .

# 5. Fazer deploy do contêiner no Google Cloud Run
echo "🚀 Fazendo deploy no Google Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_NAME}" \
    --region "${REGION}" \
    --platform managed \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 5 \
    --timeout 300 \
    --port 8080 \
    --set-env-vars "TED_DATA_DIR=/app/data,TED_DB_PATH=/app/data/ted_jurisprudencia.db,TED_ACCESS_PASSWORD=609099"

# 6. Obter e exibir a URL pública do serviço
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --platform managed --region "${REGION}" --format="value(status.url)")

echo "=================================================================="
echo "✅ Deploy concluído com sucesso!"
echo "🌐 URL do Cloud Run: ${SERVICE_URL}"
echo "🔍 Healthcheck:       ${SERVICE_URL}/health"
echo "📊 Estatísticas:     ${SERVICE_URL}/api/stats"
echo "🏛️ Seccionais:       ${SERVICE_URL}/api/seccionais"
echo ""
echo "💡 Dica: Se quiser apontar o Firebase Hosting (site: ted-oab), execute:"
echo "   firebase deploy --only hosting"
echo "=================================================================="
