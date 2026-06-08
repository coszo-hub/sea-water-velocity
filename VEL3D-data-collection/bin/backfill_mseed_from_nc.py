#!/usr/bin/env python3
"""
backfill_mseed_from_nc.py — historical MiniSEED backfill from locally saved NetCDFs.

Walks `output/temporal_anomaly/netcdf/` (populated by `temporal_anomaly_investigator
--save-nc`) and converts each (station × date) NetCDF to MiniSEED files under
`output/mseed2dmc/<YEAR>/`, matching the cron pipeline's gap-detection +
per-segment write logic.

This avoids re-hitting OOI for the historical range. The cron's responsibility
is then forward-only — it picks up where the backfill left off via the
endtime_*.txt files.

Outputs:
    output/mseed2dmc/<YEAR>/<NET>.<STA>.<LOC>.<CHA>.<start>-<end>.mseed
    output/metrics/<station>_<run>_pipeline_stats.csv  (optional, --append-stats)

Usage:
    python bin/backfill_mseed_from_nc.py --start 2014-09-14 --end 2026-03-01
    python bin/backfill_mseed_from_nc.py --start 2025-01-01 --end 2025-01-07 \\
        --station RS01SLBS-MJ01A-06-PRESTA101 --gap-algo anomaly --append-stats
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
from numpy.ma import MaskedArray
from obspy import UTCDateTime, Trace, Stream

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "bin"))

from read_param import read_param
from diagnose_timing import STATIONS, get_deployment_for_date
from plot_from_netcdf import find_nc_file, read_nc_day
from gap_algorithms import detect_gaps

PARAM_PATH       = os.path.join(REPO_ROOT, "param")
DEFAULT_NC_DIR   = os.path.join(REPO_ROOT, "output", "temporal_anomaly", "netcdf")
DEFAULT_MSEED    = os.path.join(REPO_ROOT, "output", "mseed2dmc")
DEFAULT_METRICS  = os.path.join(REPO_ROOT, "output", "metrics")

METRICS_FIELDS = [
    "date", "station", "run", "deployment",
    "algorithm", "algorithm_requested", "boundary_in_window",
    "n_points", "expected_npts", "is_full",
    "sp", "sr", "sp_nominal",
    "sp_deviation", "sp_deviation_alert_fired",
    "multiplier", "gap_threshold",
    "dt_true", "n_ideal", "true_missing",
    "n_gaps_raw", "jitter_unstable", "frac_maxabs",
    "n_gaps", "n_segments", "gap_total_missing_est",
]


def _daterange(start, end):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end,   "%Y-%m-%d")
    while s <= e:
        yield UTCDateTime(s.strftime("%Y-%m-%dT00:00:00Z"))
        s += timedelta(days=1)


def _load_existing_keys(csv_path):
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            keys.add((r["station"], r["date"]))
    return keys


def _append_metrics_row(csv_path, row):
    new_file = not os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=METRICS_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def _write_mseed_segments(fh, utc_trim, t_raw, start_idx, end_idx, station,
                          channels, datatypes, net_sta_param, run,
                          gap_result, mseed_dir):
    """One MiniSEED file per (channel × contiguous segment). Mirrors the cron
    pipeline's write block."""
    mseed_ext    = run.get("mseed_file_ext", [".mseed"])[0]
    data_quality = run.get("data_quality", ["D"])[0]
    rec_len      = int(run["rec_len"][0])
    sr           = gap_result.sr

    utc_str  = np.array([str(x) for x in utc_trim], dtype=object)
    segments = np.split(utc_str, gap_result.segment_splits)
    ref_under = station.replace("-", "_")

    written = 0
    for cha_key in channels:
        data_var = datatypes[cha_key]
        if data_var not in fh.variables:
            print(f"      skip {cha_key} — variable '{data_var}' missing")
            continue

        data_full = fh.variables[data_var][:]
        data_day  = data_full[start_idx:end_idx]
        if isinstance(data_day, MaskedArray):
            data_day = data_day.filled(np.nan)

        chan_file     = os.path.join(PARAM_PATH, f"{ref_under}_{cha_key}.txt")
        channel_param = read_param(chan_file)
        r_value       = float(channel_param["r_value"][0])

        cursor = 0
        for ti in segments:
            n_seg = len(ti)
            if n_seg == 0:
                continue
            seg_start = ti[0]
            seg_end   = ti[-1]
            seg_data  = data_day[cursor:cursor + n_seg] / r_value
            cursor   += n_seg

            stats = {
                "network":       net_sta_param["net"][0],
                "station":       net_sta_param["sta"][0],
                "location":      channel_param["c_loc"][0],
                "channel":       channel_param["cha"][0],
                "npts":          n_seg,
                "sampling_rate": sr,
                "starttime":     UTCDateTime(seg_start),
                "mseed":         {"dataquality": data_quality},
            }
            tr = Trace(data=np.asarray(seg_data, dtype=float), header=stats)
            st = Stream([tr])

            mseed_name = (
                f"{stats['network']}.{stats['station']}.{stats['location']}."
                f"{stats['channel']}."
                f"{seg_start[0:4]}."
                f"{time.strptime(seg_start[0:10], '%Y-%m-%d').tm_yday:03d}."
                f"{seg_start[11:23].replace(':', '.')}-"
                f"{seg_end[0:4]}."
                f"{time.strptime(seg_end[0:10], '%Y-%m-%d').tm_yday:03d}."
                f"{seg_end[11:23].replace(':', '.')}{mseed_ext}"
            )
            year_dir = os.path.join(mseed_dir, seg_start[0:4])
            os.makedirs(year_dir, exist_ok=True)
            write_path = os.path.join(year_dir, mseed_name)
            st.write(write_path, format="MSEED", reclen=rec_len)
            written += 1

    return written


