#!/bin/bash
# sync_to_github.sh
# Exports lightweight CSV/JSON snapshots and pushes to GitHub.
# Does NOT push the raw SQLite DB.
#
# Crontab setup:
# */15 * * * * cd ~/Sol_Trader && bash sync_to_github.sh >> logs/sync.log 2>&1

set -e

REPO_DIR="$HOME/Sol_Trader"
cd "$REPO_DIR"

source venv/bin/activate
python export_snapshot.py

git add logs/exports/trades_summary.csv
git add logs/exports/stats_snapshot.json

if git diff --cached --quiet; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') | No changes to sync"
    exit 0
fi

git commit -m "snapshot: $(date '+%Y-%m-%d %H:%M')"
git push origin main --quiet

echo "$(date '+%Y-%m-%d %H:%M:%S') | Synced snapshot to GitHub ✅"
