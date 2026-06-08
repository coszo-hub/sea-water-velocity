#!/usr/bin/env python3
"""
plot_from_netcdf.py
═══════════════════════════════════════════════════════════════════════════════
Offline variability plotter (and optional MiniSEED conversion) for NetCDF
files previously saved by `temporal_anomaly_investigator.py --save-nc`.

Uses the *same* 4-panel variability figure the investigator produces — it just
reads timestamps from the local NetCDF instead of re-fetching from OOI. No CSV
rows are read or written; this script leaves the collect-mode metrics files
alone.

Input
─────
NetCDF files at `output/temporal_anomaly/netcdf/` (default) with the naming
pattern written by the investigator:

    <station-ref>_<YYYY-MM-DD>_deployment<NNNN>.nc

Modes
─────
1. Variability figure (default): the investigator's 4-panel figure + stats
   txt written to
   `output/temporal_anomaly/figures_offline/<station>_<date>/`
   (separate directory so nothing from `collect` mode is overwritten).
2. Optional MiniSEED conversion (`--convert-mseed`): applies the same
   sample-period estimation, adaptive gap threshold, and per-segment
   splitting as `OOI_data_request_and_convert_mseed.py`, then writes one
   MiniSEED file per (channel × contiguous segment) to
   `output/temporal_anomaly/mseed/`.

Usage
─────
    # one date, all stations that have a saved .nc for it
    python bin/plot_from_netcdf.py --date 2019-01-15

    # date range, one station, also convert to MiniSEED
    python bin/plot_from_netcdf.py --start 2019-01-01 --end 2019-01-07 \\
        --station RS01SLBS-MJ01A-06-PRESTA101 --convert-mseed
"""

import os
import sys
import ast
import glob
import time
import argparse
import datetime

import numpy as np
from numpy.ma import MaskedArray
from netCDF4 import Dataset
from obspy import UTCDateTime, Stream, Trace

import matplotlib
matplotlib.use("Agg")

# ── Paths & shared helpers ──────────────────────────────────────────────────
REPO_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_PATH   = os.path.join(REPO_ROOT, "bin")
PARAM_PATH = os.path.join(REPO_ROOT, "param")
OUT_ROOT   = os.path.join(REPO_ROOT, "output", "temporal_anomaly")
DEFAULT_NC_DIR    = os.path.join(OUT_ROOT, "netcdf")
DEFAULT_PLOT_DIR  = os.path.join(OUT_ROOT, "figures_offline")
DEFAULT_MSEED_DIR = os.path.join(OUT_ROOT, "mseed")

sys.path.insert(0, BIN_PATH)

from read_param import read_param
from convert_utc import utcdata1900
from diagnose_timing import STATIONS, get_deployment_for_date
from temporal_anomaly_investigator import (
    compute_variability,
    make_per_day_figure,
    write_stats,
)


