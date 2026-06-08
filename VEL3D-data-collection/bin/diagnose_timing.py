#!/usr/bin/env python3
"""
diagnose_timing.py  —  COSZO OOI PREST timing diagnostics
═══════════════════════════════════════════════════════════════════════════════

SUMMARY
───────
This script diagnoses timing quality of OOI PREST seafloor pressure data
across three stations and over arbitrary date ranges.  It operates in three
modes selected with --mode:

  single  (default)
    Analyse one station/date.  Fetches NetCDF timestamps from the OOI M2M API,
    runs the full diagnostic pipeline, and writes a 3-panel figure and a stats
    text file to output/diagnostics/figures/per_day/<station>_<date>/.

  collect
    Batch loop over all (station × date) combinations in a requested range.
    For each day:
      1. Auto-selects the correct deployment from the param files (handles
         deployment boundaries transparently — no manual --deployment flag).
      2. Fetches raw NetCDF timestamps from the OOI M2M API.
      3. Runs the three-step diagnostic pipeline (see below).
      4. Appends one row to the station's metrics CSV.
      5. Generates a per-day figure only when anomaly triggers fire.
    Rows already present in the CSV are skipped — the script is safely
    re-runnable and can be stopped and resumed at any point.

  plot
    Reads the metrics CSVs produced by collect and generates four summary
    figures saved to output/diagnostics/figures/summary/:
      Fig 1 — Sample rate over time (one subplot per station)
      Fig 2 — Gap count over time
      Fig 3 — Jitter metric (p95) over time
      Fig 4 — Sample count per day (n_points vs expected)
      Fig 5 — Data calendar heatmap (full / incomplete / gapped / no-data)

DIAGNOSTIC PIPELINE (compute_stats)
─────────────────────────────────────
All timing analysis follows this strict order so gap artifacts never
contaminate jitter metrics:

  Step 1 — Day-level gap detection
    • Compute all Δt = np.diff(t_sec) across the full day.
    • Estimate sp_calc from the robust median of the lower-90th-percentile Δt
      (same algorithm as the main pipeline — resistant to gap outliers).
    • Apply adaptive gap threshold (same multipliers as main pipeline).
    • Record: n_gaps, gap locations, gap durations.

  Step 2 — Per-segment sample rates
    • Split t_sec at gap boundaries into contiguous segments.
    • For each segment independently compute sp_calc_seg (robust median Δt).
    • Segments may run at different rates (e.g. sensor slowed after dropout).

  Step 3 — Per-segment jitter metrics
    • For each segment: fit a line through (index, t_sec_segment), compute
      residuals = t_sec − t_fit within the segment.
    • Compute William's jitter metric using the segment-level sp_calc_seg:
          jitter_i = |Δti / sp_calc_seg  −  round(Δti / sp_calc_seg)|
      Using sp_calc_seg (not sp_nominal) means the metric measures random
      irregularity only.  Systematic clock drift is captured separately as
          sp_deviation = |sp_calc − sp_nominal|
      Measuring jitter against sp_nominal would conflate drift with jitter.
    • Concatenate residuals and jitter values across all segments.
    • Summary stats: mean, median, std, p95, max  (jitter);
                     std, p95                      (residuals in ms).

PER-DAY FIGURE (4 panels)
──────────────────────────
  Panel 1 — Histogram of timing residuals (ms, count y-axis)
    Built from per-segment residuals concatenated across the whole day.
    Bin width: Freedman-Diaconis.  Overlaid Gaussian curve scaled to count.
    Vertical lines at ±1σ and ±p95.
    Shape tells you: narrow Gaussian = random noise; bimodal = quantization
    artifact; long tail = ingestion delays; skewed = systematic drift.

  Panel 2 — Histogram of (Δt − sp_nominal) in ms (count y-axis)
    Shows how each inter-sample interval deviates from the nominal sample
    period.  Gap intervals are excluded.  Vertical reference lines for
    sp_calc offset and sp_fit offset from nominal.

  Panel 3 — Residuals scatter (sample index vs ms)
    Same residuals as Panel 1 but plotted over time so you can see when
    jitter occurs during the day.  Points colour-coded by segment.

  Panel 4 — Δt between consecutive samples
    sp_calc (green), sp_fit (amber dashed), sp_nominal (purple dotted), and
    gap threshold (red dash-dot) reference lines.  Points above the threshold
    highlighted in red.

ANOMALY TRIGGERS (when a per-day figure is generated)
───────────────────────────────────────────────────────
  First pass (jitter baseline not yet established):
    • n_gaps > 0
    • is_full = False   (day is incomplete)
    • sp_alert = True   (sp_calc deviates beyond hybrid threshold)
  Jitter trigger is DISABLED until a baseline is established from the collect
  output.  All jitter metrics are stored in the CSV regardless.

METRICS CSV SCHEMA
──────────────────
  One CSV per station: output/diagnostics/metrics/<station>_metrics.csv
  Columns: date, station, deployment, has_data,
           data_start, data_end, missing_start, missing_end,
           n_points, expected_npts, is_full,
           n_gaps, n_segments, gap_total_duration_s, gap_max_missing, gap_total_missing,
           sp_calc, sp_fit, sp_nominal, sp_deviation, sp_fit_deviation, sp_calc_minus_fit, sp_alert,
           jitter_mean_ms, jitter_median_ms, jitter_std_ms, jitter_p95_ms, jitter_max_ms,
           res_std_ms, res_p95_ms, skewness, figure_generated

USAGE
─────
  cd coszo-data-collection
  source .ooi_env

  # Single day diagnostic
  conda run -n ooi_env python bin/diagnose_timing.py \\
      --mode single --station RS01SLBS-MJ01A-06-PRESTA101 \\
      --date 2020-07-01

  # Batch collect over a date range (all 3 stations)
  conda run -n ooi_env python bin/diagnose_timing.py \\
      --mode collect --start 2024-01-01 --end 2024-01-31

  # Collect for one station only
  conda run -n ooi_env python bin/diagnose_timing.py \\
      --mode collect --station RS01SLBS-MJ01A-06-PRESTA101 \\
      --start 2024-01-01 --end 2024-01-31

  # Generate summary plots from collected metrics
  conda run -n ooi_env python bin/diagnose_timing.py --mode plot
"""

import os
import sys
import csv
import time
import argparse
import datetime
import urllib.request
import xml.etree.cElementTree as ET

import numpy as np
import requests
from netCDF4 import Dataset
from obspy import UTCDateTime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
import matplotlib.cm as mcm

# ── Repo paths ─────────────────────────────────────────────────────────────
REPO_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_PATH   = os.path.join(REPO_ROOT, "bin")
PARAM_PATH = os.path.join(REPO_ROOT, "param")
OUT_ROOT   = os.path.join(REPO_ROOT, "output", "diagnostics")
sys.path.insert(0, BIN_PATH)

from read_param  import read_param
from convert_utc import utcdata1900

# ── Stations ───────────────────────────────────────────────────────────────
STATIONS = [
    "RS01SLBS-MJ01A-06-PRESTA101",
    "RS01SUM1-LJ01B-09-PRESTB102",
    "RS03AXBS-MJ03A-06-PRESTA301",
]

# ── Metrics CSV columns ─────────────────────────────────────────────────────
CSV_COLUMNS = [
    "date", "station", "deployment", "has_data",
    "data_start", "data_end", "missing_start", "missing_end",
    "n_points", "expected_npts", "is_full",
    "n_gaps", "n_segments", "gap_total_duration_s", "gap_max_missing", "gap_total_missing",
    "sp_calc", "sp_fit", "sp_nominal",
    "sp_deviation", "sp_fit_deviation", "sp_calc_minus_fit",
    "sp_alert",
    "jitter_mean_ms", "jitter_median_ms", "jitter_std_ms", "jitter_p95_ms", "jitter_max_ms",
    "res_std_ms", "res_p95_ms", "skewness", "figure_generated",
]

# ── Colours ─────────────────────────────────────────────────────────────────
C_LINE    = "#2196F3"   # blue   — main data
C_FIT     = "#FF9800"   # amber  — best-fit / Gaussian
C_ZERO    = "#9E9E9E"   # grey   — zero reference
C_SP      = "#4CAF50"   # green  — calculated sp
C_NOMINAL = "#9C27B0"   # purple — nominal sp
C_THRESH  = "#F44336"   # red    — gap threshold
C_GAP     = "#F44336"   # red    — intervals above threshold / alerts
C_P95     = "#E91E63"   # pink   — p95 lines

# Segment palette (up to 10 colours for colour-coded residual scatter)
SEG_PALETTE = [mcm.tab10(i) for i in range(10)]

# Calendar status colours
CAL_COLORS = {
    0: "#9E9E9E",   # no data
    1: "#FFC107",   # incomplete only
    2: "#FF9800",   # gaps, otherwise complete
    3: "#F44336",   # gaps + incomplete
    4: "#9C27B0",   # sp_alert
    5: "#4CAF50",   # clean
}
CAL_LABELS = {
    0: "no data",
    1: "incomplete",
    2: "gaps",
    3: "gaps + incomplete",
    4: "sp alert",
    5: "clean",
}

