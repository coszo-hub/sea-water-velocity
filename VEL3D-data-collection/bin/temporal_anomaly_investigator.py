#!/usr/bin/env python3
"""
temporal_anomaly_investigator.py
═══════════════════════════════════════════════════════════════════════════════
Timestamp variability assessment for OOI PREST sensor timestamps.

Implements the procedure in
    Obsidian Vault/COSZO/timestamp_variability_assessment_plan.md

For a given day d and logged timestamps t'_j(d):
  Step 1 — Δt'_j = t'_{j+1} − t'_j
  Step 2 — Δt_FG(d) = median(Δt'_j)                (plain median — no percentile trim)
  Step 3 — Δi'_j = Δt'_j / Δt_FG(d);  Δi_j = round(Δi'_j)
  Step 4 — gap flagged where Δi_j > 1
  Step 5 — reconstructed index: i_0 = 0,  i_{j+1} = i_j + Δi_j
  Step 6 — OLS fit   t'_j = t_{i=0}(d) + i_j · Δt_true(d) + e_j
  Step 7 — jitter residual  e_j = t'_j − [t_{i=0}(d) + i_j · Δt_true(d)]
  Step 8 — jitter in ms       e_{j,ms} = 1000 · e_j    (stats: μ, σ, max|·|)
  Step 9 — jitter as fraction f_j = e_j / Δt_true(d)    (stats: μ, σ, max|·|)
  Step 10 — four diagnostic plots per day

Three modes:
  single  — one (station, date), writes CSV row + 4-panel figure + stats.txt
  collect — batch over a date range; one row per (station × date); figure
            on every row (all days are interesting for variability work)
  plot    — summary plots across the collected metrics
"""

import os
import sys
import csv
import argparse
import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from obspy import UTCDateTime

# Reuse OOI-fetch / deployment infrastructure from diagnose_timing.py
REPO_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_PATH   = os.path.join(REPO_ROOT, "bin")
PARAM_PATH = os.path.join(REPO_ROOT, "param")
OUT_ROOT   = os.path.join(REPO_ROOT, "output", "temporal_anomaly")

sys.path.insert(0, BIN_PATH)

from read_param import read_param
from diagnose_timing import (
    STATIONS,
    load_credentials,
    get_deployment_for_date,
    fetch_nc_timestamps,
)

# ── CSV schema ───────────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "date", "station", "deployment", "has_data",
    "data_start", "data_end",
    "n_points",
    "n_ideal",
    "true_missing",
    "sp_nominal",
    "dt_FG", "dt_true", "t_i0_offset_s",
    "n_gaps", "gap_total_missing",
    "n_gaps_raw", "gap_total_missing_raw",
    "jitter_mean_ms", "jitter_std_ms", "jitter_maxabs_ms",
    "frac_mean", "frac_std", "frac_maxabs",
    "jitter_unstable",
    "max_abs_epsilon",
    "figure_generated",
]

# ── Colours ─────────────────────────────────────────────────────────────────
C_POINTS   = "#2196F3"   # blue   — logged points
C_FIT      = "#FF9800"   # amber  — OLS line
C_RES      = "#4CAF50"   # green  — residuals
C_HIST     = "#2196F3"   # blue   — histogram bars
C_MEAN     = "#9C27B0"   # purple — mean reference
C_SIGMA    = "#E91E63"   # pink   — ±σ reference
C_ZERO     = "#9E9E9E"   # grey   — zero reference
C_NOMINAL  = "#9C27B0"   # purple — nominal sp reference


