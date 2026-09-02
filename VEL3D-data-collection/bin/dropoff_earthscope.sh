#!/usr/bin/env bash
# EarthScope Dropoff uploader — replaces the retired miniseed2dmc transfer path.
#
# Uploads staged VEL3D data to EarthScope's cloud Dropoff system via the
# `es` CLI (https://docs.earthscope.org/cli/). Runs from any clone of this
# repo: resolve the repo root from the script location, no hardcoded paths.
#
# One-time setup per machine:
#   pip install earthscope-cli        (already in bin/environment.yml ooi_env)
#   es login                          (device-code flow; tokens persist in
#                                      ~/.earthscope and auto-refresh)
#
# Pipeline wiring (historical backfill):
#   1. bin/backfill_mseed_from_nc.py       NetCDF -> output/mseed2dmc/<YEAR>/*.mseed
#   2. bin/dropoff_earthscope.sh mseed     stage -> EarthScope Dropoff (this script)
#   3. bin/dropoff_earthscope.sh status    watch server-side validation
# The Dropoff accepts the nested <YEAR>/ layout as-is (keys keep the relative
# path), so bin/flatten_mseed2dmc.sh is no longer needed for drop-off.
#
# Usage:
#   bin/dropoff_earthscope.sh mseed [--dry-run] [--archive]
#                    upload output/mseed2dmc/ (recursive, category miniseed);
#                    --archive moves uploaded files to output/mseed2dmc_sent/
#                    so the staging dir acts as a queue and re-runs are
#                    incremental
#   bin/dropoff_earthscope.sh xml   [--dry-run]    upload output/xml/OO_*.xml (category stationxml)
#   bin/dropoff_earthscope.sh status               summary of both vel3d/ prefixes
#   bin/dropoff_earthscope.sh list [prefix]        list uploaded objects (default prefix vel3d/)
#   bin/dropoff_earthscope.sh history <key>        upload history for one object key
#
# Destination layout in the dropoff space (override with DROPOFF_PREFIX):
#   vel3d/mseed/<staged relative path>      e.g. vel3d/mseed/2016/OO.CZSHF.20.MOU...mseed
#   vel3d/stationxml/OO_<STA>_<LOC>.xml
#
# Upload statuses progress RECEIVED -> VALIDATING -> VALIDATED -> AUTHORIZING
# -> AUTHORIZED -> ACCEPTED (or FAILED with a status_message). Check with the
# `status` / `list` / `history` subcommands; failures do not need re-staging,
# just fix and re-upload the same key.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MSEED_DIR="$REPO_ROOT/output/mseed2dmc"
XML_DIR="$REPO_ROOT/output/xml"
PREFIX="${DROPOFF_PREFIX:-vel3d}"
LOG_DIR="$REPO_ROOT/log_dropoff"
LOCK_FILE="$LOG_DIR/.dropoff.lock"

usage() { sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'; }

# ---------- Resolve the es CLI ----------
resolve_es() {
    if [[ -n "${ES_BIN:-}" ]]; then
        echo "$ES_BIN"
    elif command -v es >/dev/null 2>&1; then
        command -v es
    elif command -v conda >/dev/null 2>&1 \
            && conda run -n ooi_env es --version >/dev/null 2>&1; then
        echo "conda run --no-capture-output -n ooi_env es"
    else
        echo "FATAL: 'es' CLI not found. Install with: pip install earthscope-cli" >&2
        echo "       (or set ES_BIN=/path/to/es)" >&2
        exit 1
    fi
}

require_login() {
    # Cheap auth probe; guides the user to the one-time device-code login.
    if ! $ES user get-profile >/dev/null 2>&1; then
        echo "FATAL: not logged in to EarthScope. Run: $ES login" >&2
        exit 1
    fi
}

mode="${1:-}"
[[ -z "$mode" || "$mode" == "-h" || "$mode" == "--help" ]] && { usage; exit 0; }
shift

DRY_RUN=0
ARCHIVE=0
extra_args=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --archive) ARCHIVE=1 ;;
        *) extra_args+=("$arg") ;;
    esac
