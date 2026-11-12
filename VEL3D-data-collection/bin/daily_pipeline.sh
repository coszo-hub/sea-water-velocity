#!/usr/bin/env bash
# daily_pipeline.sh — near-real-time (SeedLink) chain for this instrument folder.
# Works in either repo (VEL3D-data-collection / PREST-data-collection).
#
#   1. Every series in param/seedlink_series.txt: bin/run_daily_seedlink.sh
#      (OOI M2M; a day is processed only once it is complete in M2M) →
#      timing CSV row + per-day figure, MiniSEED into output/mseed_pending/.
#   2. If anything new: bin/sync_metrics.sh — regenerate the summary figures
#      (README "Timing summary"), commit CSVs + figures + cursors, push.
#   3. SeedLink send: move output/mseed_pending/*.mseed → output/mseed/, which
#      ringserver's MSeedScan loads into the ring for EarthScope. Done even if
#      the push failed (an email is sent then) — GitHub must not hold back data.
#
# Cron runs it hourly 01:00–12:00 UTC (crons_seedlink_vm.txt at the
# sea-water-velocity repo root), PREST then VEL3D under one flock so the two
# repos never overlap. Runs with nothing new exit after step 1.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1
INSTR="$(basename "$REPO_ROOT")"
SERIES_FILE="param/seedlink_series.txt"
CONDA="${CONDA:-$(command -v conda || true)}"

mkdir -p log run output/mseed output/mseed_pending
exec >> "log/daily_pipeline_$(date -u +%Y%m).log" 2>&1
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [$INSTR] $*"; }

# flock (not a lock dir): released automatically if the process dies or the VM reboots
exec 9> "run/.lock_daily_pipeline"
if ! flock -n 9; then
    log "another daily_pipeline run is active — exiting"
    exit 0
fi

notify() {   # subject body — via bin/mail.py (recipient set there)
    [[ -n "$CONDA" ]] || return 0
    # --no-capture-output: plain `conda run` does not pass stdin to python
    "$CONDA" run --no-capture-output -n ooi_env python - "$1" "$2" <<'EOF' || log "WARN: email failed"
import sys; sys.path.insert(0, "bin")
from mail import sendmail
sendmail(sys.argv[1], sys.argv[2])
EOF
}

csv_state() { cat output/temporal_anomaly/metrics/*_variability.csv 2>/dev/null | md5sum; }

log "start"
before="$(csv_state)"

# ---------- 1. produce every series (one after another) ----------
while read -r label refdes stream source _; do
    [[ -z "${label:-}" || "$label" == \#* ]] && continue
    # hard cap per series so one hung OOI request cannot block the chain
    timeout -k 5m "${SERIES_TIMEOUT:-90m}" bin/run_daily_seedlink.sh "$refdes" "$stream" "$source" \
        >> "log/seedlink_${label}_$(date -u +%Y%m).log" 2>&1 \
        || log "WARN: $label exited non-zero or timed out (see log/seedlink_${label}_*.log)"
done < "$SERIES_FILE"

n_pending=$(find output/mseed_pending -maxdepth 1 -type f -name '*.mseed' | wc -l | tr -d ' ')
# commits left unpushed by an earlier failed push also trigger a sync
n_unpushed=$(git -C .. rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [[ "$(csv_state)" == "$before" && "$n_pending" -eq 0 && "$n_unpushed" -eq 0 ]]; then
    log "nothing new — done"
    exit 0
fi
log "new data: $n_pending MiniSEED file(s) pending"

# ---------- 2. figures + CSVs → GitHub ----------
sync_start=0; [[ -f log/sync_metrics.log ]] && sync_start=$(wc -l < log/sync_metrics.log)
bin/sync_metrics.sh
sync_out="$(tail -n +"$((sync_start + 1))" log/sync_metrics.log 2>/dev/null)"
if grep -q "WARN" <<<"$sync_out"; then
    log "WARN: sync_metrics reported problems — emailing"
    notify "COSZO $INSTR: GitHub sync problem" "$(grep WARN <<<"$sync_out")"
fi
grep -E "pushed:|no changes" <<<"$sync_out" | sed "s/^/$(date -u +%Y-%m-%dT%H:%M:%SZ) [$INSTR] sync: /"

# ---------- 3. SeedLink send: release to the ringserver folder ----------
n_moved=0
while IFS= read -r -d '' f; do
    touch "$f"                                   # fresh mtime for MSeedScan
    mv -f "$f" output/mseed/ && n_moved=$((n_moved + 1))
done < <(find output/mseed_pending -maxdepth 1 -type f -name '*.mseed' -print0)
log "released $n_moved MiniSEED file(s) to output/mseed/ (SeedLink)"
log "done"