# ════════════════════════════════════════════════════════════════════════════
# Core algorithm — Steps 1–9 of the plan
# ════════════════════════════════════════════════════════════════════════════
def compute_variability(t_sec, sp_nominal, utc_trim=None):
    """
    t_sec      : 1-D numpy array, seconds since first sample (relative clock)
    sp_nominal : nominal sample period from param files (for reference only)
    utc_trim   : optional list of UTCDateTime for data_start/data_end strings

    Returns a dict with all intermediate arrays and summary stats.
    """
    n = int(len(t_sec))
    if n < 2:
        raise ValueError(f"Need ≥2 samples, got {n}")

    # Step 1 — adjacent differences  Δt'_j
    dt_prime = np.diff(t_sec)

    # Step 2 — first-guess interval  Δt_FG = median(Δt'_j)   (plain median)
    dt_FG = float(np.median(dt_prime))
    if dt_FG <= 0:
        raise ValueError(f"Non-positive first-guess interval: {dt_FG}")

    # Step 3 — sample-step counts
    delta_i_float = dt_prime / dt_FG
    delta_i_int   = np.round(delta_i_float).astype(np.int64)

    # Step 4 — raw gap diagnosis: positions j where Δi_j > 1
    gap_positions_raw = np.where(delta_i_int > 1)[0]
    n_gaps_raw = int(gap_positions_raw.size)
    gap_total_missing_raw = (int(np.sum(delta_i_int[gap_positions_raw] - 1))
                             if n_gaps_raw else 0)

    # Step 5 — reconstructed ideal sample index  i_j
    i_j = np.empty(n, dtype=np.int64)
    i_j[0] = 0
    np.cumsum(delta_i_int, out=i_j[1:])

    # Length of the reconstructed ideal sample index (slots 0 … i_last)
    n_ideal = int(i_j[-1]) + 1

    # Step 5b — true gap classification (data-derived, no sp_nominal dependency).
    # If the integer-step reconstruction allocates exactly as many slots as
    # we have samples, every Δi>1 event was jitter that rounded up while
    # neighbouring intervals compensated. A non-zero (n_ideal − n_points)
    # is the count of samples that are genuinely missing.
    true_missing = max(0, n_ideal - n)
    if true_missing == 0:
        n_gaps = 0
        gap_total_missing = 0
        gap_positions = np.array([], dtype=np.int64)
    else:
        n_gaps = n_gaps_raw
        gap_total_missing = true_missing
        gap_positions = gap_positions_raw

    # Step 6 — OLS fit  t'_j = t_{i=0} + i_j · Δt_true + e_j
    slope, intercept = np.polyfit(i_j.astype(float), t_sec, 1)
    dt_true = float(slope)
    t_i0    = float(intercept)   # offset, in the t_sec-relative frame

    # Step 7 — residuals
    t_fit = t_i0 + i_j.astype(float) * dt_true
    e     = t_sec - t_fit

    # Step 8 — jitter in ms
    e_ms     = 1000.0 * e
    mu_ms    = float(np.mean(e_ms))
    sigma_ms = float(np.std(e_ms, ddof=0))
    emax_ms  = float(np.max(np.abs(e_ms)))

    # Step 9 — jitter as fraction of fitted interval
    f       = e / dt_true
    mu_f    = float(np.mean(f))
    sigma_f = float(np.std(f, ddof=0))
    fmax    = float(np.max(np.abs(f)))

    # Instability flag — fires when the worst single-sample jitter exceeds
    # 40 % of Δt_true. On unstable days the OLS fit (and therefore Δt_true
    # and true_missing) is itself unreliable.
    jitter_unstable = bool(fmax > 0.4)

    # Optional consistency check — ε_j = Δi'_j − round(Δi'_j)
    epsilon         = delta_i_float - np.round(delta_i_float)
    max_abs_epsilon = float(np.max(np.abs(epsilon)))

    if utc_trim and len(utc_trim) > 0:
        data_start = str(utc_trim[0])[:19] + "Z"
        data_end   = str(utc_trim[-1])[:19] + "Z"
    else:
        data_start = ""
        data_end   = ""

    return {
        "n":              n,
        "n_ideal":        n_ideal,
        "sp_nominal":     float(sp_nominal),
        "dt_prime":       dt_prime,
        "dt_FG":          dt_FG,
        "delta_i_float":  delta_i_float,
        "delta_i_int":    delta_i_int,
        "i_j":            i_j,
        "gap_positions":  gap_positions,
        "n_gaps":         n_gaps,
        "gap_total_missing": gap_total_missing,
        "n_gaps_raw":     n_gaps_raw,
        "gap_total_missing_raw": gap_total_missing_raw,
        "true_missing":   true_missing,
        "jitter_unstable": jitter_unstable,
        "dt_true":        dt_true,
        "t_i0":           t_i0,
        "t_fit":          t_fit,
        "e":              e,
        "e_ms":           e_ms,
        "f":              f,
        "epsilon":        epsilon,
        "mu_ms":          mu_ms,
        "sigma_ms":       sigma_ms,
        "emax_ms":        emax_ms,
        "mu_f":           mu_f,
        "sigma_f":        sigma_f,
        "fmax":           fmax,
        "max_abs_epsilon": max_abs_epsilon,
        "data_start":     data_start,
        "data_end":       data_end,
    }