# ── Credentials ─────────────────────────────────────────────────────────────
def load_credentials():
    username = os.environ.get("OOI_USERNAME")
    token    = os.environ.get("OOI_TOKEN")
    if username and token:
        return username, token
    env_file = os.path.join(REPO_ROOT, ".ooi_env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip().lstrip("export").strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if k.strip() == "OOI_USERNAME":
                        username = v
                    if k.strip() == "OOI_TOKEN":
                        token = v
    if not username or not token:
        raise RuntimeError(
            "OOI credentials not found. Source .ooi_env or set OOI_USERNAME/OOI_TOKEN.")
    return username, token


# ── Deployment detection ─────────────────────────────────────────────────────
def get_deployment_for_date(station, date, param_path):
    """
    Auto-detect which deployment covers `date` (UTCDateTime) for `station`.

    Handles two param file layouts:
      Numbered deployments (SLBS, SUM1): station param has channels_1,
        channels_2, etc.  Each channel has its own file with a single
        c_start / c_end.
      Epoch list (AXBS): station param has a single `channels` key.
        The channel file stores semicolon-separated c_start, c_end,
        c_sample_rate — one value per deployment epoch.

    Returns dict with keys:
      deployment  — 1-indexed deployment number
      sp_nominal  — nominal sample period in seconds (from first pressure chan)
      channels    — list of channel name strings for this deployment
    """
    ref_underscore = station.replace("-", "_")
    sta_param = read_param(os.path.join(param_path, f"{ref_underscore}.txt"))

    def _parse_pressure_chan_file(chan_file, epoch_idx=0):
        """Return (sp_nominal, c_start, c_end) for a given epoch index."""
        cp = read_param(chan_file)
        c_start = UTCDateTime(cp["c_start"][epoch_idx].strip())
        c_end_str = cp["c_end"][epoch_idx].strip()
        c_end = None if c_end_str == "None" else UTCDateTime(c_end_str)
        sp_nominal = 1.0 / float(cp["c_sample_rate"][epoch_idx].strip())
        return sp_nominal, c_start, c_end

    def _first_pressure_chan(chan_list):
        return next((c for c in chan_list if "DO" in c), chan_list[0])

    # ── Numbered deployments (channels_1, channels_2, …) ──────────────────
    dep = 1
    while True:
        chan_key = f"channels_{dep}"
        if chan_key not in sta_param:
            break
        chans = [c.strip() for c in sta_param[chan_key][0].strip("[]").split(",")]
        pressure_chan = _first_pressure_chan(chans)
        chan_file = os.path.join(param_path, f"{ref_underscore}_{pressure_chan}.txt")
        sp_nominal, c_start, c_end = _parse_pressure_chan_file(chan_file, epoch_idx=0)
        if date >= c_start and (c_end is None or date < c_end):
            return {"deployment": dep, "sp_nominal": sp_nominal, "channels": chans}
        dep += 1

    # ── Epoch list (single `channels` key, semicolon-separated dates) ─────
    if "channels" not in sta_param:
        raise ValueError(
            f"No deployment found for {station} on {date}: "
            "neither channels_N nor channels key present.")
    chans = [c.strip() for c in sta_param["channels"][0].strip("[]").split(",")]
    pressure_chan = _first_pressure_chan(chans)
    chan_file = os.path.join(param_path, f"{ref_underscore}_{pressure_chan}.txt")
    cp = read_param(chan_file)
    n_epochs = len(cp["c_start"])
    for i in range(n_epochs):
        sp_nominal, c_start, c_end = _parse_pressure_chan_file(chan_file, epoch_idx=i)
        if date >= c_start and (c_end is None or date < c_end):
            return {"deployment": i + 1, "sp_nominal": sp_nominal, "channels": chans}

    raise ValueError(
        f"No deployment found for {station} on {date}. "
        "Date may be outside all deployment windows.")


# ── Fetch OOI timestamps ─────────────────────────────────────────────────────
def fetch_nc_timestamps(station, start_dt, end_dt, deployment, run,
                        save_nc_dir=None):
    """
    Fetch raw NetCDF timestamps for one 24-hour window from OOI M2M API.

    Returns (t_raw, t_sec, utc_trim, deployment_id) where:
      t_raw       — raw float timestamps (1900 epoch) trimmed to window
      t_sec       — seconds since first sample (relative)
      utc_trim    — list of UTCDateTime objects trimmed to window
      deployment_id — integer deployment ID from API

    If save_nc_dir is set, also download the underlying NetCDF file to that
    directory using the server-provided filename (overwrites if present).
    """
    username, token = load_credentials()

    base_url   = run["base_url"][0]
    data_url   = run["data_url"][0]
    http_srv   = run["http_server"][0]
    opendap    = run["opendap_server"][0]
    max_cycle  = int(run["max_cycle"][0])
    delay      = int(run["delay"][0])
    data_time  = run["data_time"][0]

    url_designator = station.replace("-", "/", 2)
    run_name = "prest"
    start_str = str(start_dt)
    end_str   = str(end_dt)

    def _check_server_up(r):
        """Raise a clear error if the OOI API returned an HTML maintenance page."""
        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct or r.text.lstrip().startswith("<"):
            raise RuntimeError("OOI server is down (maintenance page returned).")

    # Deployment info
    dep_url      = "/".join([base_url, url_designator])
    resp         = requests.get(dep_url, auth=(username, token))
    resp.raise_for_status()
    _check_server_up(resp)
    dep_idx      = deployment - 1
    deployment_id = resp.json()[dep_idx]
    print(f"  Deployment ID from API: {deployment_id}  (requested index {dep_idx})")

    # Data request
    stream_tag = (
        f"streamed/{run_name}_real_time?"
        "include_provenance=true&format=application/netcdf"
    )
    data_req = "&".join([
        "/".join([data_url, url_designator, stream_tag]),
        f"beginDT={start_str}",
        f"endDT={end_str}",
    ])
    print(f"  Data URL: {data_req[:120]}…")
    resp = requests.get(data_req, auth=(username, token))
    resp.raise_for_status()
    _check_server_up(resp)

    if "No data for request" in str(resp.json()):
        raise RuntimeError("OOI returned 'No data for request'.")
    if "allURLs" not in resp.json():
        raise RuntimeError(f"Unexpected response: {resp.json()}")

    response_url = resp.json()["allURLs"][1]
    status_url   = "/".join([response_url, "status.json"])

    # Poll for completion
    print(f"  Polling (max {max_cycle} × {delay}s)…")
    for attempt in range(1, max_cycle + 2):
        time.sleep(delay)
        status = requests.get(status_url)
        print(f"    attempt {attempt}: HTTP {status.status_code}")
        if status.status_code == 200:
            break
    else:
        raise RuntimeError("Data request did not complete in time.")

    data_tag = status.json()
    complete = next((v for v in data_tag.values() if isinstance(v, str)), None)
    if complete != "complete":
        raise RuntimeError(f"Unexpected status: {data_tag}")

    # Locate NetCDF via NCML
    ncml_name = (
        f"deployment{deployment_id:04d}_{station}"
        f"-streamed-{run_name}_real_time.ncml"
    )
    ncml_url = "/".join([response_url, ncml_name])
    ncml     = urllib.request.urlopen(ncml_url)
    root     = ET.ElementTree(file=ncml).getroot()
    ncml.close()

    netCDF = None
    for child in root:
        if "aggregation" in child.tag:
            for elem in child:
                netCDF = elem.get("location").strip()
    if netCDF is None:
        raise RuntimeError("No NetCDF location found in NCML.")

    opendap_url = "/".join([response_url, netCDF]).replace(http_srv, opendap)
    print(f"  OPeNDAP: {opendap_url[:100]}…")

    if save_nc_dir is not None:
        os.makedirs(save_nc_dir, exist_ok=True)
        http_nc_url = "/".join([response_url, netCDF])
        local_nc = os.path.join(save_nc_dir, os.path.basename(netCDF))
        print(f"  Downloading NetCDF → {local_nc}")
        with requests.get(http_nc_url, auth=(username, token), stream=True) as r:
            r.raise_for_status()
            with open(local_nc, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        print(f"  Saved {os.path.getsize(local_nc)} bytes")

    fh = Dataset(opendap_url)
    t  = fh.variables[data_time][:]
    print(f"  Raw sample count: {len(t)}")

    utc_list  = [UTCDateTime(str(utcdata1900(float(x)))) for x in t]
    start_idx = np.searchsorted(utc_list, start_dt, "left")
    end_idx   = np.searchsorted(utc_list, end_dt,   "right")

    t_raw    = np.array(t[start_idx:end_idx], dtype=float)
    utc_trim = utc_list[start_idx:end_idx]
    print(f"  Trimmed to window: {len(t_raw)} samples  "
          f"({utc_trim[0]} → {utc_trim[-1]})")

    t0    = float(t_raw[0])
    t_sec = t_raw - t0
    return t_raw, t_sec, utc_trim, deployment_id


# ── Diagnostic computation ───────────────────────────────────────────────────
def compute_stats(t_sec, sp_nominal, utc_trim=None):
    """
    Three-step timing diagnostic:

    Step 1 — day-level gap detection (robust sp_calc, adaptive threshold)
    Step 2 — per-segment sample rate (independent sp per contiguous block)
    Step 3 — per-segment jitter (ms) + residuals (concatenated across segments)

    utc_trim: list of UTCDateTime objects (from fetch_nc_timestamps).
      Used to derive data_start, data_end, missing_start, missing_end.
      If None, those fields are left empty.

    Returns a dict of all computed values needed for make_figure, write_stats,
    and the metrics CSV.
    """
    n      = len(t_sec)
    idx    = np.arange(n, dtype=float)
    dt_all = np.diff(t_sec)

    # ── Step 1: day-level sp, sp_fit, and gap detection ───────────────────
    dt_pos   = dt_all[np.isfinite(dt_all) & (dt_all > 0.0)]
    gap_cut  = np.percentile(dt_pos, 90.0) if dt_pos.size > 0 else np.inf
    dt_clean = dt_pos[dt_pos <= gap_cut]
    if dt_clean.size == 0:
        dt_clean = dt_pos
    sp_calc = float(np.median(dt_clean)) if dt_clean.size > 0 else float(sp_nominal)

    # Best-fit line through all timestamps → sp_fit is the slope
    coeffs_full = np.polyfit(idx, t_sec, 1)
    sp_fit      = float(coeffs_full[0])

    # expected_npts based on actual data window (data_end − data_start)
    expected_npts = round((t_sec[-1] - t_sec[0]) / sp_calc) + 1 if sp_calc > 0 else n
    tol    = max(5, int(0.001 * expected_npts))
    is_full = abs(n - expected_npts) <= tol

    if sp_calc >= 10.0:
        multiplier = 4.0 if is_full else 3.0
    elif sp_calc >= 0.5:
        multiplier = 3.5 if is_full else 2.5
    else:
        multiplier = 2.5 if is_full else 2.0
    gap_threshold = multiplier * sp_calc
    gap_idx = np.where(dt_all > gap_threshold)[0]

    # Gap details: duration and estimated missing samples per gap
    gap_durations  = dt_all[gap_idx]                                      # seconds
    gap_missing    = [max(0, round(float(d) / sp_calc) - 1)
                      for d in gap_durations]                             # samples
    gap_total_dur  = float(np.sum(gap_durations)) if gap_durations.size > 0 else 0.0
    gap_max_miss   = int(max(gap_missing)) if gap_missing else 0
    gap_total_miss = int(sum(gap_missing))

    # ── data_start, data_end, missing_start, missing_end ──────────────────
    if utc_trim and len(utc_trim) > 0:
        data_start_str = str(utc_trim[0])[:19] + "Z"
        data_end_str   = str(utc_trim[-1])[:19] + "Z"
        # Derive midnight of the day from first sample
        midnight       = UTCDateTime(str(utc_trim[0])[:10] + "T00:00:00Z")
        next_midnight  = midnight + 86400.0
        missing_start  = bool((utc_trim[0]  - midnight)      > sp_nominal)
        missing_end    = bool((next_midnight - utc_trim[-1])  > sp_nominal)
    else:
        data_start_str = ""
        data_end_str   = ""
        missing_start  = False
        missing_end    = False

    # ── Step 2: per-segment sample rates ──────────────────────────────────
    split_pts  = gap_idx + 1
    segments_t = np.split(t_sec, split_pts)

    seg_sp = []
    for seg in segments_t:
        if len(seg) < 2:
            seg_sp.append(sp_calc)
            continue
        dt_seg     = np.diff(seg)
        dt_pos_seg = dt_seg[dt_seg > 0]
        if dt_pos_seg.size == 0:
            seg_sp.append(sp_calc)
            continue
        cut  = np.percentile(dt_pos_seg, 90.0)
        dt_c = dt_pos_seg[dt_pos_seg <= cut]
        seg_sp.append(float(np.median(dt_c if dt_c.size > 0 else dt_pos_seg)))

    # ── Step 3: per-segment jitter (ms) and residuals ─────────────────────
    all_residuals  = []
    all_jitter_ms  = []
    seg_colour_arr = []

    for seg_id, (seg, sp_seg) in enumerate(zip(segments_t, seg_sp)):
        if len(seg) < 2:
            seg_colour_arr.extend([seg_id] * len(seg))
            continue
        seg_idx = np.arange(len(seg), dtype=float)
        seg_rel = seg - seg[0]
        coeffs  = np.polyfit(seg_idx, seg_rel, 1)
        t_fit   = np.polyval(coeffs, seg_idx)
        res     = seg_rel - t_fit
        all_residuals.extend(res.tolist())

        # Jitter in ms: |Δt − round(Δt/sp_seg) × sp_seg| × 1000
        dt_seg    = np.diff(seg)
        jitter_ms = np.abs(dt_seg - np.round(dt_seg / sp_seg) * sp_seg) * 1000.0
        all_jitter_ms.extend(jitter_ms.tolist())

        seg_colour_arr.extend([seg_id] * len(seg))

    residuals     = np.array(all_residuals)
    jitter_ms_arr = np.array(all_jitter_ms)
    seg_colours   = np.array(seg_colour_arr)

    def _safe_stat(arr, func):
        return float(func(arr)) if arr.size > 0 else 0.0

    res_std  = _safe_stat(residuals, np.std)
    res_p95  = _safe_stat(np.abs(residuals), lambda a: np.percentile(a, 95))
    skewness = (float(np.mean(((residuals - np.mean(residuals)) / res_std) ** 3))
                if res_std > 0 else 0.0)

    # Nominal residuals via round() approach.
    # For each sample, find the nearest nominal slot (integer multiples of
    # sp_nominal anchored at t_sec[0]) and compute the offset from it.
    #
    #   nominal_slot[i] = round(t_sec[i] / sp_nominal)
    #   res_nominal[i]  = t_sec[i] - nominal_slot[i] * sp_nominal
    #
    # Properties:
    #   • Always bounded to ±sp_nominal/2  (inherently avoids millions-of-bins)
    #   • Gap-aware: round() naturally skips missing slots; gaps appear as empty
    #     space in the scatter plot without any special filtering needed.
    #   • No per-segment anchoring required — the absolute slot assignment is
    #     self-consistent across the entire day.
    t0_nominal = t_sec[0]
    t_rel = t_sec - t0_nominal
    nominal_slots = np.round(t_rel / sp_nominal)
    res_nominal   = t_rel - nominal_slots * sp_nominal
    # nominal_slots are returned so Panel 4 can use them as x-axis,
    # causing gaps to appear as empty space (missing slot numbers).
    nominal_slots_int = nominal_slots.astype(int)

    jitter_mean_ms   = _safe_stat(jitter_ms_arr, np.mean)
    jitter_median_ms = _safe_stat(jitter_ms_arr, np.median)
    jitter_std_ms    = _safe_stat(jitter_ms_arr, np.std)
    jitter_p95_ms    = _safe_stat(jitter_ms_arr, lambda a: np.percentile(a, 95))
    jitter_max_ms    = _safe_stat(jitter_ms_arr, np.max)

    return {
        "n":               n,
        "idx":             idx,
        "dt_all":          dt_all,
        "sp_calc":         sp_calc,
        "sp_fit":          sp_fit,
        "sp_nominal":      sp_nominal,
        "expected_npts":   expected_npts,
        "is_full":         is_full,
        "multiplier":      multiplier,
        "gap_threshold":   gap_threshold,
        "gap_idx":         gap_idx,
        "gap_durations":   gap_durations,
        "gap_missing":     gap_missing,
        "gap_total_dur":   gap_total_dur,
        "gap_max_miss":    gap_max_miss,
        "gap_total_miss":  gap_total_miss,
        "data_start":      data_start_str,
        "data_end":        data_end_str,
        "missing_start":   missing_start,
        "missing_end":     missing_end,
        "segments_t":      segments_t,
        "seg_sp":          seg_sp,
        "seg_colours":     seg_colours,
        "residuals":       residuals,
        "res_nominal":     res_nominal,
        "nominal_slots":   nominal_slots_int,
        "jitter_ms_arr":   jitter_ms_arr,
        "res_std":         res_std,
        "res_p95":         res_p95,
        "skewness":        skewness,
        "jitter_mean_ms":  jitter_mean_ms,
        "jitter_median_ms":jitter_median_ms,
        "jitter_std_ms":   jitter_std_ms,
        "jitter_p95_ms":   jitter_p95_ms,
        "jitter_max_ms":   jitter_max_ms,
    }


# ── Per-day figure ───────────────────────────────────────────────────────────
def make_figure(t_sec, s, station, date_str, out_dir):
    """
    5-panel figure:
      Panel 1 — Histogram of timing residuals from best-fit line (ms, count)
      Panel 2 — Histogram of residuals from nominal line (ms, count)
      Panel 3 — Residuals from best-fit scatter over time (colour by segment)
      Panel 4 — Residuals from nominal scatter over time
      Panel 5 — Δt scatter with gap threshold, sp_calc, sp_fit, sp_nominal
    """
    fig, axes = plt.subplots(5, 1, figsize=(10, 14))
    fig.suptitle(f"{station}  |  {date_str}", fontsize=10, fontweight="bold")
    for ax in axes:
        ax.grid(linewidth=0.4, linestyle="--", alpha=0.5)
        ax.tick_params(labelsize=8)

    res_ms = s["residuals"] * 1000.0

    def _fd_bins(arr):
        """Freedman-Diaconis bin count."""
        if arr.size < 2:
            return 50
        q75, q25 = np.percentile(arr, [75, 25])
        iqr = q75 - q25
        bw  = 2.0 * iqr * (arr.size ** (-1.0 / 3.0)) if iqr > 0 else 0
        span = arr.max() - arr.min()
        return min(500, max(10, int(span / bw))) if bw > 0 else 50

    # ── Panel 1: histogram of residuals (count) ────────────────────────────
    ax = axes[0]
    if res_ms.size > 1:
        ax.hist(res_ms, bins=_fd_bins(res_ms),
                color=C_LINE, alpha=0.6, edgecolor="none")

        # Gaussian overlay scaled to count
        mu, sigma = float(np.mean(res_ms)), float(np.std(res_ms))
        if sigma > 0:
            x_g  = np.linspace(mu - 4 * sigma, mu + 4 * sigma, 300)
            # Scale PDF to match count histogram: PDF × n × bin_width
            bw_est = (res_ms.max() - res_ms.min()) / _fd_bins(res_ms)
            y_g  = (res_ms.size * bw_est *
                    (1.0 / (sigma * np.sqrt(2 * np.pi))) *
                    np.exp(-0.5 * ((x_g - mu) / sigma) ** 2))
            ax.plot(x_g, y_g, color=C_FIT, linewidth=1.2,
                    label=f"Gaussian  σ={sigma:.2f} ms")

        ax.axvline(0,                    color=C_ZERO, linewidth=0.8)
        ax.axvline( s["res_std"] * 1e3,  color=C_FIT,  linewidth=0.8, linestyle="--",
                    label=f"±1σ = {s['res_std']*1e3:.2f} ms")
        ax.axvline(-s["res_std"] * 1e3,  color=C_FIT,  linewidth=0.8, linestyle="--")
        ax.axvline( s["res_p95"] * 1e3,  color=C_P95,  linewidth=0.8, linestyle=":",
                    label=f"p95 = {s['res_p95']*1e3:.2f} ms")
        ax.axvline(-s["res_p95"] * 1e3,  color=C_P95,  linewidth=0.8, linestyle=":")

    ax.set_xlabel("Residual (ms)", fontsize=8)
    ax.set_ylabel("Count", fontsize=8)
    sp_fit_dev_ms = (s["sp_fit"] - s["sp_nominal"]) * 1000.0
    ax.set_title(
        f"Timing residuals (from best-fit line)  |  "
        f"sp_fit={s['sp_fit']:.6f} s  ({sp_fit_dev_ms:+.2f} ms vs nominal)  |  "
        f"n={s['n']}  gaps={len(s['gap_idx'])}  skew={s['skewness']:.2f}",
        fontsize=8)
    ax.legend(fontsize=7, loc="upper right")

    # ── Panel 2: histogram of nominal residuals (round() approach) ───────────
    # res_nominal[i] = t_rel[i] - round(t_rel[i] / sp_nominal) * sp_nominal
    # Bounded to ±sp_nominal/2 by construction.  Same visual style as Panel 1:
    # Gaussian overlay + ±1σ and ±p95 reference lines.
    # Vertical dashed lines at ±sp_nominal/2 show the hard bounds.
    ax = axes[1]
    res_nom_ms = s["res_nominal"] * 1000.0
    half_bound_ms = s["sp_nominal"] * 1000.0 / 2.0

    rn_std  = float(np.std(res_nom_ms))   if res_nom_ms.size > 1 else 0.0
    rn_p95  = float(np.percentile(np.abs(res_nom_ms), 95)) if res_nom_ms.size > 1 else 0.0

    if res_nom_ms.size > 1:
        ax.hist(res_nom_ms, bins=_fd_bins(res_nom_ms),
                color=C_SP, alpha=0.6, edgecolor="none")
        # Gaussian overlay
        mu_n, sigma_n = float(np.mean(res_nom_ms)), rn_std
        if sigma_n > 0:
            x_g  = np.linspace(mu_n - 4 * sigma_n, mu_n + 4 * sigma_n, 300)
            bw_est = (res_nom_ms.max() - res_nom_ms.min()) / _fd_bins(res_nom_ms)
            y_g  = (res_nom_ms.size * bw_est *
                    (1.0 / (sigma_n * np.sqrt(2 * np.pi))) *
                    np.exp(-0.5 * ((x_g - mu_n) / sigma_n) ** 2))
            ax.plot(x_g, y_g, color=C_FIT, linewidth=1.2,
                    label=f"Gaussian  σ={sigma_n:.2f} ms")

    ax.axvline(0,          color=C_ZERO, linewidth=0.8)
    ax.axvline( rn_std,    color=C_FIT,  linewidth=0.8, linestyle="--",
                label=f"±1σ = {rn_std:.2f} ms")
    ax.axvline(-rn_std,    color=C_FIT,  linewidth=0.8, linestyle="--")
    ax.axvline( rn_p95,    color=C_P95,  linewidth=0.8, linestyle=":",
                label=f"p95 = {rn_p95:.2f} ms")
    ax.axvline(-rn_p95,    color=C_P95,  linewidth=0.8, linestyle=":")

    ax.xaxis.set_major_locator(mticker.MaxNLocator(8))
    ax.set_xlabel("Residual from nominal (ms)", fontsize=8)
    ax.set_ylabel("Count", fontsize=8)
    ax.set_title(
        f"Residuals from nominal (round() approach)  |  "
        f"sp_nominal={s['sp_nominal']:.6f} s  |  "
        f"σ={rn_std:.2f} ms  p95={rn_p95:.2f} ms",
        fontsize=8)
    ax.legend(fontsize=7, loc="upper right")

    # ── Panel 3: residuals from best-fit scatter, colour by segment ──────────
    ax = axes[2]
    n_segs = len(s["segments_t"])
    for seg_id in range(n_segs):
        mask = s["seg_colours"] == seg_id
        if not mask.any():
            continue
        colour = SEG_PALETTE[seg_id % len(SEG_PALETTE)]
        label  = (f"seg {seg_id}  sp={s['seg_sp'][seg_id]:.4f}s"
                  if n_segs > 1 else None)
        ax.scatter(s["idx"][mask], res_ms[mask],
                   s=0.5, alpha=0.4, color=colour, rasterized=True, label=label)
    ax.axhline(0, color=C_ZERO, linewidth=0.8)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(8))
    ax.xaxis.set_major_locator(mticker.MaxNLocator(8))
    ax.set_xlabel("Sample index", fontsize=8)
    ax.set_ylabel("Residual (ms)", fontsize=8)
    ax.set_title("Residuals from best-fit over time (colour = segment)", fontsize=8)
    if n_segs > 1:
        ax.legend(fontsize=6, loc="upper right", markerscale=5)

    # ── Panel 4: residuals from nominal scatter vs nominal slot ──────────────
    # X-axis = nominal slot number (integer multiple of sp_nominal from t0).
    # Because missing samples have no slot entry, gaps appear as empty regions
    # on the x-axis rather than collapsed into consecutive sample indices.
    # Y-axis bounded ±sp_nominal/2 by construction of the round() approach.
    ax = axes[3]
    ax.scatter(s["nominal_slots"], res_nom_ms,
               s=0.5, alpha=0.4, color=C_SP, rasterized=True)
    ax.axhline(0,               color=C_ZERO,   linewidth=0.8)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(8))
    ax.xaxis.set_major_locator(mticker.MaxNLocator(8))
    ax.set_xlabel("Nominal slot number", fontsize=8)
    ax.set_ylabel("Residual from nominal (ms)", fontsize=8)
    ax.set_title(
        "Residuals from nominal over time (round() approach)  "
        "— x = nominal slot  |  gaps = empty space",
        fontsize=8)

    # ── Panel 5: gap timeline — vertical bands at gap locations ──────────
    ax = axes[4]
    gap_idx  = s["gap_idx"]
    n_gaps   = len(gap_idx)
    ax.set_xlim(0, s["n"])
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlabel("Sample index", fontsize=8)
    ax.set_title(
        f"Gap timeline  |  {n_gaps} gap(s)  |  "
        f"threshold={s['gap_threshold']:.3f} s ({s['multiplier']:.1f}×)  |  "
        f"sp_calc={s['sp_calc']:.6f} s  sp_fit={s['sp_fit']:.6f} s  "
        f"sp_nom={s['sp_nominal']:.6f} s",
        fontsize=8)
    if n_gaps == 0:
        ax.text(0.5, 0.5, "no gaps detected", transform=ax.transAxes,
                ha="center", va="center", fontsize=9, color=C_SP)
    else:
        for k, (gi, dur, miss) in enumerate(
                zip(gap_idx, s["gap_durations"], s["gap_missing"])):
            # shade from last sample before gap to first sample after
            ax.axvspan(gi, gi + 1, color=C_GAP, alpha=0.7)
            ax.text(gi + 0.5, 0.65,
                    f"gap {k+1}\n{float(dur):.1f}s\n~{miss} missing",
                    ha="center", va="center", fontsize=6, color="white",
                    fontweight="bold")

    plt.tight_layout()
    out_path = os.path.join(out_dir, "timing_diag.png")
    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    print(f"  Figure saved: {out_path}")
    return out_path


