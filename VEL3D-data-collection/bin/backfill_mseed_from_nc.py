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
    # all VEL3D stations + every stream they carry (VEL3D-C does both streams):
    python bin/backfill_mseed_from_nc.py --start 2014-09-14 --end 2026-03-01
    # one VEL3D-B station:
    python bin/backfill_mseed_from_nc.py --start 2025-01-01 --end 2025-01-07 \\
        --station RS01SLBS-MJ01A-12-VEL3DB101 --gap-algo anomaly --append-stats
    # one VEL3D-C station, velocity stream only:
    python bin/backfill_mseed_from_nc.py --start 2025-01-01 --end 2025-01-07 \\
        --station CE02SHBP-LJ01D-07-VEL3DC108 --stream vel3d_cd_velocity_data
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
from diagnose_timing import (STATIONS, get_deployment_for_date, read_goldcopy_day, GoldCopyDay,
                             NoDataError, GoldCopyMissingVariable)
from plot_from_netcdf import find_nc_file, read_nc_day
from gap_algorithms import detect_gaps, mseed_segments, SEGMENTING_MODES, duplicate_mask
# --variability: reuse the investigator's per-day timing QC on the data this
# backfill already pulled, so each day is fetched once for both products.
from temporal_anomaly_investigator import (
    OUT_ROOT as TA_OUT_ROOT, compute_variability, make_per_day_figure,
    write_stats, _row_from_stats, _no_data_row,
    _append_row as _append_variability_row,
    _metrics_csv_path as _variability_csv_path,
    _load_existing_keys as _variability_keys,
)

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


# ── VEL3D stream helpers (parity with OOI_data_request_and_convert_mseed.py) ──
# Each VEL3D channel declares the OOI stream carrying its variable via the
# per-channel `c_stream` param. VEL3D-B is single-stream (vel3d_b_sample);
# VEL3D-C spans two (vel3d_cd_velocity_data 8 Hz + vel3d_cd_system_data 1 Hz),
# so each station is backfilled one stream at a time.
def _channel_stream(ref_under, chan):
    try:
        cp = read_param(os.path.join(PARAM_PATH, f"{ref_under}_{chan.strip()}.txt"))
        return cp.get("c_stream", [None])[0]
    except Exception:
        return None


def _distinct_streams(ref_under, channels):
    out = []
    for ch in channels:
        s = _channel_stream(ref_under, ch)
        if s and s not in out:
            out.append(s)
    return out


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
                          gap_result, mseed_dir, segmenting="timing", min_piece_s=None):
    """One MiniSEED file per (channel × contiguous segment). Mirrors the cron
    pipeline's write block. `segmenting` picks where traces break — see
    gap_algorithms.mseed_segments ('gaps' = original behaviour). Traces
    shorter than `min_piece_s` seconds are not written (fragments); longer
    traces on the same day are. Returns (files_written, traces_dropped,
    samples_dropped) — drop counts per channel."""
    mseed_ext    = run.get("mseed_file_ext", [".mseed"])[0]
    data_quality = run.get("data_quality", ["D"])[0]
    rec_len      = int(run["rec_len"][0])
    sr           = gap_result.sr

    utc_str  = np.array([str(x) for x in utc_trim], dtype=object)
    t_sec    = np.asarray(t_raw, dtype=float) - float(t_raw[0])
    splits, starts = mseed_segments(segmenting, t_sec, gap_result)
    segments = np.split(utc_str, splits)
    if segmenting == "gaps":                     # original: first/last timestamp
        seg_times = [(ti[0], ti[-1]) for ti in segments]
    else:                                        # grid-anchored start; end on the same grid
        seg_times = []
        for ti, st0 in zip(segments, starts):
            t_start = utc_trim[0] + st0
            seg_times.append((str(t_start), str(t_start + (len(ti) - 1) / sr)))
    ref_under = station.replace("-", "_")
    keep = [min_piece_s is None or len(ti) / sr >= min_piece_s for ti in segments]
    n_drop = sum(1 for k in keep if not k)
    s_drop = sum(len(ti) for ti, k in zip(segments, keep) if not k)

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
        conversion    = float(channel_param["conversion"][0])

        cursor = 0
        for ti, (seg_start, seg_end), k in zip(segments, seg_times, keep):
            n_seg = len(ti)
            if n_seg == 0:
                continue
            seg_data  = data_day[cursor:cursor + n_seg] / conversion
            cursor   += n_seg
            if not k:                    # fragment shorter than min_piece_s
                continue

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

    return written, n_drop, s_drop