# ════════════════════════════════════════════════════════════════════════════
# Per-day 4-panel figure — Step 10 of the plan
# ════════════════════════════════════════════════════════════════════════════
def make_per_day_figure(t_sec, s, station, date_str, out_dir):
    """
    Four panels:
      A — Interval-count histogram of Δi'_j
      B — Jitter histogram of e_j (ms)
      C — t'_j vs reconstructed index i_j with OLS fit overlay
      D — Residuals e_j (ms) vs reconstructed index i_j
    """
    os.makedirs(out_dir, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(16, 10), dpi=140)
    fig.suptitle(
        f"Timestamp Variability — {station}    {date_str}\n"
        f"n={s['n']}   n_ideal={s['n_ideal']}   true_missing={s['true_missing']}   "
        f"Δt_FG={s['dt_FG']:.6f}s   Δt_true={repr(s['dt_true'])}s   "
        f"gaps={s['n_gaps']}"
        + ("   [JITTER UNSTABLE]" if s['jitter_unstable'] else ""),
        fontsize=14, fontweight="bold",
    )

    di = s["delta_i_float"]
    bin_w = 0.1

    # ── A. Interval-count histogram (Δi' ∈ [0, 2]) ───────────────────────
    ax = axes[0, 0]
    bins_A = np.arange(0.0, 2.0 + bin_w, bin_w)
    di_A = di[(di >= 0.0) & (di <= 2.0)]
    ax.hist(di_A, bins=bins_A, color="#E53935", edgecolor="black", linewidth=0.8)
    ax.axvline(1.0, color=C_ZERO, linestyle="--", linewidth=1, label="Δi=1 (consecutive)")
    ax.set_xlabel("Δi'_j  =  Δt'_j / Δt_FG   (sample-step count)", fontsize=11)
    ax.set_ylabel("count", fontsize=11)
    ax.set_title(f"A. Interval count histogram   (0 ≤ Δi' ≤ 2, bin=0.1)",
                 fontsize=12, fontweight="bold")
    ax.set_yscale("log")
    ax.set_xlim(0.0, 2.0)
    ax.legend(loc="upper left", fontsize=10, framealpha=0.9)
    ax.grid(alpha=0.3)

    # ── B. Jitter histogram ──────────────────────────────────────────────
    ax = axes[0, 1]
    e_ms = s["e_ms"]
    # Freedman–Diaconis bin width
    q75, q25 = np.percentile(e_ms, [75, 25])
    iqr      = q75 - q25
    bin_w    = 2 * iqr * (len(e_ms) ** (-1 / 3)) if iqr > 0 else None
    if bin_w and bin_w > 0:
        nbins = max(20, int((e_ms.max() - e_ms.min()) / bin_w))
    else:
        nbins = 60
    ax.hist(e_ms, bins=nbins, color=C_HIST, edgecolor="black", linewidth=0.3)
    ax.axvline(s["mu_ms"], color=C_MEAN, linestyle="-", linewidth=1.5,
               label=f"μ = {s['mu_ms']:.3f} ms")
    for sign in (+1, -1):
        ax.axvline(s["mu_ms"] + sign * s["sigma_ms"], color=C_SIGMA,
                   linestyle="--", linewidth=1, label="±σ" if sign > 0 else None)
    ax.set_xlabel("e_j  (ms)", fontsize=11)
    ax.set_ylabel("count", fontsize=11)
    ax.set_title(
        f"B. Jitter histogram   σ={s['sigma_ms']:.3f} ms   "
        f"max|e|={s['emax_ms']:.3f} ms",
        fontsize=12, fontweight="bold")
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.grid(alpha=0.3)

    # ── C. Interval-count histogram (Δi' > 2, gap events) ────────────────
    ax = axes[1, 0]
    di_C = di[di > 2.0]
    if di_C.size > 0:
        max_C = float(np.max(di_C))
        max_bins = 200
        bw_C = max(bin_w, (max_C - 2.0) / max_bins)
        bins_C = np.arange(2.0, max_C + bw_C, bw_C)
        ax.hist(di_C, bins=bins_C, color="#E53935", edgecolor="black", linewidth=0.8)
    else:
        ax.text(0.5, 0.5, "no Δi' > 2  (no gap events)",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=12, color=C_ZERO)
    ax.set_xlabel("Δi'_j  =  Δt'_j / Δt_FG   (sample-step count)", fontsize=11)
    ax.set_ylabel("count", fontsize=11)
    ax.set_title(f"C. Interval count histogram   (Δi' > 2, bin=0.1)  "
                 f"n_gaps={s['n_gaps']}",
                 fontsize=12, fontweight="bold")
    ax.set_yscale("log")
    ax.grid(alpha=0.3)

    # ── D. Residuals vs i_j ──────────────────────────────────────────────
    ax = axes[1, 1]
    i_j = s["i_j"]
    ax.scatter(i_j, e_ms, s=8, color=C_RES, zorder=3)
    ax.axhline(0.0, color=C_ZERO, linestyle="--", linewidth=1)
    ax.axhline(+s["sigma_ms"], color=C_SIGMA, linestyle=":", linewidth=1,
               label=f"±σ = ±{s['sigma_ms']:.3f} ms")
    ax.axhline(-s["sigma_ms"], color=C_SIGMA, linestyle=":", linewidth=1)
    # Mark gap positions (if any)
    if s["n_gaps"] > 0:
        # gap_positions index into dt_prime; the "gap" is between record j and j+1,
        # so mark vertical line at i_j[j+1] (where the gap lands in ideal-index space)
        for p in s["gap_positions"]:
            ax.axvline(i_j[p + 1], color="red", linestyle="-", linewidth=0.5, alpha=0.4)
    ax.set_xlabel("reconstructed index  i_j", fontsize=11)
    ax.set_ylabel("e_j  (ms)", fontsize=11)
    ax.set_title("D. Jitter residuals vs ideal sample index",
                 fontsize=12, fontweight="bold")
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.grid(alpha=0.3)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out_path = os.path.join(out_dir, "variability_4panel.png")
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  Figure saved: {out_path}")
    return out_path


