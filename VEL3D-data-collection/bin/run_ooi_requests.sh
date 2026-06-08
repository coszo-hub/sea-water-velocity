#!/bin/bash
set -euo pipefail

# ---------- Read args early so we can route logging ----------
transfer_method="${3:-seedlink}"  # default to seedlink if not provided

# ---------- Configuration ----------
# VM paths (uncomment for Kozo VM):
# CONDA="/home/coszo/miniconda3/bin/conda"
# if [[ "$transfer_method" == "miniseed2dmc" ]]; then
#   LOG_DIR="/home/coszo/coszo-data-collection/log_mseed2dmc"
# else
#   LOG_DIR="/home/coszo/coszo-data-collection/log"
# fi

# Local paths:
CONDA="$(conda info --base 2>/dev/null)/bin/conda"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$transfer_method" == "miniseed2dmc" ]]; then
  LOG_DIR="$REPO_ROOT/log_mseed2dmc"
else
  LOG_DIR="$REPO_ROOT/log"
fi
LOG_FILE="$LOG_DIR/run_wrapper.log"

# ---------- Logging ----------
mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1
# date --iso-8601=seconds  # Linux only
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) starting wrapper ====="

# ---------- Resolve repo root ----------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "REPO_ROOT=$REPO_ROOT"

# ---------- Load credentials ----------
ENV_FILE="$REPO_ROOT/.ooi_env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "FATAL: credentials file not found: $ENV_FILE"
    exit 1
fi
umask 077
source "$ENV_FILE"
echo "Credentials loaded"

# ---------- Sanity check conda ----------
if [[ ! -x "$CONDA" ]]; then
    echo "FATAL: conda executable not found: $CONDA"
    exit 1
fi
"$CONDA" --version

# ... (keep everything above unchanged)

echo "Conda version OK"
echo "Executing in conda env: ooi_env"
echo "Full command: $REPO_ROOT/bin/run_data_collection.sh $@"
echo "Launching conda run NOW - if you see this but nothing after, conda run failed silently"

exec "$CONDA" run --no-capture-output -n ooi_env /bin/bash -c '
    echo "===== INSIDE CONDA ENV ====="
    echo "Inside env - pwd=$(pwd)"
    echo "Inside env - PATH=$PATH"
    echo "Arguments received inside env: $# → \"\$1\" \"\$2\""

    script_path="'"$REPO_ROOT/bin/run_data_collection.sh"'"
    echo "Executing: $script_path \"\$@\""

    exec "$script_path" "$@"
' _ "$@"