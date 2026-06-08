#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

# Modified by Mika Thompson

# Get args.
script="$0"
reference_name_dash="${1:-}"
run_name="${2:-}"
transfer_method="${3:-}"

if [[ -z "$reference_name_dash" || -z "$run_name" || -z "$transfer_method" ]]; then
  echo "Usage: $script <reference_name_dash> <run_name> <transfer_method> " >&2
  exit 2
fi

# Args 'all' runs metadata.
if [[ "$reference_name_dash" == "all" && "$run_name" == "all" ]]; then
  process="OOI_metadata.py"
else
  process="OOI_data_request_and_convert_mseed.py"
fi

# Paths
# VM paths (uncomment for Kozo VM):
# homeDir="/home/coszo/coszo-data-collection"  # MT
# rootDir="$homeDir"                            # MT
# outputDir="$homeDir"                          # MT

# Local paths:
homeDir="$(cd "$(dirname "$0")/.." && pwd)"
rootDir="$homeDir"
outputDir="$homeDir"
binDir="$rootDir/bin"

# Validate method 
if [[ "$transfer_method" != "seedlink" && "$transfer_method" != "miniseed2dmc" ]]; then
    echo "ERROR: transfer_method must be 'seedlink' or 'miniseed2dmc' (got '$transfer_method')" >&2
    exit 2
fi

# Choose log directory based on transfer_method
if [[ "$transfer_method" == "miniseed2dmc" ]]; then
    logDir="$outputDir/log_mseed2dmc"
else
    # Default for seedlink 
    logDir="$outputDir/log"
fi

today="$(/bin/date -u +%F)"
log="/tmp/script-${reference_name_dash}-${run_name}.log"  # MT
operator="seismic@uw.edu"                                         # MK

logFile="${logDir}/${process}-${reference_name_dash}-${run_name}-${today}.log"  # MT
echo "$logFile"

# Only one process should run.
date > "$log"
echo "$script" >> "$log"

# Count matching running processes:
# Use pgrep -f for robustness; fall back to ps|grep if pgrep not available.
count=0
if command -v pgrep >/dev/null 2>&1; then
  count="$(pgrep -f -- "${binDir}/${process} ${reference_name_dash} ${run_name}" || true | wc -l)"
else
  count="$(ps -fu "${USER:-$(id -un)}" | grep -F -- "${binDir}/${process} ${reference_name_dash} ${run_name}" | grep -v grep | wc -l)"
fi

echo "$script $reference_name_dash $run_name" >> "$log"
echo "$count" >> "$log"

if [[ "$count" -ge 1 ]]; then
  {
    echo
    echo "$today"
    echo "another instance of $process $reference_name_dash $run_name is already running!"
    echo
  } >> "$log"

  # Send mail (mailx or s-nail). If not present, log a message.
  if command -v mailx >/dev/null 2>&1; then
    /bin/mailx -s "OOI INFO: $process $reference_name_dash $run_name is still running!" "$operator" < "$log"
  elif command -v nail >/dev/null 2>&1; then
    nail -s "OOI INFO: $process $reference_name_dash $run_name is still running!" "$operator" < "$log"
  else
    echo "mailx/nail not found; skipping email notification." >> "$log"
  fi
else
  echo "run ${binDir}/${process} on ${reference_name_dash} ${run_name} - output to ${logFile}"
  # Ensure log directory exists
  mkdir -p "$logDir"

  # Execute the Python process with arguments, append to logFile
  # Use /usr/bin/env python3 to be robust across environments
  if [[ "$process" == *.py ]]; then
    # VM path (uncomment for Kozo VM):
    # /home/coszo/miniconda3/envs/ooi_env/bin/python "${binDir}/${process}" "$reference_name_dash" "$run_name" "$transfer_method" >> "$logFile" 2>&1
    # Local path:
    conda run -n ooi_env python "${binDir}/${process}" "$reference_name_dash" "$run_name" "$transfer_method" >> "$logFile" 2>&1
  else
    # If process is an executable script/binary
    "${binDir}/${process}" "$reference_name_dash" "$run_name" >> "$logFile" 2>&1
  fi
fi