def write_stats(s, station, date_str, out_dir):
    """Human-readable summary text file."""
    lines = [
        f"Timestamp Variability Assessment",
        f"  station       : {station}",
        f"  date          : {date_str}",
        f"  n_points      : {s['n']}",
        f"  n_ideal       : {s['n_ideal']}   (length of reconstructed ideal sample index)",
        f"  data_start    : {s['data_start']}",
        f"  data_end      : {s['data_end']}",
        "",
        f"Sample interval",
        f"  sp_nominal    : {s['sp_nominal']:.6f} s",
        f"  Δt_FG (median): {s['dt_FG']:.6f} s",
        f"  Δt_true (OLS) : {s['dt_true']:.6f} s",
        f"  t_i0 (OLS)    : {s['t_i0']:.6f} s   (relative to first sample)",
        "",
        f"Gaps",
        f"  true_missing       : {s['true_missing']}  (= n_ideal − n_points; data-derived)",
        f"  n_gaps (raw)       : {s['n_gaps_raw']}  total missing (raw): {s['gap_total_missing_raw']}",
        f"  n_gaps (true)      : {s['n_gaps']}  total missing : {s['gap_total_missing']} samples",
        "",
        f"Jitter (ms)",
        f"  mean           : {s['mu_ms']:.6f}",
        f"  std            : {s['sigma_ms']:.6f}",
        f"  max|e|         : {s['emax_ms']:.6f}",
        "",
        f"Jitter (fraction of Δt_true)",
        f"  mean           : {s['mu_f']:.6e}",
        f"  std            : {s['sigma_f']:.6e}",
        f"  max|f|         : {s['fmax']:.6e}",
        f"  (≈ {100.0 * s['fmax']:.4f}%)",
        "",
        f"Consistency check",
        f"  jitter_unstable: {s['jitter_unstable']}   (frac_maxabs > 0.4)",
        f"  max|ε_j|       : {s['max_abs_epsilon']:.6f}   "
        f"(deviation of Δi'_j from nearest integer)",
    ]
    out_path = os.path.join(out_dir, "variability_stats.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Stats saved : {out_path}")
    return out_path


