"""
verify_mseed.py — read back MiniSEED files from testk/output/mseed/ and plot.

Usage (from repo root):
    conda run -n ooi_env python testk/verify_mseed.py
"""

import os
import sys
import glob
from obspy import read
import matplotlib.pyplot as plt

# Accept an optional directory argument, otherwise default to testk/output/mseed
MSEED_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "output", "mseed")

files = sorted(glob.glob(os.path.join(MSEED_DIR, "*.mseed")))
if not files:
    raise SystemExit(f"No .mseed files found in {MSEED_DIR}")

print(f"Found {len(files)} file(s) in {MSEED_DIR}\n")

fig, axes = plt.subplots(len(files), 1, figsize=(14, 4 * len(files)), squeeze=False)

for i, path in enumerate(files):
    st = read(path)
    tr = st[0]
    stats = tr.stats

    print(f"File     : {os.path.basename(path)}")
    print(f"  Network / Station / Location / Channel : "
          f"{stats.network}.{stats.station}.{stats.location}.{stats.channel}")
    print(f"  Start        : {stats.starttime}")
    print(f"  End          : {stats.endtime}")
    print(f"  Duration     : {stats.endtime - stats.starttime:.1f} s")
    print(f"  Samples      : {stats.npts}")
    print(f"  Sampling rate: {stats.sampling_rate:.6f} Hz")
    print(f"  Data min     : {tr.data.min():.4f}")
    print(f"  Data max     : {tr.data.max():.4f}")
    print(f"  Data mean    : {tr.data.mean():.4f}")
    print()

    ax = axes[i][0]
    times = tr.times("matplotlib")
    ax.plot(times, tr.data, lw=0.6, color="steelblue")
    ax.set_title(
        f"{stats.network}.{stats.station}.{stats.location}.{stats.channel}  "
        f"|  {str(stats.starttime)[:10]}  |  {stats.npts} samples",
        fontsize=10
    )
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Pa")
    ax.xaxis_date()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

plt.tight_layout()
out_path = os.path.join(os.path.dirname(__file__), "output", "verify_plot.png")
plt.savefig(out_path, dpi=150)
print(f"Plot saved to: {out_path}")
plt.show()
