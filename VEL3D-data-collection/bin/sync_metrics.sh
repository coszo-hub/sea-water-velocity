#!/usr/bin/env bash
# sync_metrics.sh — daily git push of the near-real-time QC outputs, so the
# GitHub front page (README "Timing summary") stays current.
# Called by bin/daily_pipeline.sh after the series have run (before the SeedLink release).
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

# (git pull happens after the local commit below, so a conflict can never
#  leave stash markers inside the CSVs.)

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
# summary PNGs are *.png-ignored → force-add. One git add with an unmatched
# pattern adds nothing at all, so expand with nullglob first.
shopt -s nullglob
pngs=("$INSTR"/output/temporal_anomaly/figures/summary/*.png
      "$INSTR"/output/temporal_anomaly/figures/summary/*/*.png
      "$INSTR"/output/temporal_anomaly/figures/summary/*/*/*.png)
shopt -u nullglob
[ ${#pngs[@]} -gt 0 ] && git add -f -- "${pngs[@]}"
for d in output/metrics output/diagnostics; do
    [ -d "$INSTR/$d" ] && git add "$INSTR/$d/" 2>/dev/null
done

commit_msg="metrics: sync $(date -u +%Y-%m-%d)"
if git diff --cached --quiet; then
    echo "  no new changes to commit"
elif ! git commit -q -m "$commit_msg"; then
    echo "WARN: git commit failed"
fi
# Rebase local commits (incl. any earlier unpushed ones) onto origin, then push.
if git fetch -q origin main && git rebase -q --autostash origin/main; then
    if [ "$(git rev-list --count origin/main..HEAD)" -gt 0 ]; then
        if git push -q origin HEAD:main; then
            echo "  pushed: $(git log -1 --format=%s)"
        else
            echo "WARN: git push failed; will retry next run"
        fi
    else
        echo "  no changes — nothing to push"
    fi
else
    git rebase --abort 2>/dev/null
    echo "WARN: git fetch/rebase onto origin/main failed (conflict?) — not pushed; fix by hand"
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  sync_metrics end"