# ════════════════════════════════════════════════════════════════════════════
# CSV helpers
# ════════════════════════════════════════════════════════════════════════════
def _metrics_csv_path(station):
    short = station.split("-")[0]
    return os.path.join(OUT_ROOT, "metrics", f"{short}_variability.csv")


def _load_existing_keys(csv_path):
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            keys.add((row["station"], row["date"]))
    return keys


def _append_row(csv_path, row):
    exists = os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _no_data_row(station, date_str, deployment, sp_nominal):
    return {
        "date": date_str, "station": station, "deployment": deployment,
        "has_data": False,
        "data_start": "", "data_end": "",
        "n_points": 0,
        "n_ideal": 0,
        "true_missing": "",
        "sp_nominal": round(sp_nominal, 6) if sp_nominal else "",
        "dt_FG": "", "dt_true": "", "t_i0_offset_s": "",
        "n_gaps": "", "gap_total_missing": "",
        "n_gaps_raw": "", "gap_total_missing_raw": "",
        "jitter_mean_ms": "", "jitter_std_ms": "", "jitter_maxabs_ms": "",
        "frac_mean": "", "frac_std": "", "frac_maxabs": "",
        "jitter_unstable": "",
        "max_abs_epsilon": "",
        "figure_generated": False,
    }


def _row_from_stats(s, station, date_str, deployment, fig_generated):
    return {
        "date":              date_str,
        "station":           station,
        "deployment":        deployment,
        "has_data":          True,
        "data_start":        s["data_start"],
        "data_end":          s["data_end"],
        "n_points":          s["n"],
        "n_ideal":           s["n_ideal"],
        "true_missing":      s["true_missing"],
        "sp_nominal":        round(s["sp_nominal"], 6),
        "dt_FG":             round(s["dt_FG"],   9),
        "dt_true":           round(s["dt_true"], 9),
        "t_i0_offset_s":     round(s["t_i0"],    9),
        "n_gaps":            s["n_gaps"],
        "gap_total_missing": s["gap_total_missing"],
        "n_gaps_raw":        s["n_gaps_raw"],
        "gap_total_missing_raw": s["gap_total_missing_raw"],
        "jitter_mean_ms":    round(s["mu_ms"],    6),
        "jitter_std_ms":     round(s["sigma_ms"], 6),
        "jitter_maxabs_ms":  round(s["emax_ms"],  6),
        "frac_mean":         float(f"{s['mu_f']:.6e}"),
        "frac_std":          float(f"{s['sigma_f']:.6e}"),
        "frac_maxabs":       float(f"{s['fmax']:.6e}"),
        "jitter_unstable":   s["jitter_unstable"],
        "max_abs_epsilon":   round(s["max_abs_epsilon"], 6),
        "figure_generated":  fig_generated,
    }


# ════════════════════════════════════════════════════════════════════════════
# Shared per-day worker
# ════════════════════════════════════════════════════════════════════════════
def _process_day(station, date, run, fig_dir_base, csv_path, always_figure,
                 save_nc_dir=None, only_if_gaps=False):
    """
    Fetch + compute + write for one (station, date). Returns True on success.
    """
    date_str = str(date)[:10]

    try:
        dep_info = get_deployment_for_date(station, date, PARAM_PATH)
    except ValueError as e:
        print(f"    skip — {e}")
        _append_row(csv_path, _no_data_row(station, date_str, 0, 0))
        return False

    deployment = dep_info["deployment"]
    sp_nominal = dep_info["sp_nominal"]

    try:
        _, t_sec, utc_trim, _ = fetch_nc_timestamps(
            station, date, date + 86400.0, deployment, run,
            save_nc_dir=save_nc_dir)
    except Exception as e:
        print(f"    no data — {e}")
        _append_row(csv_path, _no_data_row(station, date_str, deployment, sp_nominal))
        return False

    if len(t_sec) < 2:
        print(f"    too few points ({len(t_sec)})")
        _append_row(csv_path, _no_data_row(station, date_str, deployment, sp_nominal))
        return False

    s = compute_variability(t_sec, sp_nominal, utc_trim=utc_trim)

    fig_generated = False
    want_figure = always_figure and (not only_if_gaps or s["n_gaps"] > 0)
    if want_figure:
        fig_dir = os.path.join(fig_dir_base, f"{station}_{date_str}")
        make_per_day_figure(t_sec, s, station, date_str, fig_dir)
        write_stats(s, station, date_str, fig_dir)
        fig_generated = True
    elif always_figure and only_if_gaps:
        print(f"    no gaps (n_gaps=0) — skipping figure")

    _append_row(csv_path, _row_from_stats(s, station, date_str, deployment, fig_generated))
    print(f"    n={s['n']}  Δt_FG={s['dt_FG']:.6f}s  Δt_true={s['dt_true']:.6f}s  "
          f"gaps={s['n_gaps']}  σ={s['sigma_ms']:.3f}ms  max|e|={s['emax_ms']:.3f}ms")
    return True


