#!/usr/bin/env bash
# Salva data/radar.db no branch "radar-dados" como um único commit órfão (sobrescrito a cada vez).
set -euo pipefail
cd "$(dirname "$0")/.."
DATA_BRANCH="${RADAR_DATA_BRANCH:-radar-dados}"
tmp="$(mktemp -d)"
gzip -9 -c data/radar.db > "$tmp/radar.db.gz"
blob="$(git hash-object -w "$tmp/radar.db.gz")"
tree="$(printf '100644 blob %s\tradar.db.gz\n' "$blob" | git mktree)"
commit="$(GIT_AUTHOR_NAME="Radar Leilões" GIT_AUTHOR_EMAIL="radar@localhost" \
          GIT_COMMITTER_NAME="Radar Leilões" GIT_COMMITTER_EMAIL="radar@localhost" \
          git commit-tree "$tree" -m "Radar: banco de dados de $(date -u +%Y-%m-%d)")"
for i in 1 2 3 4; do
  git push -q -f origin "$commit:refs/heads/$DATA_BRANCH" 2>/dev/null && break
  sleep $((2 ** i))
done
echo "Histórico salvo em $DATA_BRANCH ($(du -h "$tmp/radar.db.gz" | cut -f1))."