done
set -- "${extra_args[@]+"${extra_args[@]}"}"

ES="$(resolve_es)"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/dropoff_$(date -u +%Y%m).log"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG_FILE"; }

case "$mode" in
    mseed)
        [[ -d "$MSEED_DIR" ]] || { echo "FATAL: staging dir not found: $MSEED_DIR" >&2; exit 1; }
        n_files=$(find "$MSEED_DIR" -type f ! -name ".*" | wc -l | tr -d ' ')
        [[ "$n_files" -eq 0 ]] && { log "mseed: nothing staged in $MSEED_DIR — exiting"; exit 0; }
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "DRY RUN: would upload $n_files file(s) to ${PREFIX}/mseed/ (category miniseed):"
            find "$MSEED_DIR" -type f ! -name ".*" | sed "s|$MSEED_DIR/|  |"
            exit 0
        fi
        # Single-instance guard: two concurrent uploads of the same staging
        # tree would race on the same object keys.
        if ! mkdir "$LOCK_FILE" 2>/dev/null; then
            log "mseed: another dropoff run holds $LOCK_FILE — exiting"
            exit 1
        fi
        trap 'rmdir "$LOCK_FILE"' EXIT
        require_login
        # Manifest of what is being uploaded, captured BEFORE the upload:
        # --archive moves only these files, so anything staged mid-upload
        # stays queued for the next run.
        manifest="$(mktemp)"
        find "$MSEED_DIR" -type f ! -name ".*" > "$manifest"
        log "mseed: uploading $n_files file(s) from $MSEED_DIR to ${PREFIX}/mseed/"
        $ES dropoff upload -c miniseed -r -s "$MSEED_DIR/" -d "${PREFIX}/mseed/" 2>&1 | tee -a "$LOG_FILE"
        log "mseed: upload command finished; verify with: $0 status"
        if [[ $ARCHIVE -eq 1 ]]; then
            SENT_DIR="$REPO_ROOT/output/mseed2dmc_sent"
            moved=0
            while IFS= read -r f; do
                rel="${f#"$MSEED_DIR"/}"
                mkdir -p "$SENT_DIR/$(dirname "$rel")"
                mv "$f" "$SENT_DIR/$rel"
                moved=$((moved+1))
            done < "$manifest"
            log "mseed: archived $moved file(s) to $SENT_DIR (staging dir is now the pending queue)"
        fi
        rm -f "$manifest"
        ;;
    xml)
        shopt -s nullglob
        xmls=("$XML_DIR"/OO_*.xml)
        [[ ${#xmls[@]} -eq 0 ]] && { echo "FATAL: no OO_*.xml in $XML_DIR" >&2; exit 1; }
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "DRY RUN: would upload ${#xmls[@]} StationXML file(s) to ${PREFIX}/stationxml/ (category stationxml):"
            printf '  %s\n' "${xmls[@]##*/}"
            exit 0
        fi
        require_login
        log "xml: uploading ${#xmls[@]} StationXML file(s) to ${PREFIX}/stationxml/"
        src_args=()
        for f in "${xmls[@]}"; do src_args+=(-s "$f"); done
        $ES dropoff upload -c stationxml "${src_args[@]}" -d "${PREFIX}/stationxml/" 2>&1 | tee -a "$LOG_FILE"
        log "xml: upload command finished; verify with: $0 status"
        ;;
    status)
        require_login
        echo "== ${PREFIX}/mseed/ (miniseed) =="
        $ES dropoff get-summary -c miniseed --prefix "${PREFIX}/mseed/" || true
        echo "== ${PREFIX}/stationxml/ (stationxml) =="
        $ES dropoff get-summary -c stationxml --prefix "${PREFIX}/stationxml/" || true
        ;;
    list)
        require_login
        $ES dropoff list-objects --prefix "${1:-$PREFIX/}"
        ;;
    history)
        [[ -z "${1:-}" ]] && { echo "usage: $0 history <object-key>" >&2; exit 1; }
        require_login
        $ES dropoff get-object-history --key "$1"
        ;;
    *)
        echo "unknown mode: $mode" >&2
        usage
        exit 1
        ;;
esac