# ════════════════════════════════════════════════════════════════════════════
# Modes
# ════════════════════════════════════════════════════════════════════════════
def single_mode(args):
    run = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))
    date = UTCDateTime(args.date + "T00:00:00Z")
    station = args.station[0] if isinstance(args.station, list) else args.station
    print(f"\n[single] {station}   {args.date}")

    csv_path = _metrics_csv_path(station)
    existing = _load_existing_keys(csv_path)
    if (station, args.date) in existing and not args.force:
        print("  already in CSV — re-running anyway to regenerate figure")
    # Overwrite the existing row by removing it first
    if (station, args.date) in existing:
        _remove_row(csv_path, station, args.date)

    _process_day(
        station, date, run,
        fig_dir_base=os.path.join(OUT_ROOT, "figures", "per_day"),
        csv_path=csv_path,
        always_figure=True,
        save_nc_dir=(os.path.join(OUT_ROOT, "netcdf") if args.save_nc else None),
        only_if_gaps=args.only_gaps,
    )


def _remove_row(csv_path, station, date_str):
    rows = []
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if not (r["station"] == station and r["date"] == date_str)]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def collect_mode(args):
    run      = read_param(os.path.join(PARAM_PATH, "run_prest.txt"))
    stations = args.station if args.station else STATIONS
    date     = UTCDateTime(args.start + "T00:00:00Z")
    end_date = UTCDateTime(args.end   + "T00:00:00Z")
    total    = int((end_date - date) / 86400.0) + 1

    day_num = 0
    while date <= end_date:
        day_num += 1
        date_str = str(date)[:10]
        print(f"\n{'='*60}")
        print(f"  Day {day_num}/{total}  —  {date_str}")

        for station in stations:
            print(f"\n  [{station}]")
            csv_path = _metrics_csv_path(station)
            existing = _load_existing_keys(csv_path)
            if (station, date_str) in existing:
                print(f"    skip — already in CSV")
                continue
            _process_day(
                station, date, run,
                fig_dir_base=os.path.join(OUT_ROOT, "figures", "per_day"),
                csv_path=csv_path,
                always_figure=True,
                save_nc_dir=(os.path.join(OUT_ROOT, "netcdf")
                             if args.save_nc else None),
                only_if_gaps=args.only_gaps,
            )

        date += 86400.0


# ── plot mode ──────────────────────────────────────────────────────────────
def _load_metrics(station):
    csv_path = _metrics_csv_path(station)
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _to_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return np.nan


