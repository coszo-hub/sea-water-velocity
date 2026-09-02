#!/usr/bin/env python3
"""
fix_staged_mseed_codes.py — bring already-staged MiniSEED in line with current
metadata codes, in place, before EarthScope drop-off.

The 2026-08 metadata revisions renamed things AFTER historical backfills had
already been run on the collection VM, so staged files under
output/mseed2dmc/<YEAR>/ may still carry old codes:

  station  SHBP  -> CZSHF          (Endurance OR Shelf)
  station  OSBP  -> CZOFF          (Endurance OR Offshore)
  channel  MOE/MON/MOZ -> MOU/MOV/MOW   (VEL3D-C only; U/V/W orientation codes)
  location CZOFF: 20 -> 21         (loc 20 reserved for future instrument)

This script walks a staging directory, and for every *.mseed whose
filename codes (NET.STA.LOC.CHA.<times>) are outdated it rewrites the
MiniSEED headers AND renames the file to match. Already-current files are
left untouched (not even read). Any backfill vintage is handled — files
needing only some of the renames get only those.

Usage:
    python bin/fix_staged_mseed_codes.py --dry-run     # preview only
    python bin/fix_staged_mseed_codes.py               # fix output/mseed2dmc/
    python bin/fix_staged_mseed_codes.py --dir output/mseed2dmc_sent

Then upload with: bin/dropoff_earthscope.sh mseed [--archive]
"""
import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATION_MAP = {"SHBP": "CZSHF", "OSBP": "CZOFF"}
# U/V/W nonstandard-orientation codes apply to the VEL3D-C (Nortek) stations.
SERIES_C_STATIONS = {"CZSHF", "CZOFF"}
CHANNEL_MAP = {"MOE": "MOU", "MON": "MOV", "MOZ": "MOW"}
LOC_MAP = {"CZOFF": "21"}  # per-station location override (all channels)


def target_codes(sta, loc, cha):
    """Map (station, location, channel) to their current values."""
    sta = STATION_MAP.get(sta, sta)
    if sta in SERIES_C_STATIONS:
        cha = CHANNEL_MAP.get(cha, cha)
    loc = LOC_MAP.get(sta, loc)
    return sta, loc, cha


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dir", default=os.path.join(REPO_ROOT, "output", "mseed2dmc"),
                    help="staging directory to fix (default: output/mseed2dmc)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print planned renames without touching any file")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        sys.exit(f"FATAL: directory not found: {args.dir}")

    n_seen = n_fixed = n_skipped = 0
    for dirpath, _dirnames, filenames in os.walk(args.dir):
        for fname in sorted(filenames):
            if not fname.lower().endswith((".mseed", ".ms")):
                continue
            n_seen += 1
            parts = fname.split(".")
            if len(parts) < 5:
                print(f"WARN: unexpected filename format, skipping: {fname}")
                n_skipped += 1
                continue
            net, sta, loc, cha = parts[0], parts[1], parts[2], parts[3]
            new_sta, new_loc, new_cha = target_codes(sta, loc, cha)
            if (new_sta, new_loc, new_cha) == (sta, loc, cha):
                continue  # already current — do not even read it

            new_fname = ".".join([net, new_sta, new_loc, new_cha] + parts[4:])
            rel = os.path.relpath(dirpath, args.dir)
            print(f"{rel}/{fname}\n  -> {rel}/{new_fname}")
            n_fixed += 1
            if args.dry_run:
                continue

            # Deferred import so --dry-run works without obspy.
            from obspy import read
            in_path = os.path.join(dirpath, fname)
            out_path = os.path.join(dirpath, new_fname)
            st = read(in_path)
            reclen = None
            for tr in st:
                tr.stats.station = new_sta
                tr.stats.location = new_loc
                tr.stats.channel = new_cha
                reclen = getattr(tr.stats, "mseed", {}).get("record_length", reclen)
            if reclen:
                st.write(out_path, format="MSEED", reclen=reclen)
            else:
                st.write(out_path, format="MSEED")
            os.remove(in_path)

    verb = "would fix" if args.dry_run else "fixed"
    print(f"\n{n_seen} file(s) scanned; {verb} {n_fixed}; "
          f"{n_seen - n_fixed - n_skipped} already current; {n_skipped} skipped.")


if __name__ == "__main__":
    main()