# ════════════════════════════════════════════════════════════════════════════
# NetCDF discovery
# ════════════════════════════════════════════════════════════════════════════
def find_nc_file(nc_dir, station, date_str):
    """Locate the saved NetCDF for (station, date).

    Tries server-filename convention first (e.g. anything containing both
    the station ref and the YYYYMMDD form of the date), then falls back to
    the legacy investigator pattern <station>_<date>_deployment*.nc."""
    date_compact = date_str.replace("-", "")
    candidates = (
        sorted(glob.glob(os.path.join(nc_dir, f"*{station}*{date_compact}*.nc")))
        or sorted(glob.glob(os.path.join(nc_dir, f"{station}_{date_str}_deployment*.nc")))
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        print(f"    warn — multiple matches for {station} {date_str}, "
              f"using newest: {os.path.basename(candidates[-1])}")
    return candidates[-1]


def read_nc_day(nc_path, date, run):
    """
    Open NetCDF and return (fh, utc_trim, t_raw, start_idx, end_idx).

    fh is kept open so the caller can pull data variables by name.
    Timestamps are converted via utcdata1900 and trimmed to the 24-hour
    window starting at `date` (a UTCDateTime).
    """
    data_time = run["data_time"][0]
    fh = Dataset(nc_path)
    t  = fh.variables[data_time][:]

    utc_list  = [UTCDateTime(str(utcdata1900(float(x)))) for x in t]
    start_dt  = date
    end_dt    = date + 86400.0
    start_idx = np.searchsorted(utc_list, start_dt, "left")
    end_idx   = np.searchsorted(utc_list, end_dt,   "right")

    utc_trim  = utc_list[start_idx:end_idx]
    t_raw     = np.array(t[start_idx:end_idx], dtype=float)
    return fh, utc_trim, t_raw, start_idx, end_idx


# ════════════════════════════════════════════════════════════════════════════
# Sample-period + gap detection — delegates to bin/gap_algorithms.py so
# offline conversion uses the same algorithm code as the live pipeline.
# ════════════════════════════════════════════════════════════════════════════
from gap_algorithms import detect_gaps   # noqa: E402  (alongside other bin imports)


def estimate_sp_and_gaps(utc_trim, sp_nominal, algo="legacy"):
    """
    Backwards-compatible dict wrapper around detect_gaps(). Default `algo`
    matches what offline conversion has always done (legacy median + adaptive
    threshold). Pass algo='anomaly' to use OLS Δt_true + true_missing.
    """
    npts = len(utc_trim)
    if npts < 2:
        raise ValueError(f"Need ≥2 samples, got {npts}")

    t_sec = np.array([float(u - utc_trim[0]) for u in utc_trim])
    result = detect_gaps(algo, t_sec, sp_nominal=sp_nominal)
    diag = result.diagnostics

    return {
        "sp":            result.sp,
        "sr":            result.sr,
        "dt_all":        diag.get("dt_all", np.diff(t_sec)),
        "gap_idx":       result.gap_idx,
        "gap_threshold": diag.get("gap_threshold", float("nan")),
        "multiplier":    diag.get("multiplier", float("nan")),
        "is_full":       result.is_full,
        "expected_npts": diag.get("expected_npts",
                                  diag.get("n_ideal", npts)),
        "split_idx":     list(result.segment_splits),
        "result":        result,         # full GapResult for callers that want it
    }


# ════════════════════════════════════════════════════════════════════════════
# Variability figure — delegates to the investigator's own plotting code
# so the output is byte-identical to `collect` mode (just sourced from local nc).
# ════════════════════════════════════════════════════════════════════════════
def plot_variability(utc_trim, t_raw, station, date_str, sp_nominal, out_dir):
    """Produce the investigator's 4-panel variability figure + stats.txt."""
    os.makedirs(out_dir, exist_ok=True)
    t_sec = t_raw - float(t_raw[0])
    s = compute_variability(t_sec, sp_nominal, utc_trim=utc_trim)
    make_per_day_figure(t_sec, s, station, date_str, out_dir)
    write_stats(s, station, date_str, out_dir)
    print(f"    n={s['n']}  Δt_FG={s['dt_FG']:.6f}s  Δt_true={s['dt_true']:.6f}s"
          f"  gaps={s['n_gaps']}  σ={s['sigma_ms']:.3f}ms")


# ════════════════════════════════════════════════════════════════════════════
# MiniSEED conversion (pipeline-faithful)
# ════════════════════════════════════════════════════════════════════════════
def convert_to_mseed(fh, utc_trim, start_idx, end_idx, station, date_str,
                     channels, datatypes, net_sta_param, param_path, run,
                     gap_info, out_dir):
    """
    Mirrors the MiniSEED write block in OOI_data_request_and_convert_mseed.py.
    One file per (channel × contiguous segment).
    """
    os.makedirs(out_dir, exist_ok=True)

    mseed_ext = run.get("mseed_file_ext", [".mseed"])[0]
    data_quality = run.get("data_quality", ["D"])[0]
    sr = gap_info["sr"]

    # Split timestamps at gaps
    utc_str = np.array([str(x) for x in utc_trim], dtype=object)
    segments = np.split(utc_str, gap_info["split_idx"])

    ref_underscore = station.replace("-", "_")

    for cha_key in channels:
        data_var = datatypes[cha_key]
        if data_var not in fh.variables:
            print(f"    skip {cha_key} (mseed) — variable '{data_var}' missing")
            continue

        data_full = fh.variables[data_var][:]
        data_day  = data_full[start_idx:end_idx]
        if isinstance(data_day, MaskedArray):
            data_day = data_day.filled(np.nan)

        chan_file = os.path.join(param_path, f"{ref_underscore}_{cha_key}.txt")
        channel_param = read_param(chan_file)
        r_value = float(channel_param["r_value"][0])

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
            st = Stream([Trace(data=np.asarray(seg_data, dtype=float),
                               header=stats)])

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
            year_dir = os.path.join(out_dir, seg_start[0:4])
            os.makedirs(year_dir, exist_ok=True)
            write_path = os.path.join(year_dir, mseed_name)
            st.write(write_path, format="MSEED")
            print(f"    mseed  → {write_path}")


# ════════════════════════════════════════════════════════════════════════════
# Per-day driver
# ════════════════════════════════════════════════════════════════════════════
def process_day(station, date, run, nc_dir, plot_dir, mseed_dir,
                do_plot, do_mseed, gap_algo="legacy"):
    date_str = str(date)[:10]
    print(f"\n[{station}]  {date_str}")

    nc_path = find_nc_file(nc_dir, station, date_str)
    if nc_path is None:
        print(f"    skip — no NetCDF under {nc_dir}")
        return
    print(f"    nc: {os.path.basename(nc_path)}")

    try:
        dep_info = get_deployment_for_date(station, date, PARAM_PATH)
    except ValueError as e:
        print(f"    skip — {e}")
        return

    ref_underscore = station.replace("-", "_")
    sta_param_file = os.path.join(PARAM_PATH, f"{ref_underscore}.txt")
    net_sta_param  = read_param(sta_param_file)

    dep = dep_info["deployment"]
    # Channel list & data-type map for this deployment (mirrors pipeline logic)
    channels_raw = net_sta_param.get(f"channels_{dep}",
                                     net_sta_param.get("channels"))[0]
    channels = [c.strip() for c in channels_raw.strip("[]").split(",")]
    dt_raw = net_sta_param.get(f"data_types_{dep}",
                               net_sta_param.get("data_types"))[0]
    datatypes = dt_raw if isinstance(dt_raw, dict) else ast.literal_eval(dt_raw)

    fh, utc_trim, t_raw, start_idx, end_idx = read_nc_day(nc_path, date, run)
    if len(utc_trim) < 2:
        print(f"    skip — only {len(utc_trim)} points in day window")
        fh.close()
        return

    try:
        if do_plot:
            out_dir = os.path.join(plot_dir, f"{station}_{date_str}")
            plot_variability(utc_trim, t_raw, station, date_str,
                             dep_info["sp_nominal"], out_dir)

        if do_mseed:
            gap_info = estimate_sp_and_gaps(utc_trim, dep_info["sp_nominal"],
                                            algo=gap_algo)
            thr = gap_info.get("gap_threshold")
            thr_str = f"  gap_thresh={thr:.2f}s" if thr and not (isinstance(thr, float) and thr != thr) else ""
            print(f"    [{gap_algo}] sp={gap_info['sp']:.6f}s{thr_str}  "
                  f"n_gaps={len(gap_info['gap_idx'])}  "
                  f"is_full={gap_info['is_full']}")
            convert_to_mseed(fh, utc_trim, start_idx, end_idx, station,
                             date_str, channels, datatypes, net_sta_param,
                             PARAM_PATH, run, gap_info, mseed_dir)
    finally:
        fh.close()


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Plot dayplots (and optionally convert to MiniSEED) from "
                    "NetCDF files saved by temporal_anomaly_investigator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date",  help="YYYY-MM-DD (single day).")
    parser.add_argument("--start", help="YYYY-MM-DD (range start).")
    parser.add_argument("--end",   help="YYYY-MM-DD (range end, inclusive).")
    parser.add_argument("--station", nargs="*", default=None,
                        help="Station reference designator(s); "
                             "default = all 3 PREST stations.")
    parser.add_argument("--nc-dir",    default=DEFAULT_NC_DIR,
                        help=f"NetCDF input directory (default: {DEFAULT_NC_DIR})")
    parser.add_argument("--plot-dir",  default=DEFAULT_PLOT_DIR,
                        help=f"Dayplot output directory (default: {DEFAULT_PLOT_DIR})")
    parser.add_argument("--mseed-dir", default=DEFAULT_MSEED_DIR,
                        help=f"MiniSEED output directory (default: {DEFAULT_MSEED_DIR})")
    parser.add_argument("--no-plot", action="store_true",
                        help="Skip dayplot generation.")
    parser.add_argument("--convert-mseed", action="store_true",
                        help="Also convert NetCDF → MiniSEED using the "
                             "pipeline's gap-detection and splitting logic.")
    parser.add_argument("--gap-algo", choices=["legacy", "anomaly"], default="legacy",
                        help="Gap-detection algorithm for MiniSEED conversion. "
                             "Default 'legacy' matches the pipeline default. Use "
                             "'anomaly' to test OLS Δt_true + true_missing offline.")
    args = parser.parse_args()

    if not args.date and not (args.start and args.end):
        parser.error("must give either --date OR --start and --end")
    if args.no_plot and not args.convert_mseed:
        parser.error("--no-plot with no --convert-mseed leaves nothing to do")

    run = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))
    stations = args.station if args.station else STATIONS

    if args.date:
        dates = [UTCDateTime(args.date + "T00:00:00Z")]
    else:
        d    = UTCDateTime(args.start + "T00:00:00Z")
        last = UTCDateTime(args.end   + "T00:00:00Z")
        dates = []
        while d <= last:
            dates.append(d)
            d += 86400.0

    do_plot  = not args.no_plot
    do_mseed = args.convert_mseed

    for date in dates:
        for station in stations:
            process_day(station, date, run,
                        nc_dir=args.nc_dir,
                        plot_dir=args.plot_dir,
                        mseed_dir=args.mseed_dir,
                        do_plot=do_plot,
                        do_mseed=do_mseed,
                        gap_algo=args.gap_algo)


if __name__ == "__main__":
    main()
