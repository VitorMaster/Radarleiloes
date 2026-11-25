#!/usr/bin/env bash
# Rotina diária do Radar de Leilões (usada pela tarefa agendada).
#  1. restaura o banco de dados do branch "radar-dados" (histórico entre execuções);
#  2. roda `radar rotina` (seg: completa · ter-sex: atualização · sáb: resumo semanal);
#  3. salva o banco de volta em "radar-dados" (um único commit, sobrescrito a cada dia).
# O e-mail é enviado depois, a partir de data/relatorio/manifest.json.
# Uso: scripts/rotina_agendada.sh [auto|completo|atualizacao|semanal]
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-auto}"
DATA_BRANCH="${RADAR_DATA_BRANCH:-radar-dados}"

pip install -q -e . >/dev/null 2>&1 || pip install -q -e .
mkdir -p data

if git fetch -q origin "$DATA_BRANCH" 2>/dev/null; then
  git show FETCH_HEAD:radar.db.gz | gunzip > data/radar.db
  echo "Histórico restaurado de $DATA_BRANCH."
else
  echo "Branch $DATA_BRANCH não existe ainda: começando um histórico novo."
fi

export RADAR_DB="data/radar.db"
rm -rf data/relatorio
radar rotina --modo "$MODE" --out data/relatorio

bash scripts/salvar_historico.sh