# ── Stats text file ──────────────────────────────────────────────────────────
def write_stats(s, sp_alert, alert_thr, station, date_str, out_dir):
    lines = [
        "Timing Diagnostic Summary",
        f"Station  : {station}",
        f"Date     : {date_str}",
        "",
        "Sample counts",
        f"  npts             : {s['n']}",
        f"  expected_npts    : {s['expected_npts']}",
        f"  is_full          : {s['is_full']}",
        f"  data_start       : {s['data_start']}",
        f"  data_end         : {s['data_end']}",
        f"  missing_start    : {s['missing_start']}",
        f"  missing_end      : {s['missing_end']}",
        "",
        "Gap detection",
        f"  gaps detected    : {len(s['gap_idx'])}",
        f"  segments         : {len(s['segments_t'])}",
        f"  multiplier       : {s['multiplier']:.1f}×",
        f"  gap_threshold    : {s['gap_threshold']:.6f} s",
    ]
    for i, (dur, miss) in enumerate(zip(s["gap_durations"], s["gap_missing"])):
        lines.append(f"  gap {i+1}            : {float(dur):.1f} s  (~{miss} missing samples)")
    for i, (seg, sp_seg) in enumerate(zip(s["segments_t"], s["seg_sp"])):
        lines.append(f"  segment {i}         : {len(seg)} pts  sp={sp_seg:.6f} s  "
                     f"sr={1.0/sp_seg:.6f} Hz")
    lines += [
        "",
        "Day-level sample period",
        f"  sp_calc (robust) : {s['sp_calc']:.6f} s",
        f"  sp_fit (ls slope): {s['sp_fit']:.6f} s",
        f"  sp_nominal       : {s['sp_nominal']:.6f} s",
        f"  |deviation|      : {abs(s['sp_calc']-s['sp_nominal']):.6f} s  "
        f"(alert threshold: {alert_thr:.3f} s)",
        f"  sp_alert         : {sp_alert}",
        "",
        "Jitter metric  |Δti − round(Δti/sp_seg) × sp_seg|  (ms)",
        f"  mean             : {s['jitter_mean_ms']:.4f} ms",
        f"  median           : {s['jitter_median_ms']:.4f} ms",
        f"  std              : {s['jitter_std_ms']:.4f} ms",
        f"  p95              : {s['jitter_p95_ms']:.4f} ms",
        f"  max              : {s['jitter_max_ms']:.4f} ms",
        "",
        "Timing residuals (from per-segment best-fit line)",
        f"  std              : {s['res_std']*1000:.4f} ms",
        f"  p95              : {s['res_p95']*1000:.4f} ms",
        f"  skewness         : {s['skewness']:.4f}",
        f"  interpretation   : "
        f"{'symmetric (random jitter)' if abs(s['skewness']) < 0.5 else 'skewed — possible systematic drift'}",
    ]

    # Edge cases section — human-readable flags for unusual characteristics
    flags = []
    if len(s["gap_idx"]) > 0:
        flags.append(
            f"GAPS: {len(s['gap_idx'])} gap(s) detected  "
            f"total={s['gap_total_dur']:.1f} s  missing≈{s['gap_total_miss']} samples")
    if not s["is_full"]:
        flags.append(
            f"INCOMPLETE: {s['n']} samples present, expected {s['expected_npts']}")
    if sp_alert:
        flags.append(
            f"SP_ALERT: sp_calc={s['sp_calc']:.6f} s  "
            f"deviation={abs(s['sp_calc']-s['sp_nominal']):.6f} s  "
            f"threshold={alert_thr:.3f} s")
    if s["missing_start"]:
        flags.append(f"MISSING_START: data does not begin at midnight")
    if s["missing_end"]:
        flags.append(f"MISSING_END: data does not reach next midnight")
    if abs(s["skewness"]) > 1.0:
        flags.append(f"SKEWED_RESIDUALS: skewness={s['skewness']:.4f}")
    lines += ["", "Edge Cases"]
    if flags:
        for flag in flags:
            lines.append(f"  ! {flag}")
    else:
        lines.append("  (none — day appears nominal)")

    out_path = os.path.join(out_dir, "timing_stats.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Stats saved: {out_path}")
    return out_path


