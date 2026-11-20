#!/usr/bin/env bash
# ==============================================================================
# Arquivo do banco do TED num bucket do Google Cloud Storage.
#
# Para que serve: guardar cada versão publicada do banco, com data, fora do Git.
# Isso dá um ponto de volta (rollback) e, no futuro, permite tirar o banco do
# repositório. Rode no Cloud Shell, dentro do repositório já clonado.
#
#   ./scripts/banco_bucket.sh criar      # uma vez só
#   ./scripts/banco_bucket.sh publicar   # envia o banco que está no repositório
#   ./scripts/banco_bucket.sh listar
#   ./scripts/banco_bucket.sh baixar [AAAA-MM-DD]   # traz uma versão para data/
# ==============================================================================
set -euo pipefail

PROJECT_ID="${GCP_PROJECT:-dicionario-503613}"
REGION="${GCP_REGION:-us-central1}"
BUCKET="gs://${PROJECT_ID}-ted-banco"
DADOS="$(cd "$(dirname "$0")/.." && pwd)/data"
BASE="ted_jurisprudencia.db"

case "${1:-}" in
  criar)
    gcloud storage buckets create "${BUCKET}" --project "${PROJECT_ID}" --location "${REGION}" \
      --uniform-bucket-level-access --public-access-prevention
    # versões antigas de um mesmo objeto somem depois de 90 dias; as pastas datadas ficam
    gcloud storage buckets update "${BUCKET}" --versioning
    echo "Bucket criado: ${BUCKET} (privado)"
    ;;
  publicar)
    ls "${DADOS}/${BASE}".xz.parte-* >/dev/null 2>&1 || { echo "Não há pacote do banco em ${DADOS}"; exit 1; }
    DIA="$(python3 -c "import json;print(json.load(open('${DADOS}/${BASE}.manifesto.json'))['empacotado_em'][:10])")"
    gcloud storage cp "${DADOS}/${BASE}".xz.parte-* "${DADOS}/${BASE}.manifesto.json" "${BUCKET}/${DIA}/"
    gcloud storage cp "${DADOS}/${BASE}".xz.parte-* "${DADOS}/${BASE}.manifesto.json" "${BUCKET}/atual/"
    echo "Publicado em ${BUCKET}/${DIA}/ e ${BUCKET}/atual/"
    ;;
  listar)
    gcloud storage ls "${BUCKET}/"
    ;;
  baixar)
    DIA="${2:-atual}"
    rm -f "${DADOS}/${BASE}".xz.parte-* "${DADOS}/${BASE}.manifesto.json"
    gcloud storage cp "${BUCKET}/${DIA}/*" "${DADOS}/"
    echo "Versão ${DIA} baixada para ${DADOS}"
    ;;
  *)
    sed -n 2,14p "$0"; exit 2
    ;;
esac
