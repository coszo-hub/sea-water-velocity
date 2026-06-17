#!/usr/bin/env python3
"""Render fig1_dt_true.png with outlier days marked as red vertical lines.

Outliers are detected per (stream, deployment) using the same logic as
bin/find_dt_true_outliers.py: a day is flagged if its `dt_true` has either
  * |robust z| >= --z-threshold against the per-group median (MAD-scaled), or
  * |dt_true - dt_FG| / dt_FG >= --fg-threshold

One panel is drawn per per-stream metrics CSV. VEL3D-C stations have two
streams under one reference designator (8 Hz velocity + 1 Hz system), so
they get one panel each rather than being pooled into a single station.

Saves fig1_dt_true_outliers.png to output/temporal_anomaly/figures/summary/
(plus per-year subdirs that already contain a fig1_dt_true.png).
"""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
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


def discover_streams():
    """One panel per per-stream variability CSV.

    Returns a list of (label, path), one per
    output/temporal_anomaly/metrics/<STATION>_<stream>_variability.csv.
    The label is "<STATION>_<stream>". VEL3D-C stations yield two CSVs
    (8 Hz velocity + 1 Hz system) and therefore two panels — never pooled.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(METRICS_DIR, "*_variability.csv"))):
        label = os.path.basename(path)[: -len("_variability.csv")]
        out.append((label, path))
    return out


def load_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("has_data") == "True"]


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
    per_panel = {}
    for label, path in discover_streams():
        rows = _filter_rows(load_rows(path), year=year)
        if rows:
            per_panel[label] = rows

    if not per_panel:
        print(f"[{out_dir}] no metrics — skipping")
        return

    n = len(per_panel)
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.5 * n), dpi=140, squeeze=False)

    total_flagged = 0
    for ax, (label, rows) in zip(axes[:, 0], per_panel.items()):
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

        ax.set_title(label, fontweight="bold")
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
    p.add_argument("--years", nargs="*", default=[],
                   help="per-year subdirs to also render in addition to the "
                        "full range, e.g. --years 2025 (default: none)")
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