# Variables that identify one instrument record, per stream, for duplicate
# removal. VEL3D-C velocity: the raw (L0) counts + ensemble counter — the two
# copies of a double-ingested record match exactly there, while the L1
# velocities differ by ~1e-6 m/s (declination is evaluated at each copy's own
# timestamp). Other streams fall back to the written data variables.
DEDUP_KEYS = {
    "vel3d_cd_velocity_data": ["ensemble_counter", "turbulent_velocity_east",
                               "turbulent_velocity_north", "turbulent_velocity_vertical"],
    # 1 Hz system record: the instrument's own clock string is unique per
    # second and identical in both copies (internal_timestamp is NOT).
    "vel3d_cd_system_data": ["date_time_string", "temperature_centidegree",
                             "heading_decidegree", "pitch_decidegree",
                             "roll_decidegree", "sound_speed_dms"],
}


def _key_column(a):
    """Numeric column for duplicate matching; char/string arrays → integer ids."""
    if isinstance(a, MaskedArray):
        a = a.filled(b"" if a.dtype.kind == "S" else np.nan)
    a = np.asarray(a)
    if a.dtype.kind == "S" and a.ndim == 2:             # netCDF char array (n, strlen)
        a = np.ascontiguousarray(a).view(f"S{a.shape[1]}").ravel()
    if a.dtype.kind in "SUO":
        return np.unique(a, return_inverse=True)[1].ravel().astype(float)
    return a.astype(float)


