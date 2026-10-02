#!/usr/bin/env bash
# run_daily_seedlink.sh — daily near-real-time (SeedLink) job for one station/stream.
#
# Produces MiniSEED for every day from the station's cursor up to yesterday
# (UTC) with the SAME code and rules as the historical backfill
# (bin/backfill_mseed_from_nc.py: timing segmenting, excess-record cleanup,
# 60 s minimum trace, timing-CSV row per day), written flat into
# output/mseed/ — the folder ringserver's MSeedScan watches and serves over
# SeedLink.
#
# Usage (from cron):
#   bin/run_daily_seedlink.sh <REFDES> <STREAM|-> <goldcopy|m2m>
#     REFDES   OOI reference designator, dashes (e.g. RS01SUM1-LJ01B-12-VEL3DB104)
#     STREAM   OOI stream, or "-" for single-stream instruments (PREST)
#     SOURCE   goldcopy (pre-built OOI gold copy; velocity, PREST)
#              m2m      (OOI async request per day; VEL3D-C temperature,
#                        which the gold copy lacks)
#
# Cursor: run/endtime_<REFDES>_<STREAM>.txt (or _<run>.txt) holds the next day to
# produce (UTC midnight). Each run re-checks the last LOOKBACK_DAYS before the
# cursor too; days already in the timing CSV are skipped, so a day that
# failed (OOI hiccup) is retried automatically on the next run. The cursor is
# advanced to the first day still missing (or to today when all are done), so
# bin/detect.py's "stale endtime file" alert fires if the job stops progressing.
#
# After each run bin/daily_alerts.py emails (via bin/mail.py) only if something needs
# attention: crash, failed/no-data days, rate deviation, OOI excess records, heavy
# fragmentation, deployment ending or a new OOI deployment missing from the params.
#
# Environment overrides: LOOKBACK_DAYS (default 7), CONDA (conda executable),
# CONDA_ENV (default ooi_env).

set -uo pipefail

REFDES="${1:?usage: run_daily_seedlink.sh <REFDES> <STREAM|-> <goldcopy|m2m>}"
STREAM="${2:?missing STREAM (use - for single-stream instruments)}"
SOURCE="${3:?missing SOURCE (goldcopy|m2m)}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-7}"
CONDA_ENV="${CONDA_ENV:-ooi_env}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
# run name from the instrument folder: VEL3D-data-collection → vel3d, PREST-… → prest
RUN_NAME="$(basename "$REPO_ROOT" | cut -d- -f1 | tr '[:upper:]' '[:lower:]')"

# cursor/lock tag: <REFDES>_<stream>, or <REFDES>_<run> for single-stream instruments
if [[ "$STREAM" != "-" ]]; then TAG="${REFDES}_${STREAM}"; else TAG="${REFDES}_${RUN_NAME}"; fi
CURSOR="run/endtime_${TAG}.txt"
LOCK="run/.lock_${TAG}"
mkdir -p log run output/mseed output/temporal_anomaly/netcdf

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [$TAG] $*"; }

# ---------- single instance per station/stream ----------
if ! mkdir "$LOCK" 2>/dev/null; then
    log "another run holds $LOCK — exiting"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# ---------- conda ----------
CONDA="${CONDA:-$(command -v conda || true)}"
if [[ -z "$CONDA" || ! -x "$CONDA" ]]; then
    log "FATAL: conda not found (set CONDA=/path/to/conda)"
    exit 1
fi
PY=("$CONDA" run --no-capture-output -n "$CONDA_ENV" python -u)

if [[ ! -f .ooi_env ]]; then
    log "FATAL: $REPO_ROOT/.ooi_env (OOI credentials) not found"
    exit 1
fi

# ---------- date window ----------
if [[ ! -f "$CURSOR" ]]; then
    log "FATAL: cursor $CURSOR missing — seed it with the first day to produce (e.g. 2026-10-01T00:00:00.000000Z)"
    exit 1
fi
read -r START END < <("${PY[@]}" - "$CURSOR" "$LOOKBACK_DAYS" <<'EOF'
import sys, datetime as dt
cur = dt.date.fromisoformat(open(sys.argv[1]).read().strip()[:10])
start = cur - dt.timedelta(days=int(sys.argv[2]))
yesterday = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
print(start.isoformat(), yesterday.isoformat())
EOF
)
if [[ -z "${START:-}" || "$START" > "$END" ]]; then
    log "nothing to do (window ${START:-?} → ${END:-?})"
    exit 0
fi
log "producing $START → $END from $SOURCE"

STREAM_ARGS=()
[[ "$STREAM" != "-" ]] && STREAM_ARGS=(--stream "$STREAM")

RUN_LOG="log/.last_run_${TAG}.log"
"${PY[@]}" bin/backfill_mseed_from_nc.py \
    --source "$SOURCE" --variability --min-piece-seconds 60 --flat --recent-retry-days 3 \
    --mseed-dir output/mseed --nc-dir output/temporal_anomaly/netcdf \
    --station "$REFDES" ${STREAM_ARGS[@]+"${STREAM_ARGS[@]}"} --start "$START" --end "$END" 2>&1 | tee "$RUN_LOG"
rc=${PIPESTATUS[0]}

# ---------- email alerts (one email per job, only when something needs attention) ----------
"${PY[@]}" bin/daily_alerts.py "$REFDES" "$STREAM" "$START" "$END" "$RUN_LOG" "$rc" \
    || log "WARN: alert step failed"

# ---------- advance cursor to the first day still missing ----------
"${PY[@]}" - "$REFDES" "$STREAM" "$START" "$END" "$CURSOR" <<'EOF'
import sys, os, datetime as dt
sys.path.insert(0, "bin")
from temporal_anomaly_investigator import _metrics_csv_path, _load_existing_keys
station, stream, start, end, cursor = sys.argv[1:6]
done = {d for s, d in _load_existing_keys(_metrics_csv_path(station, None if stream == "-" else stream))}
day, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
while day <= last and day.isoformat() in done:
    day += dt.timedelta(days=1)
old = open(cursor).read().strip()[:10]
new = max(day, dt.date.fromisoformat(old)) if day > last else day
if new.isoformat() != old:                 # rewrite only on change: detect.py
    with open(cursor, "w") as f:           # alerts on a cursor file that goes stale
        f.write(f"{new.isoformat()}T00:00:00.000000Z")
print(f"cursor {old} → {new.isoformat()}" + ("" if day > last else f"  (first missing day: {day})"))
EOF

log "done (backfill exit $rc)"
exit $rc
