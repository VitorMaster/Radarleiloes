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

# salva o banco: commit órfão com um único arquivo, sem acumular histórico
tmp="$(mktemp -d)"
gzip -9 -c data/radar.db > "$tmp/radar.db.gz"
blob="$(git hash-object -w "$tmp/radar.db.gz")"
tree="$(printf '100644 blob %s\tradar.db.gz\n' "$blob" | git mktree)"
commit="$(GIT_AUTHOR_NAME="Radar de Leilões" GIT_AUTHOR_EMAIL="radar@localhost" \
          GIT_COMMITTER_NAME="Radar de Leilões" GIT_COMMITTER_EMAIL="radar@localhost" \
          git commit-tree "$tree" -m "Radar: banco de dados de $(date -u +%Y-%m-%d)")"
for i in 1 2 3 4; do
  git push -q -f origin "$commit:refs/heads/$DATA_BRANCH" && break
  sleep $((2 ** i))
done
echo "Histórico salvo em $DATA_BRANCH ($(du -h "$tmp/radar.db.gz" | cut -f1))."
