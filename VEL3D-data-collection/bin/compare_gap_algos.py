#!/usr/bin/env python3
"""
compare_gap_algos.py — Phase 0b side-by-side comparison harness.

Runs both gap-detection algorithms against locally saved NetCDFs and emits a
per-(station, date) diff CSV so disagreements between legacy median-Δt + adaptive
threshold and the anomaly OLS Δt_true + true_missing approach are visible at a
glance.

No pipeline change. No OOI calls. Reads only from
output/temporal_anomaly/netcdf/ — populated by
`temporal_anomaly_investigator.py --save-nc`.

Output:
    output/temporal_anomaly/algo_comparison/<station>.csv

Usage:
    python bin/compare_gap_algos.py --start 2025-01-22 --end 2025-01-26
    python bin/compare_gap_algos.py --date 2025-01-24 --station RS01SLBS-MJ01A-06-PRESTA101
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timedelta

import numpy as np
from obspy import UTCDateTime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "bin"))

from read_param import read_param
from diagnose_timing import STATIONS, get_deployment_for_date
from plot_from_netcdf import find_nc_file, read_nc_day
from gap_algorithms import detect_gaps_legacy, detect_gaps_anomaly

PARAM_PATH = os.path.join(REPO_ROOT, "param")
DEFAULT_NC_DIR = os.path.join(REPO_ROOT, "output", "temporal_anomaly", "netcdf")
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "output", "temporal_anomaly", "algo_comparison")

CSV_FIELDS = [
    "station", "date", "deployment", "sp_nominal",
    "n_points",
    # Legacy
    "legacy_sp", "legacy_sr", "legacy_multiplier", "legacy_gap_threshold",
    "legacy_n_gaps", "legacy_n_segments", "legacy_is_full",
    # Anomaly
    "anomaly_dt_true", "anomaly_sr",
    "anomaly_n_gaps_raw", "anomaly_true_missing",
    "anomaly_n_gaps_corrected", "anomaly_n_segments",
    "anomaly_jitter_unstable", "anomaly_frac_maxabs",
    # Diff
    "sp_minus_dt_true", "n_segments_diff", "n_gaps_diff",
    "agreement",
]


def _daterange(start, end):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end,   "%Y-%m-%d")
    while s <= e:
        yield UTCDateTime(s.strftime("%Y-%m-%dT00:00:00Z"))
        s += timedelta(days=1)


def _row_for_day(station, date, run, nc_dir):
    """Return a dict for the CSV, or None if no NetCDF is available."""
    date_str = str(date)[:10]

    nc_path = find_nc_file(nc_dir, station, date_str)
    if not nc_path:
        return None

    try:
        dep_info = get_deployment_for_date(station, date, PARAM_PATH)
    except ValueError:
        return None
    deployment = dep_info["deployment"]
    sp_nominal = dep_info["sp_nominal"]

    fh, utc_trim, t_raw, _, _ = read_nc_day(nc_path, date, run)
    fh.close()

    if len(utc_trim) < 2:
        return None

    # Use the same algorithms the live pipeline uses (bin/gap_algorithms.py)
    t_sec = t_raw - float(t_raw[0])
    leg = detect_gaps_legacy(t_sec, sp_nominal)   # offline: req_duration = data span
    ano = detect_gaps_anomaly(t_sec, sp_nominal)

    n_segments_diff = ano.n_segments - leg.n_segments
    n_gaps_diff     = int(ano.n_gaps) - int(leg.n_gaps)
    agreement = "match" if (n_segments_diff == 0 and n_gaps_diff == 0) else "differ"

    return {
        "station":    station,
        "date":       date_str,
        "deployment": deployment,
        "sp_nominal": round(sp_nominal, 6),
        "n_points":   int(len(t_sec)),
        # Legacy
        "legacy_sp":            round(leg.sp, 9),
        "legacy_sr":            round(leg.sr, 9),
        "legacy_multiplier":    round(leg.diagnostics["multiplier"], 3),
        "legacy_gap_threshold": round(leg.diagnostics["gap_threshold"], 6),
        "legacy_n_gaps":        int(leg.n_gaps),
        "legacy_n_segments":    int(leg.n_segments),
        "legacy_is_full":       bool(leg.is_full),
        # Anomaly
        "anomaly_dt_true":          repr(ano.diagnostics["dt_true"]),
        "anomaly_sr":               repr(ano.sr),
        "anomaly_n_gaps_raw":       int(ano.diagnostics["n_gaps_raw"]),
        "anomaly_true_missing":     int(ano.diagnostics["true_missing"]),
        "anomaly_n_gaps_corrected": int(ano.n_gaps),
        "anomaly_n_segments":       int(ano.n_segments),
        "anomaly_jitter_unstable":  bool(ano.diagnostics["jitter_unstable"]),
        "anomaly_frac_maxabs":      round(ano.diagnostics["frac_maxabs"], 6),
        # Diff
        "sp_minus_dt_true": round(leg.sp - ano.diagnostics["dt_true"], 9),
        "n_segments_diff":  n_segments_diff,
        "n_gaps_diff":      n_gaps_diff,
        "agreement":        agreement,
    }


def _load_existing_keys(csv_path):
    """Return set of (station, date) already present in the CSV for idempotency."""
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            keys.add((r["station"], r["date"]))
    return keys


def _append_row(csv_path, row):
    new_file = not os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="Single date YYYY-MM-DD")
    g.add_argument("--start", help="Start date YYYY-MM-DD (use with --end)")
    p.add_argument("--end", help="End date YYYY-MM-DD")
    p.add_argument("--station", action="append",
                   help="Restrict to a station (repeat for multiple). "
                        "Default: all 3 PREST stations.")
    p.add_argument("--nc-dir",  default=DEFAULT_NC_DIR)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--force", action="store_true",
                   help="Re-run even if (station, date) already in output CSV.")
    args = p.parse_args()

    if args.start and not args.end:
        p.error("--start requires --end")

    if args.date:
        dates = [UTCDateTime(args.date + "T00:00:00Z")]
    else:
        dates = list(_daterange(args.start, args.end))

    stations = args.station if args.station else STATIONS

    run = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))

    n_processed = n_skipped_present = n_no_nc = n_match = n_differ = 0

    for station in stations:
        csv_path = os.path.join(args.out_dir, f"{station}.csv")
        existing = _load_existing_keys(csv_path) if not args.force else set()

        for date in dates:
            date_str = str(date)[:10]
            if (station, date_str) in existing:
                n_skipped_present += 1
                continue

            row = _row_for_day(station, date, run, args.nc_dir)
            if row is None:
                n_no_nc += 1
                continue

            _append_row(csv_path, row)
            n_processed += 1
            if row["agreement"] == "match":
                n_match += 1
            else:
                n_differ += 1
                print(f"  DIFFER  {station}  {date_str}  "
                      f"legacy_gaps={row['legacy_n_gaps']} segs={row['legacy_n_segments']}  "
                      f"anomaly_gaps={row['anomaly_n_gaps_corrected']} segs={row['anomaly_n_segments']}  "
                      f"true_missing={row['anomaly_true_missing']}")

    print(f"\nProcessed: {n_processed}  match: {n_match}  differ: {n_differ}  "
          f"no-nc: {n_no_nc}  already-present: {n_skipped_present}")


if __name__ == "__main__":
    main()
