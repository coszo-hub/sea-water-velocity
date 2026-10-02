#!/usr/bin/env bash
# cleanup_seedlink.sh — daily housekeeping for the near-real-time (SeedLink) VM.
#
# The VM's disk is small. Once ringserver's MSeedScan has loaded a MiniSEED
# file into the ring it no longer needs the file, so staged files older than
# KEEP_MSEED_DAYS are deleted. Also removes scratch inputs: M2M NetCDFs
# (VEL3D-C temperature) and gold-copy full-file downloads (OPeNDAP fallback).
#
# Overrides: KEEP_MSEED_DAYS (7), KEEP_NC_DAYS (7), KEEP_CACHE_DAYS (2), KEEP_LOG_DAYS (180),
#            KEEP_FIG_DAYS (90; skipped when per_day is a symlink, e.g. to the COSZO drive)

set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1
mkdir -p log
exec >> log/cleanup_seedlink.log 2>&1

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cleanup start ($REPO_ROOT)"
n_mseed=$(find output/mseed -maxdepth 1 -type f -name '*.mseed' -mtime +"${KEEP_MSEED_DAYS:-7}" -print -delete 2>/dev/null | wc -l)
n_nc=$(find output/temporal_anomaly/netcdf -type f -name '*.nc' -mtime +"${KEEP_NC_DAYS:-7}" -print -delete 2>/dev/null | wc -l)
n_cache=$(find -L output/goldcopy_cache -type f -mtime +"${KEEP_CACHE_DAYS:-2}" -print -delete 2>/dev/null | wc -l)
n_log=$(find log -type f -name '*.log' -mtime +"${KEEP_LOG_DAYS:-180}" -print -delete 2>/dev/null | wc -l)
# per-day timing figures (gap days only; not tracked in git) — VM copy only
n_fig=0
if [ -d output/temporal_anomaly/figures/per_day ] && [ ! -L output/temporal_anomaly/figures/per_day ]; then
    n_fig=$(find output/temporal_anomaly/figures/per_day -mindepth 1 -maxdepth 1 -type d -mtime +"${KEEP_FIG_DAYS:-90}" -print -exec rm -rf {} + 2>/dev/null | wc -l)
fi
echo "  removed: $n_mseed staged MiniSEED, $n_nc NetCDF, $n_cache cache files, $n_log old logs, $n_fig per-day figure dirs"
df -h "$REPO_ROOT" | tail -1 | awk '{print "  disk: " $4 " free (" $5 " used)"}'