def process_day(station, date, run, gap_algo, nc_dir, mseed_dir,
                metrics_csv, append_stats, skip_existing):
    date_str = str(date)[:10]
    gap_algo_requested = gap_algo

    nc_path = find_nc_file(nc_dir, station, date_str)
    if nc_path is None:
        print(f"  [{station}] {date_str}  skip — no NetCDF")
        return False

    if append_stats and skip_existing:
        existing = _load_existing_keys(metrics_csv)
        if (station, date_str) in existing:
            print(f"  [{station}] {date_str}  skip — already in metrics CSV")
            return False

    try:
        dep_info = get_deployment_for_date(station, date, PARAM_PATH)
    except ValueError as e:
        print(f"  [{station}] {date_str}  skip — {e}")
        return False
    deployment = dep_info["deployment"]
    sp_nominal = dep_info["sp_nominal"]

    ref_under     = station.replace("-", "_")
    sta_param     = read_param(os.path.join(PARAM_PATH, f"{ref_under}.txt"))
    chan_raw      = sta_param.get(f"channels_{deployment}",
                                  sta_param.get("channels"))[0]
    channels      = [c.strip() for c in chan_raw.strip("[]").split(",")]
    dt_raw        = sta_param.get(f"data_types_{deployment}",
                                  sta_param.get("data_types"))[0]
    import ast
    datatypes     = dt_raw if isinstance(dt_raw, dict) else ast.literal_eval(dt_raw)

    # Deployment-boundary check (parity with cron pipeline). If this
    # deployment's c_end falls inside the calendar day window, the data
    # crosses a rate change. Anomaly OLS on mixed-rate data produces
    # garbage Δt_true, so fall back to legacy for that one day.
    boundary_in_window = False
    first_chan = next((c for c in channels if "DO" in c), channels[0])
    chan_param_first = read_param(
        os.path.join(PARAM_PATH, f"{ref_under}_{first_chan}.txt"))
    c_end_raw = chan_param_first.get("c_end", [None])[0]
    if c_end_raw and str(c_end_raw).strip().lower() not in ("none", "null", ""):
        try:
            window_start = date
            window_end   = date + 86400.0
            c_end_dt     = UTCDateTime(str(c_end_raw).strip())
            if window_start < c_end_dt < window_end:
                boundary_in_window = True
                if gap_algo == "anomaly":
                    print(f"  [{station}] {date_str}  WARNING: deployment "
                          f"boundary at {c_end_dt} inside window — falling "
                          f"back to legacy")
                    gap_algo = "legacy"
        except Exception as e_b:
            print(f"  [{station}] {date_str}  boundary parse failed (non-fatal): {e_b}")

    fh, utc_trim, t_raw, start_idx, end_idx = read_nc_day(nc_path, date, run)
    if len(utc_trim) < 2:
        print(f"  [{station}] {date_str}  skip — only {len(utc_trim)} samples")
        fh.close()
        return False

    try:
        t_sec = t_raw - float(t_raw[0])
        gap_result = detect_gaps(gap_algo, t_sec, sp_nominal=sp_nominal,
                                 req_duration=86400.0)

        n_written = _write_mseed_segments(
            fh, utc_trim, t_raw, start_idx, end_idx, station,
            channels, datatypes, sta_param, run, gap_result, mseed_dir)

        actual_algo = gap_result.diagnostics.get("algorithm", gap_algo)
        print(f"  [{station}] {date_str}  algo={actual_algo}  "
              f"n={len(utc_trim)}  segs={gap_result.n_segments}  "
              f"gaps={gap_result.n_gaps}  → {n_written} mseed files")

        if append_stats:
            diag = gap_result.diagnostics
            if "true_missing" in diag:
                gap_total_missing_est = int(diag["true_missing"])
            else:
                gap_total_missing_est = int(diag.get("gap_total_missing_est", 0))
            sp_dev = (abs(gap_result.sp - sp_nominal)
                      if sp_nominal and gap_result.sp and np.isfinite(gap_result.sp)
                      else None)
            _append_metrics_row(metrics_csv, {
                "date":                     date_str,
                "station":                  station,
                "run":                      "prest",
                "deployment":               deployment,
                "algorithm":                actual_algo,
                "algorithm_requested":      gap_algo_requested,
                "boundary_in_window":       bool(boundary_in_window),
                "n_points":                 int(len(utc_trim)),
                "expected_npts":            int(diag.get("expected_npts", "")) if "expected_npts" in diag else "",
                "is_full":                  bool(gap_result.is_full),
                "sp":                       round(float(gap_result.sp), 9) if gap_result.sp else "",
                "sr":                       round(float(gap_result.sr), 9) if gap_result.sr else "",
                "sp_nominal":               round(float(sp_nominal), 6) if sp_nominal else "",
                "sp_deviation":             round(float(sp_dev), 9) if sp_dev is not None else "",
                "sp_deviation_alert_fired": "",     # backfill doesn't trigger emails
                "multiplier":               round(float(diag["multiplier"]), 3) if "multiplier" in diag else "",
                "gap_threshold":            round(float(diag["gap_threshold"]), 6) if "gap_threshold" in diag else "",
                "dt_true":                  repr(diag["dt_true"]) if "dt_true" in diag else "",
                "n_ideal":                  int(diag["n_ideal"]) if "n_ideal" in diag else "",
                "true_missing":             int(diag["true_missing"]) if "true_missing" in diag else "",
                "n_gaps_raw":               int(diag["n_gaps_raw"]) if "n_gaps_raw" in diag else "",
                "jitter_unstable":          bool(diag["jitter_unstable"]) if "jitter_unstable" in diag else "",
                "frac_maxabs":              round(float(diag["frac_maxabs"]), 6) if "frac_maxabs" in diag else "",
                "n_gaps":                   int(gap_result.n_gaps),
                "n_segments":               int(gap_result.n_segments),
                "gap_total_missing_est":    gap_total_missing_est,
            })
        return True
    finally:
        fh.close()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date",  help="Single date YYYY-MM-DD")
    g.add_argument("--start", help="Start date YYYY-MM-DD (use with --end)")
    p.add_argument("--end", help="End date YYYY-MM-DD")
    p.add_argument("--station", action="append",
                   help="Restrict to a station (repeat for multiple). "
                        "Default: all 3 PREST stations.")
    p.add_argument("--gap-algo", choices=["legacy", "anomaly"], default="anomaly",
                   help="Gap detection algorithm. Default: anomaly "
                        "(matches param/run_prest.txt as of 2026-04-29).")
    p.add_argument("--nc-dir",     default=DEFAULT_NC_DIR)
    p.add_argument("--mseed-dir",  default=DEFAULT_MSEED,
                   help=f"MiniSEED output root (default: {DEFAULT_MSEED})")
    p.add_argument("--metrics-dir", default=DEFAULT_METRICS,
                   help=f"Per-day stats CSV directory (default: {DEFAULT_METRICS})")
    p.add_argument("--append-stats", action="store_true",
                   help="Also append per-day stats rows to "
                        "<metrics-dir>/<station>_prest_pipeline_stats.csv")
    p.add_argument("--no-skip", action="store_true",
                   help="Don't skip days that already have a metrics CSV row "
                        "(only meaningful with --append-stats).")
    args = p.parse_args()

    if args.start and not args.end:
        p.error("--start requires --end")

    if args.date:
        dates = [UTCDateTime(args.date + "T00:00:00Z")]
    else:
        dates = list(_daterange(args.start, args.end))

    stations = args.station if args.station else STATIONS
    run = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))

    n_done = n_skipped = 0
    for station in stations:
        metrics_csv = os.path.join(args.metrics_dir,
                                   f"{station}_prest_pipeline_stats.csv")
        for date in dates:
            ok = process_day(station, date, run, args.gap_algo,
                             args.nc_dir, args.mseed_dir,
                             metrics_csv, args.append_stats,
                             skip_existing=not args.no_skip)
            if ok:
                n_done += 1
            else:
                n_skipped += 1

    print(f"\nDone. Converted: {n_done}  Skipped: {n_skipped}")


if __name__ == "__main__":
    main()