# ── CSV helpers ──────────────────────────────────────────────────────────────
def _metrics_csv_path(station):
    short = station.split("-")[0]   # e.g. RS01SLBS
    return os.path.join(OUT_ROOT, "metrics", f"{short}_metrics.csv")

def _load_existing_keys(csv_path):
    """Return set of (station, date) strings already recorded."""
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            keys.add((row["station"], row["date"]))
    return keys

def _append_metrics_row(csv_path, row_dict):
    exists = os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(row_dict)

def _no_data_row(station, date_str, deployment, sp_nominal):
    return {
        "date": date_str, "station": station, "deployment": deployment,
        "has_data": False,
        "data_start": "", "data_end": "", "missing_start": "", "missing_end": "",
        "n_points": 0, "expected_npts": 0, "is_full": False,
        "n_gaps": 0, "n_segments": 0,
        "gap_total_duration_s": "", "gap_max_missing": "", "gap_total_missing": "",
        "sp_calc": "", "sp_fit": "",
        "sp_nominal": round(sp_nominal, 6) if sp_nominal else "",
        "sp_deviation": "", "sp_fit_deviation": "", "sp_calc_minus_fit": "",
        "sp_alert": False,
        "jitter_mean_ms": "", "jitter_median_ms": "", "jitter_std_ms": "",
        "jitter_p95_ms": "", "jitter_max_ms": "",
        "res_std_ms": "", "res_p95_ms": "", "skewness": "",
        "figure_generated": False,
    }