def process_day(station, date, run, gap_algo, nc_dir, mseed_dir,
                metrics_csv, append_stats, skip_existing, stream=None,
                source="local", variability=None, segmenting="timing",
                min_piece_s=None):
    """variability: None, or {"csv": path, "done": set of (station, date)} —
    also write the temporal_anomaly_investigator CSV row (+ figure when the
    day has gaps) from the same pulled data. A day already in that CSV is
    complete (row is written after the MiniSEED) and is skipped."""
    date_str = str(date)[:10]
    gap_algo_requested = gap_algo

    def _variability_no_data(dep, sp_nom):
        if variability is not None:
            _append_variability_row(variability["csv"],
                                    _no_data_row(station, date_str, dep, sp_nom))
            variability["done"].add((station, date_str))

    if variability is not None and (station, date_str) in variability["done"]:
        print(f"  [{station}] {date_str}  skip — already in variability CSV")
        return False

    if source == "local":
        nc_path = find_nc_file(nc_dir, station, date_str, stream=stream)
        if nc_path is None:
            print(f"  [{station}] {date_str}  skip — no NetCDF")
            return False

    if append_stats and skip_existing:
        existing = _load_existing_keys(metrics_csv)
        if (station, date_str) in existing:
            print(f"  [{station}] {date_str}  skip — already in metrics CSV")
            return False

    try:
        dep_info = get_deployment_for_date(station, date, PARAM_PATH, stream=stream)
    except ValueError as e:
        print(f"  [{station}] {date_str}  skip — {e}")
        _variability_no_data(0, 0)
        return False
    deployment = dep_info["deployment"]
    sp_nominal = dep_info["sp_nominal"]

    ref_under     = station.replace("-", "_")
    sta_param     = read_param(os.path.join(PARAM_PATH, f"{ref_under}.txt"))
    chan_raw      = sta_param.get(f"channels_{deployment}",
                                  sta_param.get("channels"))[0]
    channels      = [c.strip() for c in chan_raw.strip("[]").split(",")]
    # VEL3D-C: keep only the channels carried by the stream being backfilled
    # (velocity → MOU/MOV/MOW, system_data → LKO). Single-stream stations keep all.
    if stream is not None:
        channels = [c for c in channels if _channel_stream(ref_under, c) == stream]
        if not channels:
            print(f"  [{station}] {date_str}  skip — no channels for stream {stream}")
            return False
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

    if source == "goldcopy":
        try:
            want = [datatypes[c] for c in channels]
            fh, utc_trim, t_raw, start_idx, end_idx = read_goldcopy_day(
                station, stream, date, run,
                variables=want + [k for k in DEDUP_KEYS.get(stream, []) + ["internal_timestamp"]
                                  if k not in want])
        except NoDataError as e:
            print(f"  [{station}] {date_str}  skip — {e}")
            _variability_no_data(deployment, sp_nominal)
            return False
        except GoldCopyMissingVariable:
            raise                    # whole stream unusable — stop, don't skip day by day
        except Exception as e:
            print(f"  [{station}] {date_str}  FAILED (re-run to retry) — {type(e).__name__}: {e}")
            return False
    else:
        fh, utc_trim, t_raw, start_idx, end_idx = read_nc_day(nc_path, date, run)

    # OOI double ingestion: drop duplicated records (identical values within
    # 30 s, offset copy) before any timing analysis. No-op on normal days.
    if len(utc_trim) >= 2 and sp_nominal:
        def _col(v):
            a = fh.variables[v][:][start_idx:end_idx]
            return a.filled(np.nan) if isinstance(a, MaskedArray) else np.asarray(a)
        dvars = [datatypes[c] for c in channels if datatypes[c] in fh.variables]
        keys = [k for k in DEDUP_KEYS.get(stream, []) if k in fh.variables] or dvars
        t_rel = np.asarray(t_raw, dtype=float) - float(t_raw[0])
        prefer = None
        if "internal_timestamp" in fh.variables:   # original copy: internal clock ≈ time
            prefer = np.abs(np.asarray(t_raw, dtype=float)
                            - np.asarray(_col("internal_timestamp"), dtype=float))
            prefer = np.where(np.isfinite(prefer), prefer, np.inf)
        keep, n_dup = duplicate_mask(t_rel, [_key_column(_col(k)) for k in keys], sp_nominal,
                                     prefer=prefer)
        if n_dup:
            print(f"  [{station}] {date_str}  removed {n_dup} duplicated records "
                  f"({100.0 * n_dup / len(keep):.1f}% — OOI double ingestion)")
            cols = {v: _col(v) for v in dvars}
            fh.close()
            fh = GoldCopyDay({v: c[keep] for v, c in cols.items()})
            t_raw = np.asarray(t_raw, dtype=float)[keep]
            utc_trim = [u for u, k in zip(utc_trim, keep) if k]
            start_idx, end_idx = 0, len(t_raw)
    if len(utc_trim) < 2:
        print(f"  [{station}] {date_str}  skip — only {len(utc_trim)} samples")
        _variability_no_data(deployment, sp_nominal)
        fh.close()
        return False

    try:
        t_sec = t_raw - float(t_raw[0])
        gap_result = detect_gaps(gap_algo, t_sec, sp_nominal=sp_nominal,
                                 req_duration=86400.0)

        n_written, n_drop, s_drop = _write_mseed_segments(
            fh, utc_trim, t_raw, start_idx, end_idx, station,
            channels, datatypes, sta_param, run, gap_result, mseed_dir,
            segmenting=segmenting, min_piece_s=min_piece_s)
        if n_drop:
            # Log fragments not written: <mseed-dir>/../outlier/dropped_pieces.csv
            drop_log = os.path.join(os.path.dirname(os.path.abspath(mseed_dir)),
                                    "outlier", "dropped_pieces.csv")
            os.makedirs(os.path.dirname(drop_log), exist_ok=True)
            new = not os.path.exists(drop_log)
            with open(drop_log, "a") as f:
                if new:
                    f.write("station,stream,date,n_points,traces_per_channel,"
                            "traces_dropped,samples_dropped,min_piece_s\n")
                f.write(f"{station},{stream},{date_str},{len(t_sec)},"
                        f"{n_written // max(1, len(channels)) + n_drop},{n_drop},{s_drop},{min_piece_s:g}\n")
            print(f"      dropped {n_drop} trace(s)/ch shorter than {min_piece_s:g}s "
                  f"({s_drop} samples, {100.0 * s_drop / len(t_sec):.2f}% of the day)")

        actual_algo = gap_result.diagnostics.get("algorithm", gap_algo)
        print(f"  [{station}] {date_str}  algo={actual_algo}  "
              f"n={len(utc_trim)}  segs={gap_result.n_segments}  "
              f"traces/ch={n_written // max(1, len(channels))} ({segmenting})  "
              f"gaps={gap_result.n_gaps}  → {n_written} mseed files")

        if variability is not None:
            vs = compute_variability(t_sec, sp_nominal, utc_trim=utc_trim)
            fig_generated = False
            if vs["n_gaps"] > 0:          # investigator --only-gaps behaviour
                tag = f"{station}_{stream}_{date_str}" if stream else f"{station}_{date_str}"
                fig_dir = os.path.join(TA_OUT_ROOT, "figures", "per_day", tag)
                make_per_day_figure(t_sec, vs, station, date_str, fig_dir)
                write_stats(vs, station, date_str, fig_dir)
                fig_generated = True
            _append_variability_row(variability["csv"], _row_from_stats(
                vs, station, date_str, deployment, fig_generated))
            variability["done"].add((station, date_str))
            print(f"      variability: Δt_true={vs['dt_true']:.6f}s  gaps={vs['n_gaps']}  "
                  f"σ={vs['sigma_ms']:.3f}ms" + ("  (figure)" if fig_generated else ""))

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
                "run":                      "vel3d",
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
                        "Default: all VEL3D stations.")
    p.add_argument("--stream", default=None,
                   help="Restrict to one OOI stream (e.g. vel3d_cd_velocity_data). "
                        "Default: process every stream each station carries — "
                        "VEL3D-C runs both velocity (8 Hz) and system_data temp (1 Hz).")
    p.add_argument("--gap-algo", choices=["legacy", "anomaly"], default="anomaly",
                   help="Gap detection algorithm. Default: anomaly "
                        "(matches param/run_vel3d.txt).")
    p.add_argument("--source", choices=["local", "goldcopy"], default="local",
                   help="local: saved NetCDFs in --nc-dir (default). goldcopy: read "
                        "straight from OOI's pre-built gold copy over OPeNDAP — no M2M "
                        "queue, nothing stored. Gold copy lacks VEL3D-C temperature "
                        "(vel3d_cd_system_data), so use local for that stream.")
    p.add_argument("--segmenting", choices=SEGMENTING_MODES, default=None,
                   help="Where MiniSEED traces break. timing (default): at gaps AND wherever "
                        "timestamps leave the regular grid by > ½ sample, so every sample stays "
                        "within ½ sample of OOI's recorded time. gaps: original behaviour "
                        "(gap splits only). Default comes from run_vel3d.txt mseed_segmenting.")
    p.add_argument("--min-piece-seconds", type=float, default=None,
                   help="Do not write MiniSEED traces shorter than this (fragments); longer "
                        "traces on the same day are written. 60 caps a channel at 1440 "
                        "files/day. Drops are logged to <mseed-dir>/../outlier/dropped_pieces.csv.")
    p.add_argument("--variability", action="store_true",
                   help="Also write the temporal_anomaly_investigator CSV row per day "
                        "(+ 4-panel figure on gap days) from the same pulled data — "
                        "one pull per day for both MiniSEED and timing QC. Days already "
                        "in that CSV are skipped, so re-runs resume.")
    p.add_argument("--nc-dir",     default=DEFAULT_NC_DIR)
    p.add_argument("--mseed-dir",  default=DEFAULT_MSEED,
                   help=f"MiniSEED output root (default: {DEFAULT_MSEED})")
    p.add_argument("--metrics-dir", default=DEFAULT_METRICS,
                   help=f"Per-day stats CSV directory (default: {DEFAULT_METRICS})")
    p.add_argument("--append-stats", action="store_true",
                   help="Also append per-day stats rows to "
                        "<metrics-dir>/<station>_vel3d_pipeline_stats.csv")
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
    run = read_param(os.path.join(PARAM_PATH, "run_vel3d.txt"))
    segmenting = args.segmenting or run.get("mseed_segmenting", ["timing"])[0]
    print(f"MiniSEED segmenting: {segmenting}")

    n_done = n_skipped = 0
    for station in stations:
        ref_under = station.replace("-", "_")
        sta_param = read_param(os.path.join(PARAM_PATH, f"{ref_under}.txt"))
        all_chans = [c.strip() for c in sta_param["channels"][0].strip("[]").split(",")]
        streams = _distinct_streams(ref_under, all_chans) or [None]
        if args.stream:
            if args.stream not in streams:
                print(f"  [{station}] skip — stream {args.stream} not among {streams}")
                continue
            streams = [args.stream]
        multi = len(streams) > 1

        for stream in streams:
            # Per-stream metrics CSV so VEL3D-C's two streams (different sp,
            # same dates) don't collide on the (station, date) skip key.
            sfx = f"_{stream}" if (stream and multi) else ""
            metrics_csv = os.path.join(args.metrics_dir,
                                       f"{station}{sfx}_vel3d_pipeline_stats.csv")
            variability = None
            if args.variability:
                vcsv = _variability_csv_path(station, stream)
                variability = {"csv": vcsv, "done": _variability_keys(vcsv)}
            for date in dates:
                try:
                    ok = process_day(station, date, run, args.gap_algo,
                                     args.nc_dir, args.mseed_dir,
                                     metrics_csv, args.append_stats,
                                     skip_existing=not args.no_skip, stream=stream,
                                     source=args.source, variability=variability,
                                     segmenting=segmenting,
                                     min_piece_s=args.min_piece_seconds)
                except GoldCopyMissingVariable as e:
                    print(f"  [{station}] skip stream {stream} — {e}")
                    break
                if ok:
                    n_done += 1
                else:
                    n_skipped += 1

    print(f"\nDone. Converted: {n_done}  Skipped: {n_skipped}")


if __name__ == "__main__":
    main()
