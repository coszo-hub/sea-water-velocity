#!/usr/bin/env python3
"""Find dates with outlier `dt_true` values in the temporal-anomaly metrics.

Reads every `*_variability.csv` under output/temporal_anomaly/metrics/,
groups rows by (station, deployment), and flags days whose `dt_true`
deviates strongly from the group's robust centre.

Two views are produced for each day with data:
  * robust z-score against the per-(station, deployment) median, scaled by
    MAD (median absolute deviation). Days with |z| >= --z-threshold are
    flagged as "robust" outliers — this catches drift within a deployment.
  * absolute fractional deviation from `dt_FG` (the nominal sample period).
    Days with |dt_true - dt_FG| / dt_FG >= --fg-threshold are flagged as
    "nominal" outliers — this catches everything that wandered off the
    metadata-declared rate.

Outputs:
  * a CSV of all flagged rows (default: dt_true_outliers.csv next to the
    input CSVs)
  * a short summary table to stdout

Usage:
  bin/find_dt_true_outliers.py
  bin/find_dt_true_outliers.py --z-threshold 6 --fg-threshold 1e-4
  bin/find_dt_true_outliers.py --metrics-dir /path/to/metrics --out report.csv
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import sys
from collections import defaultdict

# Constant that makes MAD a consistent estimator of stdev for a normal dist.
MAD_TO_SIGMA = 1.4826


def parse_float(s):
    if s is None or s == "":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def median(xs):
    n = len(xs)
    if n == 0:
        return None
    s = sorted(xs)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def robust_z(value, med, mad):
    """MAD-based z-score. Returns None if MAD is zero (degenerate group)."""
    if mad is None or mad == 0:
        return None
    return (value - med) / (MAD_TO_SIGMA * mad)


def load_rows(metrics_dir):
    pattern = os.path.join(metrics_dir, "*_variability.csv")
    paths = sorted(glob.glob(pattern))
    if not paths:
        sys.exit(f"error: no *_variability.csv files found in {metrics_dir}")

    rows = []
    for path in paths:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("has_data", "").strip() != "True":
                    continue
                dt_true = parse_float(r.get("dt_true"))
                if dt_true is None:
                    continue
                rows.append({
                    "source_csv": os.path.basename(path),
                    "date": r["date"],
                    "station": r["station"],
                    "deployment": r["deployment"],
                    "dt_FG": parse_float(r.get("dt_FG")),
                    "dt_true": dt_true,
                    "jitter_maxabs_ms": parse_float(r.get("jitter_maxabs_ms")),
                    "frac_maxabs": parse_float(r.get("frac_maxabs")),
                    "jitter_unstable": r.get("jitter_unstable", ""),
                })
    return rows, paths


def group_stats(rows):
    """Median and MAD of dt_true per (station, deployment)."""
    by_group = defaultdict(list)
    for r in rows:
        by_group[(r["station"], r["deployment"])].append(r["dt_true"])

    stats = {}
    for key, values in by_group.items():
        med = median(values)
        mad = median([abs(v - med) for v in values])
        stats[key] = {"median": med, "mad": mad, "n": len(values)}
    return stats


def find_outliers(rows, stats, z_threshold, fg_threshold):
    flagged = []
    for r in rows:
        key = (r["station"], r["deployment"])
        s = stats[key]
        med = s["median"]
        mad = s["mad"]
        z = robust_z(r["dt_true"], med, mad)

        fg = r["dt_FG"]
        frac_fg = (
            abs(r["dt_true"] - fg) / fg
            if fg not in (None, 0)
            else None
        )

        reasons = []
        if z is not None and abs(z) >= z_threshold:
            reasons.append("robust")
        if frac_fg is not None and frac_fg >= fg_threshold:
            reasons.append("nominal")

        if reasons:
            flagged.append({
                **r,
                "group_median_dt_true": med,
                "group_mad_dt_true": mad,
                "group_n_days": s["n"],
                "robust_z": z,
                "frac_dev_from_dt_FG": frac_fg,
                "flag_reasons": "+".join(reasons),
            })
    return flagged


def write_report(flagged, out_path):
    fieldnames = [
        "date", "station", "deployment", "source_csv",
        "dt_FG", "dt_true",
        "group_median_dt_true", "group_mad_dt_true", "group_n_days",
        "robust_z", "frac_dev_from_dt_FG",
        "jitter_maxabs_ms", "frac_maxabs", "jitter_unstable",
        "flag_reasons",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in flagged:
            row = dict(r)
            # Pretty-format floats so the CSV is readable.
            for k in ("dt_FG", "dt_true",
                      "group_median_dt_true", "group_mad_dt_true"):
                if row.get(k) is not None:
                    row[k] = f"{row[k]:.9f}"
            if row.get("robust_z") is not None:
                row["robust_z"] = f"{row['robust_z']:.3f}"
            if row.get("frac_dev_from_dt_FG") is not None:
                row["frac_dev_from_dt_FG"] = f"{row['frac_dev_from_dt_FG']:.3e}"
            if row.get("jitter_maxabs_ms") is not None:
                row["jitter_maxabs_ms"] = f"{row['jitter_maxabs_ms']:.3f}"
            if row.get("frac_maxabs") is not None:
                row["frac_maxabs"] = f"{row['frac_maxabs']:.3e}"
            writer.writerow(row)


def print_summary(flagged, stats, z_threshold, fg_threshold, out_path):
    total = len(flagged)
    print()
    print(f"Outliers found: {total}")
    print(f"Thresholds: |robust z| >= {z_threshold}, "
          f"|dt_true - dt_FG|/dt_FG >= {fg_threshold:g}")
    print(f"Report written to: {out_path}")

    if not flagged:
        return

    # Per-(station, deployment) tally.
    tally = defaultdict(int)
    for r in flagged:
        tally[(r["station"], r["deployment"])] += 1

    print()
    print("Per-group counts (outlier_days / total_days_with_data):")
    for key in sorted(tally.keys()):
        station, dep = key
        n_total = stats[key]["n"]
        med = stats[key]["median"]
        mad = stats[key]["mad"]
        print(f"  {station}  dep={dep}: {tally[key]:5d} / {n_total:5d}   "
              f"(median dt_true={med:.9f}s, MAD={mad:.3e}s)")

    print()
    print("Top 15 most extreme outliers (by |robust_z|, then by frac_dev_from_dt_FG):")
    def sort_key(r):
        z = abs(r["robust_z"]) if r["robust_z"] is not None else -1
        f = r["frac_dev_from_dt_FG"] if r["frac_dev_from_dt_FG"] is not None else -1
        return (-z, -f)
    for r in sorted(flagged, key=sort_key)[:15]:
        z = r["robust_z"]
        f = r["frac_dev_from_dt_FG"]
        z_str = f"{z:+7.2f}" if z is not None else "    n/a"
        f_str = f"{f:.2e}" if f is not None else "    n/a"
        print(f"  {r['date']}  {r['station']}  dep={r['deployment']}  "
              f"dt_true={r['dt_true']:.9f}s  z={z_str}  "
              f"frac_dev={f_str}  [{r['flag_reasons']}]")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_metrics_dir = os.path.normpath(
        os.path.join(here, "..", "output", "temporal_anomaly", "metrics")
    )

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--metrics-dir", default=default_metrics_dir,
                   help=f"directory containing *_variability.csv "
                        f"(default: {default_metrics_dir})")
    p.add_argument("--out", default=None,
                   help="output CSV path (default: <metrics-dir>/dt_true_outliers.csv)")
    p.add_argument("--z-threshold", type=float, default=10000.0,
                   help="|robust z| threshold for the per-group flag (default: 10000)")
    p.add_argument("--fg-threshold", type=float, default=5e-2,
                   help="|dt_true - dt_FG| / dt_FG threshold for the nominal flag "
                        "(default: 5e-2 = 5%%)")
    args = p.parse_args()

    out_path = args.out or os.path.join(args.metrics_dir, "dt_true_outliers.csv")

    rows, paths = load_rows(args.metrics_dir)
    print(f"Read {len(rows)} rows-with-data from {len(paths)} CSV(s):")
    for path in paths:
        print(f"  {os.path.basename(path)}")

    stats = group_stats(rows)
    flagged = find_outliers(rows, stats, args.z_threshold, args.fg_threshold)
    write_report(flagged, out_path)
    print_summary(flagged, stats, args.z_threshold, args.fg_threshold, out_path)


if __name__ == "__main__":
    main()