def _edge_case_log_path(station):
    short = station.split("-")[0]
    return os.path.join(OUT_ROOT, "metrics", f"{short}_edge_cases.log")

def _log_edge_case(station, date_str, kind, detail):
    """
    Append one line to the station's edge case log.

    kind   : one of SKIP | ANOMALY | WARN
    detail : human-readable description

    Format (tab-separated):
      <date>  <station>  <kind>  <detail>
    """
    log_path = _edge_case_log_path(station)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(log_path, "a") as f:
        f.write(f"{date_str}\t{station}\t{kind}\t{detail}\t[logged {timestamp}]\n")


# ── Collect mode ─────────────────────────────────────────────────────────────
def collect_mode(args):
    """
    Batch loop: for each (station, date) in the requested range, fetch
    timestamps from OOI, run compute_stats, append to CSV, and optionally
    generate a per-day figure on anomaly.
    """
    run      = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))
    stations = args.station if args.station else STATIONS

    abs_floor = float(run.get("sp_alert_abs_floor", [0.05])[0])
    rel_frac  = float(run.get("sp_alert_rel_frac",  [0.05])[0])

    date     = UTCDateTime(args.start + "T00:00:00Z")
    end_date = UTCDateTime(args.end   + "T00:00:00Z")
    total    = int((end_date - date) / 86400.0) + 1

    day_num = 0
    while date <= end_date:
        day_num  += 1
        date_str  = str(date)[:10]
        print(f"\n{'='*60}")
        print(f"  Day {day_num}/{total}  —  {date_str}")

        for station in stations:
            print(f"\n  [{station}]")
            csv_path = _metrics_csv_path(station)
            existing = _load_existing_keys(csv_path)

            if (station, date_str) in existing:
                print(f"    skip — already in CSV")
                continue

            # Deployment detection
            try:
                dep_info   = get_deployment_for_date(station, date, PARAM_PATH)
            except ValueError as e:
                print(f"    skip — {e}")
                _log_edge_case(station, date_str, "SKIP",
                               f"no deployment covering this date — {e}")
                _append_metrics_row(csv_path,
                    _no_data_row(station, date_str, 0, 0))
                continue

            deployment = dep_info["deployment"]
            sp_nominal = dep_info["sp_nominal"]

            # Fetch
            try:
                _, t_sec, utc_trim, _ = fetch_nc_timestamps(
                    station, date, date + 86400.0, deployment, run)
            except Exception as e:
                print(f"    no data — {e}")
                _log_edge_case(station, date_str, "SKIP",
                               f"fetch failed — {e}")
                _append_metrics_row(csv_path,
                    _no_data_row(station, date_str, deployment, sp_nominal))
                continue

            if len(t_sec) < 2:
                print(f"    too few points ({len(t_sec)})")
                _log_edge_case(station, date_str, "SKIP",
                               f"too few points returned by API ({len(t_sec)})")
                _append_metrics_row(csv_path,
                    _no_data_row(station, date_str, deployment, sp_nominal))
                continue

            # Compute
            s = compute_stats(t_sec, sp_nominal, utc_trim=utc_trim)

            alert_thr = max(abs_floor, rel_frac * sp_nominal)
            sp_dev    = abs(s["sp_calc"] - sp_nominal)
            sp_alert  = sp_dev >= alert_thr
            anomaly   = len(s["gap_idx"]) > 0 or not s["is_full"] or sp_alert

            # Log anomaly details
            if anomaly:
                parts = []
                if len(s["gap_idx"]) > 0:
                    parts.append(f"gaps={len(s['gap_idx'])} total_dur={s['gap_total_dur']:.1f}s missing≈{s['gap_total_miss']}")
                if not s["is_full"]:
                    parts.append(f"incomplete n={s['n']} expected={s['expected_npts']}")
                if sp_alert:
                    parts.append(f"sp_alert sp_calc={s['sp_calc']:.6f}s dev={sp_dev:.6f}s")
                if s["missing_start"]:
                    parts.append("missing_start")
                if s["missing_end"]:
                    parts.append("missing_end")
                _log_edge_case(station, date_str, "ANOMALY", "  |  ".join(parts))

            # Per-day figure on anomaly
            fig_generated = False
            if anomaly:
                fig_dir = os.path.join(
                    OUT_ROOT, "figures", "per_day", f"{station}_{date_str}")
                os.makedirs(fig_dir, exist_ok=True)
                make_figure(t_sec, s, station, date_str, fig_dir)
                write_stats(s, sp_alert, alert_thr, station, date_str, fig_dir)
                fig_generated = True

            row = {
                "date":                date_str,
                "station":             station,
                "deployment":          deployment,
                "has_data":            True,
                "data_start":          s["data_start"],
                "data_end":            s["data_end"],
                "missing_start":       s["missing_start"],
                "missing_end":         s["missing_end"],
                "n_points":            s["n"],
                "expected_npts":       s["expected_npts"],
                "is_full":             s["is_full"],
                "n_gaps":              len(s["gap_idx"]),
                "n_segments":          len(s["segments_t"]),
                "gap_total_duration_s": round(s["gap_total_dur"], 2),
                "gap_max_missing":     s["gap_max_miss"],
                "gap_total_missing":   s["gap_total_miss"],
                "sp_calc":             round(s["sp_calc"], 6),
                "sp_fit":              round(s["sp_fit"],  6),
                "sp_nominal":          round(sp_nominal, 6),
                "sp_deviation":        round(sp_dev, 6),
                "sp_fit_deviation":    round(s["sp_fit"] - sp_nominal, 6),
                "sp_calc_minus_fit":   round(s["sp_calc"] - s["sp_fit"], 6),
                "sp_alert":            sp_alert,
                "jitter_mean_ms":      round(s["jitter_mean_ms"],   4),
                "jitter_median_ms":    round(s["jitter_median_ms"], 4),
                "jitter_std_ms":       round(s["jitter_std_ms"],    4),
                "jitter_p95_ms":       round(s["jitter_p95_ms"],    4),
                "jitter_max_ms":       round(s["jitter_max_ms"],    4),
                "res_std_ms":          round(s["res_std"] * 1000, 4),
                "res_p95_ms":          round(s["res_p95"] * 1000, 4),
                "skewness":            round(s["skewness"], 4),
                "figure_generated":    fig_generated,
            }
            _append_metrics_row(csv_path, row)
            print(f"    gaps={len(s['gap_idx'])}  "
                  f"sp_calc={s['sp_calc']:.4f}s  "
                  f"jitter_p95={s['jitter_p95_ms']:.2f}ms  "
                  f"anomaly={anomaly}  fig={fig_generated}")

        date += 86400.0


# ── Plot mode ─────────────────────────────────────────────────────────────────
def _load_metrics(station):
    """Load metrics CSV for a station. Returns list of row dicts."""
    csv_path = _metrics_csv_path(station)
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))

def _classify_day(row):
    """Return integer status code for the data calendar.

    is_full is ignored here because expected_npts is derived from the
    observed data window and is not a reliable indicator of completeness.
    Days that used to be classified as "incomplete" (code 1) or
    "gaps + incomplete" (code 3) now fall through to clean (5) or gaps (2).
    """
    if str(row["has_data"]).lower() in ("false", "0", ""):
        return 0   # no data
    n_gaps = int(row["n_gaps"]) if row["n_gaps"] else 0
    alert  = str(row["sp_alert"]).lower() in ("true", "1")
    if alert:
        return 4   # sp alert
    if n_gaps > 0:
        return 2   # gaps
    return 5       # clean


