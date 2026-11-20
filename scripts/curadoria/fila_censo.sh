#!/bin/bash
# Fila única de coleta em modo censo: uma Seccional por vez. Uso: fila_censo.sh UF [UF...]
cd "$(dirname "$0")/../.." || exit 1
for uf in "$@"; do
  echo "=== $uf coleta $(date -u +%H:%M:%S)"
  .venv/bin/ted-cli sec-coletar --uf "$uf" --concurrency 10 2>&1 | grep -v "publicações inspecionadas" | tail -9
  # segunda passada: só refaz janelas que não fecharam e publicações que falharam
  .venv/bin/ted-cli sec-coletar --uf "$uf" --concurrency 10 2>&1 | grep -E "parciais [1-9]|falhas [1-9]" | sed 's/^/  [2a passada ainda aberta] /'
  echo "=== $uf carga $(date -u +%H:%M:%S)"
  .venv/bin/ted-cli sec-carregar --uf "$uf" --no-fts 2>&1 | .venv/bin/python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print({k: d[k] for k in ('uf', 'normalizados', 'rejeitados', 'inseridos', 'anos_preservados')})
except Exception as e:
    print('ERRO NA CARGA', e)
"
  echo "=== $uf fim $(date -u +%H:%M:%S)"
done
.venv/bin/ted-cli cobertura
echo "FILA CONCLUIDA"