def plot_mode(args):
    """Summary plots across collected variability metrics."""
    stations = args.station if args.station else STATIONS
    summary_dir = os.path.join(OUT_ROOT, "figures", "summary")
    os.makedirs(summary_dir, exist_ok=True)

    # Load per-station data
    data = {}
    for st in stations:
        rows = _load_metrics(st)
        rows = [r for r in rows if r["has_data"] == "True"]
        if not rows:
            continue
        dates  = [datetime.datetime.fromisoformat(r["date"]) for r in rows]
        data[st] = {
            "rows":    rows,
            "dates":   dates,
            "dt_true": np.array([_to_float(r["dt_true"])          for r in rows]),
            "dt_FG":   np.array([_to_float(r["dt_FG"])            for r in rows]),
            "sigma_ms":np.array([_to_float(r["jitter_std_ms"])    for r in rows]),
            "max_ms":  np.array([_to_float(r["jitter_maxabs_ms"]) for r in rows]),
            "n_gaps":  np.array([_to_float(r["n_gaps"])           for r in rows]),
            "sp_nom":  np.array([_to_float(r["sp_nominal"])       for r in rows]),
        }

    if not data:
        print("No metrics to plot.")
        return

    n = len(data)

    # Fig 1 — Δt_true per day
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.5 * n), dpi=140, squeeze=False)
    for ax, (st, d) in zip(axes[:, 0], data.items()):
        ax.scatter(d["dates"], d["dt_true"], s=16, color=C_POINTS, label="Δt_true (OLS slope)")
        ax.scatter(d["dates"], d["dt_FG"],   s=12, color=C_FIT,    marker="x",
                   label="Δt_FG (median)")
        # Nominal reference
        for sp in np.unique(d["sp_nom"][~np.isnan(d["sp_nom"])]):
            ax.axhline(sp, color=C_NOMINAL, linestyle=":", linewidth=1,
                       label=f"sp_nominal = {sp:.6f}s")
        ax.set_title(f"{st}", fontweight="bold")
        ax.set_ylabel("interval (s)")
        ax.legend(loc="best", fontsize=9, framealpha=0.9)
        ax.grid(alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.suptitle("Fitted true sample interval per day", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(os.path.join(summary_dir, "fig1_dt_true.png"), bbox_inches="tight")
    plt.close(fig)

    # Fig 2 — jitter σ and max per day
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.5 * n), dpi=140, squeeze=False)
    for ax, (st, d) in zip(axes[:, 0], data.items()):
        ax.plot(d["dates"], d["sigma_ms"], "o-", color=C_POINTS, markersize=4,
                label="σ jitter (ms)")
        ax.plot(d["dates"], d["max_ms"],   "s-", color=C_SIGMA,  markersize=4,
                label="max|e| (ms)")
        ax.set_title(f"{st}", fontweight="bold")
        ax.set_ylabel("jitter (ms)")
        ax.legend(loc="best", fontsize=9, framealpha=0.9)
        ax.grid(alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.suptitle("Timestamp jitter per day", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(os.path.join(summary_dir, "fig2_jitter.png"), bbox_inches="tight")
    plt.close(fig)

    # Fig 3 — gap count per day
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.5 * n), dpi=140, squeeze=False)
    for ax, (st, d) in zip(axes[:, 0], data.items()):
        ax.bar(d["dates"], d["n_gaps"], color=C_SIGMA, width=0.9)
        ax.set_title(f"{st}", fontweight="bold")
        ax.set_ylabel("n gaps (Δi>1)")
        ax.grid(alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.suptitle("Gap count per day", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(os.path.join(summary_dir, "fig3_gap_count.png"), bbox_inches="tight")
    plt.close(fig)

    print(f"Summary figures saved to {summary_dir}")


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Timestamp variability assessment for OOI PREST stations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", choices=["single", "collect", "plot"], default="single")
    parser.add_argument("--station", nargs="*", default=None,
                        help="Station reference designator(s); defaults to all 3.")
    parser.add_argument("--date",  help="YYYY-MM-DD (single mode).")
    parser.add_argument("--start", help="YYYY-MM-DD (collect mode).")
    parser.add_argument("--end",   help="YYYY-MM-DD (collect mode).")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing CSV row (single mode).")
    parser.add_argument("--save-nc", action="store_true",
                        help="Also download the raw NetCDF file for each "
                             "fetched day into output/temporal_anomaly/netcdf/.")
    parser.add_argument("--only-gaps", action="store_true",
                        help="Only generate the per-day 4-panel figure when "
                             "n_gaps > 0 (post wall-clock correction). "
                             "CSV row is still written every day.")
    args = parser.parse_args()

    if args.mode == "single":
        if not args.date or not args.station:
            parser.error("single mode requires --station and --date")
        if isinstance(args.station, list):
            if len(args.station) != 1:
                parser.error("single mode needs exactly one --station")
            args.station = args.station[0]
        single_mode(args)
    elif args.mode == "collect":
        if not (args.start and args.end):
            parser.error("collect mode requires --start and --end")
        collect_mode(args)
    else:
        plot_mode(args)


if __name__ == "__main__":
    main()
