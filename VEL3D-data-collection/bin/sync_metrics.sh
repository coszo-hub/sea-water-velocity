#!/usr/bin/env bash
# sync_metrics.sh — daily git push of the near-real-time QC outputs, so the
# GitHub front page (README "Timing summary") stays current.
# Triggered by its own cron entry after the daily SeedLink jobs (~18:45 UTC).
#
# Commits (relative to this instrument folder, e.g. VEL3D-data-collection/):
#   output/temporal_anomaly/metrics/*.csv    timing CSV rows appended by the daily jobs
#   output/temporal_anomaly/figures/summary/ summary figures, regenerated here
#   output/metrics/, output/diagnostics/     pipeline stats / diagnostics (if any)
#   run/endtime_*.txt                        daily-job cursors
#
# Works in either repo (sea-water-velocity / absolute-seafloor-pressure): the
# git clone is the parent of this instrument folder.
# Auth: a deploy key on the VM with write access to this repo.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTR="$(basename "$REPO_ROOT")"                  # VEL3D-data-collection | PREST-data-collection
CLONE="${SYNC_CLONE:-$(cd "$REPO_ROOT/.." && pwd)}"
CONDA="${CONDA:-$(command -v conda || true)}"
LOG_FILE="$REPO_ROOT/log/sync_metrics.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec >> "$LOG_FILE" 2>&1

echo "============================================================"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  sync_metrics start  ($CLONE / $INSTR)"

if [ ! -d "$CLONE/.git" ]; then
    echo "ERROR: $CLONE is not a git clone. Aborting."
    exit 1
fi

# Pull first (rebase keeps history linear; autostash protects files the
# daily jobs may be writing).
if ! ( cd "$CLONE" && git pull --rebase --autostash ); then
    echo "WARN: git pull --rebase failed; continuing with local state"
fi

# Regenerate the summary figures from the updated CSVs (same file names, so
# the README embeds pick them up).
if [ -n "$CONDA" ] && [ -x "$CONDA" ]; then
    cd "$REPO_ROOT"
    if [ "$INSTR" = "VEL3D-data-collection" ]; then
        "$CONDA" run -n ooi_env python bin/temporal_anomaly_investigator.py --mode plot --all-series \
            || echo "WARN: summary plot failed"
    else
        "$CONDA" run -n ooi_env python bin/temporal_anomaly_investigator.py --mode plot \
            || echo "WARN: summary plot failed"
    fi
    "$CONDA" run -n ooi_env python bin/plot_dt_true_outliers.py || echo "WARN: outlier plot failed"
else
    echo "WARN: conda not found — figures not regenerated"
fi

cd "$CLONE"
git add "$INSTR/output/temporal_anomaly/metrics/" "$INSTR/run/" 2>/dev/null
git add -f "$INSTR"/output/temporal_anomaly/figures/summary/*.png \
           "$INSTR"/output/temporal_anomaly/figures/summary/*/*.png \
           "$INSTR"/output/temporal_anomaly/figures/summary/*/*/*.png 2>/dev/null
for d in output/metrics output/diagnostics; do
    [ -d "$INSTR/$d" ] && git add "$INSTR/$d/" 2>/dev/null
done

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
