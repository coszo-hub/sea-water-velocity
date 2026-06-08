#!/usr/bin/env bash
# sync_metrics.sh — daily git push of the cron's per-day stats CSVs and
# per-event diagnostic logs to coszo-hub/Tidal-Seafloor-Pressure.
# Triggered by a separate cron entry (~18:35 UTC).
#
# The cron pipeline writes to (relative to PREST-data-collection/):
#   output/metrics/<station>_<run>_pipeline_stats.csv
#   output/diagnostics/<event>_<station>_<run>.txt
#
# Both paths are git-tracked inside the monorepo (no top-level mirror).
# This script just pulls, stages those two paths, commits, and pushes.
#
# Auth: a deploy key on the VM with write access to
#       coszo-hub/Tidal-Seafloor-Pressure only.
# Override the clone location via env: TSP_CLONE=/path/to/Tidal-Seafloor-Pressure

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# After the repo migration this code lives at <Tidal-Seafloor-Pressure>/
# PREST-data-collection/, so its parent IS the monorepo root.
TSP_CLONE="${TSP_CLONE:-$(cd "$REPO_ROOT/.." && pwd)}"
LOG_FILE="$REPO_ROOT/log/sync_metrics.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec >> "$LOG_FILE" 2>&1

echo "============================================================"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  sync_metrics start"
echo "  repo:        $REPO_ROOT"
echo "  TSP clone:   $TSP_CLONE"

if [ ! -d "$TSP_CLONE/.git" ]; then
    echo "ERROR: $TSP_CLONE is not a git clone. Aborting."
    exit 1
fi

# Pull latest first (rebase to keep history linear; autostash for safety in
# case the cron pipeline is mid-write to output/metrics/ or output/diagnostics/)
if ! ( cd "$TSP_CLONE" && git pull --rebase --autostash ); then
    echo "WARN: git pull --rebase failed; continuing with local state"
fi

cd "$TSP_CLONE"
git add PREST-data-collection/output/metrics/ \
        PREST-data-collection/output/diagnostics/

if git diff --cached --quiet; then
    echo "  no changes — skipping commit/push"
else
    commit_msg="metrics: sync $(date -u +%Y-%m-%d)"
    if git commit -m "$commit_msg"; then
        if git push origin main; then
            echo "  pushed: $commit_msg"
        else
            echo "WARN: git push failed; will retry next run"
        fi
    else
        echo "WARN: git commit failed"
    fi
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  sync_metrics end"
