#!/usr/bin/env python3
"""Segregate outlier-date MiniSEED files and flatten the year folders.

Three steps, in order:
  1. Read the most recent outlier list from
     output/temporal_anomaly/metrics/dt_true_outliers.csv
     (produced by bin/find_dt_true_outliers.py).
  2. For every file under output/mseed2dmc/<YYYY>/ (or already flat at the
     top), if (date, SEED station) matches an outlier pair, move it to
     output/outlier/<SEED_STA>/<YYYY>/.
  3. Flatten remaining files in each YYYY/ subdir up into output/mseed2dmc/
     and remove the year dir if it ends up empty.

Per-station match: an outlier on RS01SUM1 only moves HYS14 files for that
date; HYSB1 / AXBA1 files on the same date stay (they weren't flagged).

Filename format expected:
  OO.<STA>.<LOC>.<CHAN>.YYYY.DDD.HH.MM.SS.SSS-YYYY.DDD.HH.MM.SS.SSS.mseed
The date is derived from the leading YYYY.DDD (Julian day → calendar date).

Usage:
  bin/segregate_outlier_mseed.py --dry-run    # preview, touch nothing
  bin/segregate_outlier_mseed.py              # actually move files
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import re
import shutil
import sys
from collections import defaultdict

REPO_ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MSEED_DIR   = os.path.join(REPO_ROOT, "output", "mseed2dmc")
OUTLIER_DIR = os.path.join(REPO_ROOT, "output", "outlier")
OUTLIER_CSV = os.path.join(REPO_ROOT, "output", "temporal_anomaly",
                           "metrics", "dt_true_outliers.csv")

# OOI reference designator → SEED station code (from param/ files).
STATION_TO_SEED = {
    "RS01SLBS-MJ01A-06-PRESTA101": "HYSB1",
    "RS01SUM1-LJ01B-09-PRESTB102": "HYS14",
    "RS03AXBS-MJ03A-06-PRESTA301": "AXBA1",
}

FNAME_RE = re.compile(
    r"^(?P<net>[^.]+)\.(?P<sta>[^.]+)\.(?P<loc>[^.]+)\.(?P<chan>[^.]+)\."
    r"(?P<yyyy>\d{4})\.(?P<ddd>\d{3})\."
)


def parse_mseed_filename(fname):
    m = FNAME_RE.match(fname)
    if not m:
        return None
    try:
        date_iso = datetime.datetime.strptime(
            f"{m['yyyy']}.{m['ddd']}", "%Y.%j"
        ).date().isoformat()
    except ValueError:
        return None
    return {"sta": m["sta"], "date": date_iso}


def load_outlier_pairs(csv_path):
    """Return (set of (date, seed_sta) pairs, set of unmapped station strings)."""
    pairs = set()
    unmapped = set()
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            station_full = r["station"]
            seed = STATION_TO_SEED.get(station_full)
            if seed is None:
                unmapped.add(station_full)
                continue
            pairs.add((r["date"], seed))
    return pairs, unmapped


def iter_mseed_files(mseed_root):
    """Yield (full_path, year_dir_name_or_None) for each .mseed under mseed_root.
    year_dir_name is the YYYY subdir if the file is inside one, else None
    (the file is already at the top level of mseed_root)."""
    for entry in sorted(os.listdir(mseed_root)):
        path = os.path.join(mseed_root, entry)
        if os.path.isdir(path) and re.fullmatch(r"\d{4}", entry):
            for fname in sorted(os.listdir(path)):
                fpath = os.path.join(path, fname)
                if os.path.isfile(fpath) and fname.endswith(".mseed"):
                    yield fpath, entry
        elif os.path.isfile(path) and entry.endswith(".mseed"):
            yield path, None


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mseed-dir", default=MSEED_DIR)
    p.add_argument("--outlier-dir", default=OUTLIER_DIR)
    p.add_argument("--outlier-csv", default=OUTLIER_CSV,
                   help="Path to dt_true_outliers.csv "
                        "(default: %(default)s)")
    p.add_argument("--dry-run", "-n", action="store_true",
                   help="Show planned moves without touching any files.")
    args = p.parse_args()

    if not os.path.isfile(args.outlier_csv):
        sys.exit(f"error: outlier CSV not found: {args.outlier_csv}\n"
                 "Run bin/find_dt_true_outliers.py first to produce it.")
    if not os.path.isdir(args.mseed_dir):
        sys.exit(f"error: mseed dir not found: {args.mseed_dir}")

    pairs, unmapped = load_outlier_pairs(args.outlier_csv)
    print(f"Outlier CSV: {args.outlier_csv}")
    print(f"  loaded {len(pairs)} (date, station) outlier pairs")
    if unmapped:
        print(f"  warning: no SEED mapping for: {sorted(unmapped)}")
    print(f"Mseed dir:   {args.mseed_dir}")
    print(f"Outlier dir: {args.outlier_dir}")
    if args.dry_run:
        print("Mode:        DRY RUN (no files moved)")
    print()

    moved_outliers = 0
    flattened = 0
    conflicts = 0
    unparsed = 0
    per_outlier_bin = defaultdict(int)

    for fpath, year_dir in iter_mseed_files(args.mseed_dir):
        fname = os.path.basename(fpath)
        info = parse_mseed_filename(fname)
        if info is None:
            print(f"  WARN unparseable filename: {fname}")
            unparsed += 1
            continue

        is_outlier = (info["date"], info["sta"]) in pairs

        if is_outlier:
            yyyy = info["date"][:4]
            dst_dir = os.path.join(args.outlier_dir, info["sta"], yyyy)
            dst = os.path.join(dst_dir, fname)
            if os.path.exists(dst):
                print(f"  CONFLICT outlier: {fname} already exists in {dst_dir}")
                conflicts += 1
                continue
            if args.dry_run:
                print(f"  outlier -> {info['sta']}/{yyyy}/{fname}")
            else:
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(fpath, dst)
            moved_outliers += 1
            per_outlier_bin[(info["sta"], yyyy)] += 1
            continue

        # Not an outlier: flatten if currently inside a YYYY/ subdir.
        if year_dir is None:
            continue  # already at top level
        dst = os.path.join(args.mseed_dir, fname)
        if os.path.exists(dst):
            print(f"  CONFLICT flatten: {fname} already at top level")
            conflicts += 1
            continue
        if args.dry_run:
            pass  # too noisy to print every flatten in dry-run
        else:
            shutil.move(fpath, dst)
        flattened += 1

    # Remove any empty YYYY/ subdir — both ones we just emptied and any that
    # were already empty when we started.
    removed_dirs = []
    if not args.dry_run:
        for entry in sorted(os.listdir(args.mseed_dir)):
            if not re.fullmatch(r"\d{4}", entry):
                continue
            ydir = os.path.join(args.mseed_dir, entry)
            if os.path.isdir(ydir) and not os.listdir(ydir):
                os.rmdir(ydir)
                removed_dirs.append(entry)

    print()
    print("──── summary ────")
    print(f"  outlier files moved : {moved_outliers}")
    print(f"  files flattened     : {flattened}")
    print(f"  conflicts skipped   : {conflicts}")
    print(f"  unparseable names   : {unparsed}")
    if per_outlier_bin:
        print("  outliers by (station, year):")
        for (sta, yyyy), n in sorted(per_outlier_bin.items()):
            print(f"    {sta}/{yyyy}: {n}")
    if removed_dirs:
        print(f"  removed empty year dirs: {removed_dirs}")
    if args.dry_run:
        print()
        print("Dry run — no files moved. Re-run without --dry-run to execute.")


if __name__ == "__main__":
    main()