def plot_mode(args):
    """
    Generate four summary figures from the collected metrics CSVs.
    Output: output/diagnostics/figures/summary/
    """
    stations  = args.station if args.station else STATIONS
    out_dir   = os.path.join(OUT_ROOT, "figures", "summary")
    os.makedirs(out_dir, exist_ok=True)

    all_data = {st: _load_metrics(st) for st in stations}
    n_loaded = sum(len(v) for v in all_data.values())
    if n_loaded == 0:
        print("No metrics data found. Run --mode collect first.")
        return
    print(f"  Loaded {n_loaded} rows across {len(stations)} stations.")

    # Figure styling constants — readable at presentation/print sizes
    FIG_DPI       = 160
    SUPTITLE_FS   = 20
    TITLE_FS      = 15
    AXLABEL_FS    = 14
    TICK_FS       = 12
    LEGEND_FS     = 12
    MARKER_S      = 18
    MARKER_S_BIG  = 55

    def _station_range(rows):
        """Return 'YYYY-MM-DD → YYYY-MM-DD' covering rows with parseable dates."""
        ds = []
        for r in rows:
            try:
                ds.append(datetime.datetime.strptime(r["date"], "%Y-%m-%d"))
            except (ValueError, KeyError):
                continue
        if not ds:
            return "no data"
        return f"{min(ds):%Y-%m-%d} → {max(ds):%Y-%m-%d}  ({len(ds)} days)"

    def _style_time_axis(ax):
        _loc = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(_loc)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(_loc))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right",
                 fontsize=TICK_FS)
        plt.setp(ax.yaxis.get_majorticklabels(), fontsize=TICK_FS)

    def _short_name(station):
        for code in ("SLBS", "SUM1", "AXBS"):
            if code in station:
                return code
        return station

    def _dates_values(rows, col, has_data_only=True):
        """Extract (datetime, float) pairs from rows, skipping blanks."""
        dates, vals = [], []
        for r in rows:
            if has_data_only and str(r["has_data"]).lower() not in ("true", "1"):
                continue
            if r[col] == "":
                continue
            try:
                dates.append(datetime.datetime.strptime(r["date"], "%Y-%m-%d"))
                vals.append(float(r[col]))
            except (ValueError, KeyError):
                continue
        return dates, vals

    # ── Figure 1: sample rate over time ───────────────────────────────────
    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(18, 4.5 * len(stations)),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle("Daily sample rate (1 / sp_calc) — drift and alert days",
                 fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        dates, sp_vals = _dates_values(rows, "sp_calc")
        sr_vals  = [1.0 / sp for sp in sp_vals if sp > 0]
        dates_sr = [d for d, sp in zip(dates, sp_vals) if sp > 0]

        sp_noms = [float(r["sp_nominal"]) for r in rows
                   if r["sp_nominal"] not in ("", None)]
        sp_nom  = float(np.median(sp_noms)) if sp_noms else None

        # Fixed y-axis at ±10% of nominal sample rate
        y_lo = y_hi = None
        if sp_nom:
            nom_sr = 1.0 / sp_nom
            y_lo   = 0.9 * nom_sr
            y_hi   = 1.1 * nom_sr

        def _clip(val):
            if y_lo is None:
                return val, False
            if val > y_hi:
                return y_hi, True
            if val < y_lo:
                return y_lo, True
            return val, False

        # sp_calc — clip and annotate out-of-range
        sr_plot = []
        for d, sr in zip(dates_sr, sr_vals):
            v, clipped = _clip(sr)
            sr_plot.append(v)
            if clipped:
                ax.annotate(f"{sr:.4f}", xy=(d, v),
                            xytext=(0, 6 if sr > y_hi else -14),
                            textcoords="offset points",
                            ha="center", fontsize=TICK_FS - 2,
                            color=C_LINE)
        ax.scatter(dates_sr, sr_plot, s=MARKER_S, color=C_LINE, alpha=0.7,
                   edgecolor="none", label="sp_calc  (per-day median interval)")

        if sp_nom:
            ax.axhline(nom_sr, color=C_NOMINAL, linewidth=1.4,
                       linestyle=":",
                       label=f"nominal = {nom_sr:.4f} Hz")

        dates_fit, sp_fit_vals = _dates_values(rows, "sp_fit")
        sr_fit    = [1.0 / sp for sp in sp_fit_vals if sp > 0]
        dates_fit = [d for d, sp in zip(dates_fit, sp_fit_vals) if sp > 0]
        if dates_fit:
            sr_fit_plot = []
            for d, sr in zip(dates_fit, sr_fit):
                v, clipped = _clip(sr)
                sr_fit_plot.append(v)
                if clipped:
                    ax.annotate(f"{sr:.4f}", xy=(d, v),
                                xytext=(0, 6 if sr > y_hi else -14),
                                textcoords="offset points",
                                ha="center", fontsize=TICK_FS - 2,
                                color=C_FIT)
            ax.scatter(dates_fit, sr_fit_plot, s=MARKER_S, color=C_FIT,
                       alpha=0.6, edgecolor="none",
                       label="sp_fit  (least-squares slope)")

        alert_rows = [r for r in rows
                      if str(r.get("sp_alert", "")).lower() in ("true", "1")]
        a_dates, a_sp = _dates_values(alert_rows, "sp_calc")
        a_sr     = [1.0 / sp for sp in a_sp if sp > 0]
        a_dates  = [d for d, sp in zip(a_dates, a_sp) if sp > 0]
        if a_dates:
            a_plot = [_clip(sr)[0] for sr in a_sr]
            ax.scatter(a_dates, a_plot, s=MARKER_S_BIG, facecolor="none",
                       edgecolor=C_GAP, linewidth=1.6, zorder=5,
                       label=f"sp alert  ({len(a_dates)} days)")

        _style_time_axis(ax)
        if y_lo is not None:
            ax.set_ylim(y_lo, y_hi)
        ax.set_ylabel("Sample rate (Hz)  [±10% of nominal]",
                      fontsize=AXLABEL_FS)
        ax.set_title(f"{station}   |   {_station_range(rows)}",
                     fontsize=TITLE_FS, fontweight="bold")
        ax.legend(fontsize=LEGEND_FS, loc="best", framealpha=0.9)
        ax.grid(linewidth=0.5, linestyle="--", alpha=0.5)

    plt.tight_layout()
    p = os.path.join(out_dir, "sample_rate_over_time.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 1b: sample rate differences over time ──────────────────────
    fig, axes = plt.subplots(len(stations), 3,
                             figsize=(22, 4.2 * len(stations)),
                             sharex=False, squeeze=False)
    # Overall date range across all stations for the suptitle
    all_dates = []
    for st in stations:
        for r in all_data[st]:
            try:
                all_dates.append(
                    datetime.datetime.strptime(r["date"], "%Y-%m-%d"))
            except (ValueError, KeyError):
                continue
    rng_txt = (f"{min(all_dates):%Y-%m-%d} → {max(all_dates):%Y-%m-%d}"
               if all_dates else "no data")
    fig.suptitle(f"Sample-period deviations — drift diagnostics   ({rng_txt})",
                 fontsize=SUPTITLE_FS, fontweight="bold")

    diff_cols = [
        ("sp_deviation",      "sp_calc − sp_nominal (s)",  C_LINE,
         "|median interval − nominal|\n(typical-rate offset)"),
        ("sp_fit_deviation",  "sp_fit − sp_nominal (s)",   C_FIT,
         "day-average drift from nominal\n(signed)"),
        ("sp_calc_minus_fit", "sp_calc − sp_fit (s)",      C_NOMINAL,
         "median − mean interval\n(skewness signature, signed)"),
    ]

    for i, (row_axes, station) in enumerate(zip(axes, stations)):
        rows  = all_data[station]
        short = _short_name(station)
        for j, (ax, (col, ylabel, colour, desc)) in enumerate(
                zip(row_axes, diff_cols)):
            dates, vals = _dates_values(rows, col)

            # Robust y-limits from 2nd-98th percentile, expanded by 25%
            if vals:
                lo_p = float(np.percentile(vals, 2))
                hi_p = float(np.percentile(vals, 98))
                span = hi_p - lo_p if hi_p > lo_p else max(abs(hi_p), 1e-9)
                y_lo = lo_p - 0.25 * span
                y_hi = hi_p + 0.25 * span
                # Make sure zero is visible for signed metrics
                y_lo = min(y_lo, 0.0)
                y_hi = max(y_hi, 0.0)
            else:
                y_lo = y_hi = None

            plot_vals = []
            for d, v in zip(dates, vals):
                if y_lo is not None and v > y_hi:
                    plot_vals.append(y_hi)
                    ax.annotate(f"{v:.3g}", xy=(d, y_hi),
                                xytext=(0, 6), textcoords="offset points",
                                ha="center", fontsize=TICK_FS - 3,
                                color=colour)
                elif y_lo is not None and v < y_lo:
                    plot_vals.append(y_lo)
                    ax.annotate(f"{v:.3g}", xy=(d, y_lo),
                                xytext=(0, -14), textcoords="offset points",
                                ha="center", fontsize=TICK_FS - 3,
                                color=colour)
                else:
                    plot_vals.append(v)

            ax.scatter(dates, plot_vals, s=MARKER_S, color=colour, alpha=0.7,
                       edgecolor="none")
            if y_lo is not None:
                ax.set_ylim(y_lo, y_hi)
            _style_time_axis(ax)
            ax.set_ylabel(ylabel, fontsize=AXLABEL_FS)
            if i == 0:
                ax.set_title(desc, fontsize=TITLE_FS, fontweight="bold")
            if j == 0:
                ax.text(-0.14, 0.5, short, transform=ax.transAxes,
                        rotation=90, va="center", ha="center",
                        fontsize=TITLE_FS + 2, fontweight="bold")
            ax.grid(linewidth=0.5, linestyle="--", alpha=0.5)

    plt.tight_layout(rect=(0.02, 0, 1, 0.97))
    p = os.path.join(out_dir, "sample_rate_differences.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 1c: day-boundary offsets (per-day, self-contained) ─────────
    # For each day that has data:
    #   start_offset = seconds from midnight of the day to the first sample
    #   end_offset   = seconds from the last sample to midnight of next day
    # No cross-day arithmetic, so missing days don't contaminate anything.
    # Start offset uses the left y-axis (plotted in the BOTTOM band of the
    # figure). End offset uses the right y-axis (plotted in the TOP band of
    # the figure). Limits are expanded so the two traces never overlap.
    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(18, 5.0 * len(stations)),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle(
        "Day-boundary offsets  —  "
        "first-sample delay after midnight (left)  and  "
        "last-sample lag before next midnight (right)",
        fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        start_dates, start_off = [], []
        end_dates,   end_off   = [], []
        for r in rows:
            if str(r["has_data"]).lower() not in ("true", "1"):
                continue
            try:
                d = datetime.datetime.strptime(r["date"], "%Y-%m-%d")
            except (ValueError, KeyError):
                continue
            if r.get("data_start"):
                try:
                    t = datetime.datetime.strptime(
                        r["data_start"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                    start_dates.append(d)
                    start_off.append((t - d).total_seconds())
                except ValueError:
                    pass
            if r.get("data_end"):
                try:
                    t = datetime.datetime.strptime(
                        r["data_end"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                    next_mid = d + datetime.timedelta(days=1)
                    end_dates.append(d)
                    end_off.append((next_mid - t).total_seconds())
                except ValueError:
                    pass

        # Left y — start offset lives in the BOTTOM half of the figure.
        # Expand upper limit to 2x the data max so data sits in bottom half.
        l1, = ax.plot(start_dates, start_off, color=C_SP, linewidth=1.4,
                      marker="o", markersize=9,
                      markerfacecolor=C_SP, markeredgecolor="black",
                      markeredgewidth=0.6, alpha=0.9,
                      label="start offset  (s after midnight of day)")
        if start_off:
            s_max = max(start_off)
            s_min = min(start_off)
            s_pad = max((s_max - s_min) * 0.1, 1.0)
            ax.set_ylim(s_min - s_pad, (s_max + s_pad) * 2.0)
        ax.set_ylabel("Start offset (s)  —  lower is better",
                      fontsize=AXLABEL_FS, color=C_SP)
        ax.tick_params(axis="y", labelcolor=C_SP, labelsize=TICK_FS)
        _style_time_axis(ax)
        ax.grid(linewidth=0.5, linestyle="--", alpha=0.5)

        # Right y — end offset lives in the TOP half of the figure.
        # Expand lower limit to 2x below zero so data sits in top half.
        ax2 = ax.twinx()
        l2, = ax2.plot(end_dates, end_off, color=C_GAP, linewidth=1.4,
                       marker="s", markersize=9,
                       markerfacecolor=C_GAP, markeredgecolor="black",
                       markeredgewidth=0.6, alpha=0.9,
                       label="end offset  (s before midnight of next day)")
        if end_off:
            e_max = max(end_off)
            e_min = min(end_off)
            e_pad = max((e_max - e_min) * 0.1, 1.0)
            ax2.set_ylim((e_min - e_pad) - (e_max - e_min + e_pad) * 1.0,
                         e_max + e_pad)
        ax2.set_ylabel("End offset (s)  —  lower is better",
                       fontsize=AXLABEL_FS, color=C_GAP)
        ax2.tick_params(axis="y", labelcolor=C_GAP, labelsize=TICK_FS)

        ax.set_title(f"{station}   |   {_station_range(rows)}",
                     fontsize=TITLE_FS, fontweight="bold")
        ax.legend(handles=[l1, l2], fontsize=LEGEND_FS, loc="center right",
                  framealpha=0.9)

    plt.tight_layout()
    p = os.path.join(out_dir, "day_boundary_offsets.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 1d: sample rate & day start/end offsets ────────────────────
    # Left y: sp_calc, sp_fit lines.
    # Right y: start offset (sec since midnight of day) and
    #          end offset (sec before midnight of next day).
    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(18, 4.5 * len(stations)),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle("Sample period and day-boundary offsets  "
                 "(ideal: offsets → 0 s)",
                 fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        dates_calc, sp_calc_vals = _dates_values(rows, "sp_calc")
        dates_fit,  sp_fit_vals  = _dates_values(rows, "sp_fit")

        l1, = ax.plot(dates_calc, sp_calc_vals, color=C_LINE, linewidth=1.4,
                      label="sp_calc (s)")
        l2, = ax.plot(dates_fit, sp_fit_vals,  color=C_FIT, linewidth=1.4,
                      label="sp_fit (s)")
        _style_time_axis(ax)
        ax.set_ylabel("Sample period (s)", fontsize=AXLABEL_FS)
        ax.grid(linewidth=0.5, linestyle="--", alpha=0.5)

        # Day start/end offsets on twin axis
        start_dates, start_off = [], []
        end_dates,   end_off   = [], []
        for r in rows:
            if str(r["has_data"]).lower() not in ("true", "1"):
                continue
            try:
                d = datetime.datetime.strptime(r["date"], "%Y-%m-%d")
            except (ValueError, KeyError):
                continue
            if r.get("data_start"):
                try:
                    t = datetime.datetime.strptime(
                        r["data_start"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                    start_dates.append(d)
                    start_off.append((t - d).total_seconds())
                except ValueError:
                    pass
            if r.get("data_end"):
                try:
                    t = datetime.datetime.strptime(
                        r["data_end"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                    next_mid = d + datetime.timedelta(days=1)
                    end_dates.append(d)
                    end_off.append((next_mid - t).total_seconds())
                except ValueError:
                    pass

        ax2 = ax.twinx()
        l3, = ax2.plot(start_dates, start_off, color=C_SP, linewidth=1.4,
                       alpha=0.9,
                       label="start offset (s since midnight of day)")
        l4, = ax2.plot(end_dates, end_off, color=C_GAP, linewidth=1.4,
                       alpha=0.9,
                       label="end offset (s before midnight of next day)")
        ax2.set_ylabel("Day-boundary offset (s)", fontsize=AXLABEL_FS)
        ax2.tick_params(axis="y", labelsize=TICK_FS)

        ax.set_title(f"{station}   |   {_station_range(rows)}",
                     fontsize=TITLE_FS, fontweight="bold")
        ax.legend(handles=[l1, l2, l3, l4], fontsize=LEGEND_FS, loc="best",
                  framealpha=0.9)

    plt.tight_layout()
    p = os.path.join(out_dir, "sample_period_and_day_offsets.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 2: gap count over time ──────────────────────────────────────
    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(18, 4.5 * len(stations)),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle("Gaps per day — red = gapped, grey = no data, green = clean",
                 fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        dates_all, gaps_all, colours = [], [], []
        gap_dur_all, missing_all, has_data_all = [], [], []
        n_gap_days = n_clean = n_missing = 0
        for r in rows:
            try:
                d = datetime.datetime.strptime(r["date"], "%Y-%m-%d")
            except ValueError:
                continue
            has_data = str(r["has_data"]).lower() in ("true", "1")
            n_gaps   = int(r["n_gaps"]) if r["n_gaps"] else 0
            gap_dur  = float(r["gap_total_duration_s"]) \
                       if r.get("gap_total_duration_s") else 0.0
            try:
                sp_fit = float(r["sp_fit"]) if r.get("sp_fit") else 0.0
            except ValueError:
                sp_fit = 0.0
            missing = (gap_dur / sp_fit) if sp_fit > 0 else float("nan")
            dates_all.append(d)
            has_data_all.append(has_data)
            gaps_all.append(n_gaps if has_data else 0)
            gap_dur_all.append(gap_dur if has_data else 0.0)
            missing_all.append(missing if has_data else float("nan"))
            if not has_data:
                colours.append(C_ZERO); n_missing += 1
            elif n_gaps > 0:
                colours.append(C_GAP); n_gap_days += 1
            else:
                colours.append(C_SP); n_clean += 1

        # Grey-out no-data days as full-height vertical spans.
        # ax.bar centers each bar on its date with width=1 day, spanning
        # [date − 0.5d, date + 0.5d] — match that exactly so the grey
        # columns line up one-to-one with the missing days.
        one_day  = datetime.timedelta(days=1)
        half_day = datetime.timedelta(hours=12)
        for d, has in zip(dates_all, has_data_all):
            if not has:
                ax.axvspan(d - half_day, d + half_day, color=C_ZERO,
                           alpha=0.35, linewidth=0, zorder=0)

        bars = ax.bar(dates_all, gaps_all, color=colours, width=1.0, linewidth=0)

        # Horizontal gap-duration labels near the x-axis, staggered up
        # through consecutive gap days so they don't overlap.
        from matplotlib.transforms import blended_transform_factory
        tx = blended_transform_factory(ax.transData, ax.transAxes)
        stagger_levels = [0.03, 0.09, 0.15, 0.21]
        level_idx = 0
        prev_date = None
        for d, n, dur in zip(dates_all, gaps_all, gap_dur_all):
            if not (n > 0 and dur > 0):
                prev_date = None
                continue
            if prev_date is not None and (d - prev_date).days == 1:
                level_idx = (level_idx + 1) % len(stagger_levels)
            else:
                level_idx = 0
            ax.text(d, stagger_levels[level_idx],
                    f"{dur:.1f}s", transform=tx,
                    ha="center", va="bottom",
                    fontsize=TICK_FS - 2, color="black",
                    rotation=0, zorder=6)
            prev_date = d

        _style_time_axis(ax)
        ax.set_ylabel("Gap segments per day", fontsize=AXLABEL_FS)

        # Twin y-axis: samples missing if sp_fit were the true rate
        ax2 = ax.twinx()
        ax2.plot(dates_all, missing_all, color=C_FIT, linewidth=1.4, alpha=0.9,
                 marker="o", markersize=7, markerfacecolor=C_FIT,
                 markeredgecolor="black", markeredgewidth=0.5,
                 label="missing samples  (gap_total_duration_s / sp_fit)")
        ax2.set_ylabel("Missing samples (via sp_fit)", fontsize=AXLABEL_FS,
                       color=C_FIT)
        ax2.tick_params(axis="y", labelcolor=C_FIT, labelsize=TICK_FS)
        ax2.legend(fontsize=LEGEND_FS, loc="upper right", framealpha=0.9)

        ax.set_title(
            f"{station}   |   {_station_range(rows)}   |   "
            f"gapped: {n_gap_days}   no-data: {n_missing}",
            fontsize=TITLE_FS, fontweight="bold")
        ax.grid(linewidth=0.5, linestyle="--", alpha=0.5, axis="y")

    plt.tight_layout()
    p = os.path.join(out_dir, "gap_count_over_time.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 3: jitter p95 over time ────────────────────────────────────
    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(18, 4.5 * len(stations)),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle(
        "Timing jitter p95  |Δtᵢ − round(Δtᵢ / sp_seg) · sp_seg|   (lower is better)",
        fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        dates, j95 = _dates_values(rows, "jitter_p95_ms")
        ax.plot(dates, j95, color=C_LINE, linewidth=1.2, alpha=0.8,
                marker="o", markersize=8, markerfacecolor=C_LINE,
                markeredgecolor="black", markeredgewidth=0.5,
                label="p95 jitter")
        if j95:
            med = float(np.median(j95))
            ax.axhline(med, color=C_NOMINAL, linewidth=1.4, linestyle=":",
                       label=f"median = {med:.2f} ms")
        _style_time_axis(ax)
        ax.set_ylabel("Jitter p95 (ms)", fontsize=AXLABEL_FS)
        ax.set_title(f"{station}   |   {_station_range(rows)}",
                     fontsize=TITLE_FS, fontweight="bold")
        ax.legend(fontsize=LEGEND_FS, loc="best", framealpha=0.9)
        ax.grid(linewidth=0.5, linestyle="--", alpha=0.5)

    plt.tight_layout()
    p = os.path.join(out_dir, "jitter_over_time.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 4: sample count per day ────────────────────────────────────
    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(18, 4.5 * len(stations)),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle("Sample count per day  —  green = full day, red = samples missing",
                 fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        sp_noms = [float(r["sp_nominal"]) for r in rows
                   if r["sp_nominal"] not in ("", None)]
        sp_nom  = float(np.median(sp_noms)) if sp_noms else None
        nominal_full = int(round(86400.0 / sp_nom)) if sp_nom else None

        dates_all, npts_all, colours = [], [], []
        for r in rows:
            try:
                d = datetime.datetime.strptime(r["date"], "%Y-%m-%d")
            except ValueError:
                continue
            has_data = str(r["has_data"]).lower() in ("true", "1")
            npts     = int(r["n_points"]) if r["n_points"] else 0
            dates_all.append(d)
            npts_all.append(npts)
            if not has_data:
                colours.append(C_ZERO)
            elif nominal_full and npts >= nominal_full - 1:
                colours.append(C_SP)
            else:
                colours.append(C_GAP)
        ax.bar(dates_all, npts_all, color=colours, width=1.0,
               edgecolor="black", linewidth=0.3)
        if nominal_full:
            ax.axhline(nominal_full, color=C_NOMINAL, linewidth=1.4,
                       linestyle="--",
                       label=f"nominal full day = {nominal_full:,} samples "
                             f"(86400 s / {sp_nom:.4f} s)")

        # Per-day expected count from the fitted sample period:
        #   expected_fit = 86400 / sp_fit[day]
        fit_dates, fit_exp = [], []
        for r in rows:
            try:
                d      = datetime.datetime.strptime(r["date"], "%Y-%m-%d")
                sp_fit = float(r["sp_fit"]) if r["sp_fit"] not in ("", None) else 0.0
            except (ValueError, KeyError):
                continue
            if sp_fit > 0:
                fit_dates.append(d)
                fit_exp.append(86400.0 / sp_fit)
        if fit_dates:
            ax.plot(fit_dates, fit_exp, color=C_FIT, linewidth=1.6,
                    label="expected from sp_fit  (86400 / sp_fit per day)")
        _style_time_axis(ax)
        ax.set_ylabel("Samples per day", fontsize=AXLABEL_FS)
        ax.set_title(f"{station}   |   {_station_range(rows)}",
                     fontsize=TITLE_FS, fontweight="bold")
        ax.legend(fontsize=LEGEND_FS, loc="best", framealpha=0.9)
        ax.grid(linewidth=0.5, linestyle="--", alpha=0.5, axis="y")

    plt.tight_layout()
    p = os.path.join(out_dir, "sample_count_over_time.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")

    # ── Figure 5: data quality calendar  (one strip per station, real dates)
    from matplotlib.patches import Patch

    # Only the codes currently emitted by _classify_day
    status_codes = [0, 2, 4, 5]

    fig, axes = plt.subplots(len(stations), 1,
                             figsize=(20, 2.2 * len(stations) + 1.4),
                             sharex=False)
    if len(stations) == 1:
        axes = [axes]
    fig.suptitle("Data quality calendar  —  one day = one bar, "
                 "x-axis zoomed to available dates per station",
                 fontsize=SUPTITLE_FS, fontweight="bold")

    for ax, station in zip(axes, stations):
        rows = all_data[station]
        if not rows:
            ax.set_title(f"{station}   |   no data",
                         fontsize=TITLE_FS, fontweight="bold")
            ax.axis("off")
            continue

        day_status = {}
        for r in rows:
            try:
                d = datetime.datetime.strptime(r["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            day_status[d] = _classify_day(r)

        if not day_status:
            ax.set_title(f"{station}   |   no dated rows",
                         fontsize=TITLE_FS, fontweight="bold")
            ax.axis("off")
            continue

        counts = {c: 0 for c in status_codes}
        for code in status_codes:
            xranges = [(mdates.date2num(d), 1.0)
                       for d, s in day_status.items() if s == code]
            counts[code] = len(xranges)
            if xranges:
                ax.broken_barh(xranges, (0, 1),
                               facecolors=CAL_COLORS[code],
                               edgecolors="black",
                               linewidth=0.3)

        d_min = min(day_status)
        d_max = max(day_status) + datetime.timedelta(days=1)
        ax.set_xlim(mdates.date2num(d_min), mdates.date2num(d_max))
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        _style_time_axis(ax)
        for spine in ax.spines.values():
            spine.set_visible(False)

        total = sum(counts.values())
        ax.set_title(
            f"{station}   |   {total} days   "
            f"(clean: {counts[5]},  gaps: {counts[2]},  "
            f"sp_alert: {counts[4]},  no-data: {counts[0]})",
            fontsize=TITLE_FS, fontweight="bold")

    legend_handles = [Patch(facecolor=CAL_COLORS[c], edgecolor="white",
                            label=CAL_LABELS[c]) for c in status_codes]
    fig.legend(handles=legend_handles, loc="lower center",
               ncol=len(status_codes), fontsize=LEGEND_FS,
               frameon=False, bbox_to_anchor=(0.5, 0.0))

    plt.tight_layout(rect=(0, 0.06, 1, 0.97))
    p = os.path.join(out_dir, "data_calendar.png")
    fig.savefig(p, dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {p}")


# ── Hook for main pipeline (called from OOI_data_request_and_convert_mseed) ──
def run_diagnostic(t_sec, sp_nominal, station, date_str, out_root=None):
    """
    Called directly from OOI_data_request_and_convert_mseed.py when gaps or
    a sample-period alert are detected.  Accepts pre-fetched timestamps and
    runs the full compute_stats → make_figure → write_stats pipeline.

    Note: sp_alert and alert_thr are re-derived here from run_prest.txt
    defaults since the main pipeline does not pass them.
    """
    if out_root is None:
        out_root = OUT_ROOT

    s         = compute_stats(t_sec, sp_nominal)
    sp_dev    = abs(s["sp_calc"] - sp_nominal)
    alert_thr = max(0.05, 0.05 * sp_nominal)   # default thresholds
    sp_alert  = sp_dev >= alert_thr

    tag     = f"{station}_{date_str}"
    out_dir = os.path.join(out_root, "figures", "per_day", tag)
    os.makedirs(out_dir, exist_ok=True)

    make_figure(t_sec, s, station, date_str, out_dir)
    write_stats(s, sp_alert, alert_thr, station, date_str, out_dir)
    return out_dir


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="single",
                        choices=["single", "collect", "plot"],
                        help="Operating mode (default: single)")
    parser.add_argument("--station", nargs="+",
                        help="Station reference designator(s).  "
                             "collect/plot default to all 3 stations.")
    # single mode
    parser.add_argument("--date",
                        help="[single] YYYY-MM-DD")
    # collect mode
    parser.add_argument("--start",
                        help="[collect] Start date YYYY-MM-DD")
    parser.add_argument("--end",
                        help="[collect] End date YYYY-MM-DD")
    args = parser.parse_args()

    if args.mode == "single":
        if not args.date:
            parser.error("--date required for --mode single")
        station = (args.station[0] if args.station
                   else "RS01SLBS-MJ01A-06-PRESTA101")
        date    = UTCDateTime(args.date + "T00:00:00Z")

        run     = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))
        dep_info = get_deployment_for_date(station, date, PARAM_PATH)
        deployment = dep_info["deployment"]
        sp_nominal = dep_info["sp_nominal"]
        print(f"  Deployment {deployment}  |  sp_nominal={sp_nominal:.6f} s")

        _, t_sec, utc_trim, _ = fetch_nc_timestamps(
            station, date, date + 86400.0, deployment, run)

        s = compute_stats(t_sec, sp_nominal, utc_trim=utc_trim)

        abs_floor = float(run.get("sp_alert_abs_floor", [0.05])[0])
        rel_frac  = float(run.get("sp_alert_rel_frac",  [0.05])[0])
        alert_thr = max(abs_floor, rel_frac * sp_nominal)
        sp_alert  = abs(s["sp_calc"] - sp_nominal) >= alert_thr

        out_dir = os.path.join(
            OUT_ROOT, "figures", "per_day", f"{station}_{args.date}")
        os.makedirs(out_dir, exist_ok=True)
        make_figure(t_sec, s, station, args.date, out_dir)
        write_stats(s, sp_alert, alert_thr, station, args.date, out_dir)
        print(f"\nDone.  Output in: {out_dir}")

    elif args.mode == "collect":
        if not args.start or not args.end:
            parser.error("--start and --end required for --mode collect")
        collect_mode(args)

    elif args.mode == "plot":
        plot_mode(args)


if __name__ == "__main__":
    main()
