#!/usr/bin/env python3
"""Render fig1_dt_true.png with outlier days marked as red vertical lines.

Outliers are detected per (station, deployment) using the same logic as
bin/find_dt_true_outliers.py: a day is flagged if its `dt_true` has either
  * |robust z| >= --z-threshold against the per-group median (MAD-scaled), or
  * |dt_true - dt_FG| / dt_FG >= --fg-threshold

Saves fig1_dt_true_outliers.png to output/temporal_anomaly/figures/summary/
(plus per-year subdirs that already contain a fig1_dt_true.png).
"""

from __future__ import annotations

import argparse
import csv
import datetime
import math
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

REPO_ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
METRICS_DIR = os.path.join(REPO_ROOT, "output", "temporal_anomaly", "metrics")
SUMMARY_DIR = os.path.join(REPO_ROOT, "output", "temporal_anomaly",
                           "figures", "summary")
STATIONS = (
    "RS01SLBS-MJ01A-06-PRESTA101",
    "RS01SUM1-LJ01B-09-PRESTB102",
    "RS03AXBS-MJ03A-06-PRESTA301",
)

C_POINTS   = "#2196F3"
C_FIT      = "#FF9800"
C_NOMINAL  = "#9C27B0"
C_OUTLIER  = "#D32F2F"  # red

MAD_TO_SIGMA = 1.4826


def _to_float(s):
    try:
        v = float(s)
    except (ValueError, TypeError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def _station_key(station_full):
    """RS01SLBS-MJ01A-06-PRESTA101 -> RS01SLBS (CSV file prefix)."""
    return station_full.split("-", 1)[0]


def load_station_rows(station_full):
    path = os.path.join(METRICS_DIR, f"{_station_key(station_full)}_variability.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("has_data") == "True"]
    return rows


def _median(xs):
    n = len(xs)
    if n == 0:
        return None
    s = sorted(xs)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def find_outlier_dates(rows, z_threshold, fg_threshold):
    """Return the set of date strings flagged across any deployment in rows."""
    by_group = defaultdict(list)
    for r in rows:
        dt = _to_float(r.get("dt_true"))
        if math.isnan(dt):
            continue
        by_group[r["deployment"]].append((r, dt))

    flagged = set()
    for dep, items in by_group.items():
        values = [v for _, v in items]
        med = _median(values)
        mad = _median([abs(v - med) for v in values])
        for r, dt in items:
            z = (dt - med) / (MAD_TO_SIGMA * mad) if mad else None
            fg = _to_float(r.get("dt_FG"))
            frac_fg = (abs(dt - fg) / fg) if (not math.isnan(fg) and fg != 0) else None

            if (z is not None and abs(z) >= z_threshold) or \
               (frac_fg is not None and frac_fg >= fg_threshold):
                flagged.add(r["date"])
    return flagged


def _filter_rows(rows, year=None):
    if year is None:
        return rows
    return [r for r in rows if r["date"].startswith(f"{year}-")]


def render(out_dir, z_threshold, fg_threshold, year=None, suffix=""):
    per_station = {}
    for st in STATIONS:
        rows = _filter_rows(load_station_rows(st), year=year)
        if rows:
            per_station[st] = rows

    if not per_station:
        print(f"[{out_dir}] no metrics — skipping")
        return

    n = len(per_station)
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.5 * n), dpi=140, squeeze=False)

    total_flagged = 0
    for ax, (st, rows) in zip(axes[:, 0], per_station.items()):
        dates    = [datetime.datetime.fromisoformat(r["date"]) for r in rows]
        dt_true  = np.array([_to_float(r["dt_true"]) for r in rows])
        dt_FG    = np.array([_to_float(r["dt_FG"])   for r in rows])
        sp_nom   = np.array([_to_float(r["sp_nominal"]) for r in rows])

        flagged = find_outlier_dates(rows, z_threshold, fg_threshold)
        total_flagged += len(flagged)

        # Draw outlier vertical lines first so points/markers sit on top.
        for d in sorted(flagged):
            ax.axvline(datetime.datetime.fromisoformat(d),
                       color=C_OUTLIER, alpha=0.35, linewidth=1.0, zorder=1)

        # Add a single proxy artist for the legend entry.
        if flagged:
            ax.axvline(np.nan, color=C_OUTLIER, alpha=0.6, linewidth=1.0,
                       label=f"outlier day ({len(flagged)} flagged)")

        ax.scatter(dates, dt_true, s=16, color=C_POINTS,
                   label="Δt_true (OLS slope)", zorder=3)
        ax.scatter(dates, dt_FG,   s=12, color=C_FIT, marker="x",
                   label="Δt_FG (median)", zorder=3)
        for sp in np.unique(sp_nom[~np.isnan(sp_nom)]):
            ax.axhline(sp, color=C_NOMINAL, linestyle=":", linewidth=1,
                       label=f"sp_nominal = {sp:.6f}s")

        ax.set_title(st, fontweight="bold")
        ax.set_ylabel("interval (s)")
        ax.legend(loc="best", fontsize=9, framealpha=0.9)
        ax.grid(alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))

    title = "Fitted true sample interval per day — outliers marked"
    if suffix:
        title += f" — {suffix}"
    title += f"  (|z|>={z_threshold:g}, |Δ/dt_FG|>={fg_threshold:g})"
    fig.suptitle(title, fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "fig1_dt_true_outliers.png")
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out_path}  ({total_flagged} outlier marks)")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--z-threshold", type=float, default=10000.0,
                   help="|robust z| threshold (default: 10000)")
    p.add_argument("--fg-threshold", type=float, default=5e-2,
                   help="|dt_true - dt_FG|/dt_FG threshold (default: 5e-2 = 5%%)")
    p.add_argument("--years", nargs="*", default=["2015", "2022", "2023"],
                   help="per-year subdirs to also render "
                        "(default: 2015 2022 2023; pass empty to skip)")
    args = p.parse_args()

    print(f"Thresholds: |z|>={args.z_threshold:g}, "
          f"|Δ/dt_FG|>={args.fg_threshold:g}")
    print("Full range:")
    render(SUMMARY_DIR, args.z_threshold, args.fg_threshold)
    for year in args.years:
        print(f"Year {year}:")
        render(os.path.join(SUMMARY_DIR, year),
               args.z_threshold, args.fg_threshold,
               year=year, suffix=year)


if __name__ == "__main__":
    main()
