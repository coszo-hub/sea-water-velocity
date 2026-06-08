"""
pull_data.py — OOI data pull + MiniSEED conversion for one station, 24-hour window.

Workflow:
  1. Load credentials from .ooi_env
  2. Fetch deployment info
  3. Submit async data request
  4. Poll until ready
  5. Resolve and open NetCDF via OPeNDAP
  6. Detect gaps (robust median Δt)
  7. Split at gaps, convert each segment to MiniSEED
  8. Write .mseed files to testk/output/mseed/

Usage (from repo root):
    conda run -n ooi_env python testk/pull_data.py
"""

import ast
import datetime
import os
import sys
import time
import urllib.request
import xml.etree.cElementTree as ET

import numpy as np
import requests
from netCDF4 import Dataset
from numpy.ma import MaskedArray
from obspy import UTCDateTime, Trace, Stream

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_DIR    = os.path.join(REPO_ROOT, "bin")
sys.path.insert(0, BIN_DIR)
from gap_algorithms import detect_gaps   # noqa: E402

# Algorithm selection — override via env var TESTK_GAP_ALGO=anomaly to exercise
# the new path; default 'legacy' matches what the cron pipeline does today.
GAP_ALGO = os.environ.get("TESTK_GAP_ALGO", "legacy").strip().lower()
PARAM_DIR  = os.path.join(REPO_ROOT, "param")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "mseed")

sys.path.insert(0, BIN_DIR)
from read_param import read_param      # noqa: E402
from convert_utc import utcdata1900    # noqa: E402

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Credentials ───────────────────────────────────────────────────────────────

ooi_env_path = os.path.join(REPO_ROOT, ".ooi_env")
if os.path.exists(ooi_env_path):
    with open(ooi_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

USERNAME = os.environ.get("OOI_USERNAME")
TOKEN    = os.environ.get("OOI_TOKEN")
if not USERNAME or not TOKEN:
    sys.exit("ERROR: OOI credentials not found. Check .ooi_env in the repo root.")

# ── Configuration ─────────────────────────────────────────────────────────────

REFERENCE_DASH = "RS01SLBS-MJ01A-06-PRESTA101"
REFERENCE_URL  = REFERENCE_DASH.replace("-", "/", 2)   # RS01SLBS/MJ01A/06-PRESTA101
REFERENCE_US   = REFERENCE_DASH.replace("-", "_")      # for param file names

# 24-hour window — edit to test a different day
START_TIME = "2025-01-01T00:00:00.000Z"
END_TIME   = "2025-01-02T00:00:00.000Z"

BASE_URL       = "https://ooinet.oceanobservatories.org/api/m2m/12587/events/deployment/inv"
DATA_URL       = "https://ooinet.oceanobservatories.org/api/m2m/12576/sensor/inv"
HTTP_SERVER    = "downloads.oceanobservatories.org/async_results"
OPENDAP_SERVER = "opendap.oceanobservatories.org/thredds/dodsC/ooi"
STREAM_TAG     = "streamed/prest_real_time?include_provenance=true&format=application/netcdf"

MAX_POLL   = 10    # max status polls
POLL_DELAY = 30    # seconds between polls
REC_LEN    = 512   # MiniSEED record length
DATA_QUALITY = "D"

# ── Helpers ───────────────────────────────────────────────────────────────────

def sep():
    print("=" * 60)

# ── Step 1: Deployment info ───────────────────────────────────────────────────

sep()
print(f"Station : {REFERENCE_DASH}")
print(f"Window  : {START_TIME}  →  {END_TIME}")
sep()

print("\n[1] Fetching deployment info...")
dep_url = "/".join([BASE_URL, REFERENCE_URL])
r = requests.get(dep_url, auth=(USERNAME, TOKEN))
r.raise_for_status()
deployment_id = r.json()[-1]   # most recent deployment
print(f"    Deployment ID : {deployment_id}  (HTTP {r.status_code})")

# ── Step 2: Async data request ────────────────────────────────────────────────

print("\n[2] Submitting async data request...")
date_params      = f"beginDT={START_TIME}&endDT={END_TIME}"
data_request_url = "/".join([DATA_URL, REFERENCE_URL, STREAM_TAG])
data_request_url = "&".join([data_request_url, date_params])
print(f"    URL: {data_request_url}")

r = requests.get(data_request_url, auth=(USERNAME, TOKEN))
print(f"    Response: HTTP {r.status_code}")
resp = r.json()

if "No data for request" in str(resp):
    sys.exit("ERROR: OOI returned 'No data for request'.")
if "allURLs" not in resp:
    sys.exit(f"ERROR: No 'allURLs' in response.\n{resp}")

response_url = resp["allURLs"][1]
status_url   = "/".join([response_url, "status.json"])
print(f"    Async result  : {response_url}")

# ── Step 3: Poll until ready ──────────────────────────────────────────────────

print(f"\n[3] Polling status (max {MAX_POLL} × {POLL_DELAY}s)...")
success = False
for attempt in range(1, MAX_POLL + 1):
    time.sleep(POLL_DELAY)
    status = requests.get(status_url)
    ts = datetime.datetime.utcnow().strftime("%H:%M:%S")
    print(f"    [{ts}] Try {attempt}/{MAX_POLL} — HTTP {status.status_code}")
    if status.status_code == 200:
        success = True
        break

if not success:
    sys.exit("ERROR: Data request did not complete within the polling window.")
print("    Ready.")

# ── Step 4: Resolve NetCDF via NCML ──────────────────────────────────────────

print("\n[4] Resolving NetCDF location from NCML...")
ncml_filename = (
    f"deployment{deployment_id:04d}_{REFERENCE_DASH}-streamed-prest_real_time.ncml"
)
ncml_url = "/".join([response_url, ncml_filename])
print(f"    {ncml_url}")

ncml = urllib.request.urlopen(ncml_url)
tree = ET.ElementTree(file=ncml)
ncml.close()

netcdf_loc = None
for child in tree.getroot():
    if "aggregation" in child.tag:
        for element in child:
            netcdf_loc = element.get("location").strip()

if netcdf_loc is None:
    sys.exit("ERROR: No NetCDF location found in NCML.")

opendap_url = (
    "/".join([response_url, netcdf_loc]).replace(HTTP_SERVER, OPENDAP_SERVER)
)
print(f"    OPeNDAP: {opendap_url}")

# ── Step 5: Open NetCDF ───────────────────────────────────────────────────────

print("\n[5] Opening NetCDF...")
fh = Dataset(opendap_url)
t_raw = fh.variables["time"][:]
print(f"    Total data points : {len(t_raw)}")

# Convert to UTCDateTime using seconds-since-1900 epoch
utc_t = [UTCDateTime(str(utcdata1900(float(x)))) for x in t_raw]

# Slice to requested window
req_start = UTCDateTime(START_TIME)
req_end   = UTCDateTime(END_TIME)
start_idx = np.searchsorted(utc_t, req_start, "left")
end_idx   = np.searchsorted(utc_t, req_end,   "right")
new_t     = t_raw[start_idx:end_idx]
new_utc   = utc_t[start_idx:end_idx]
npts      = len(new_t)

print(f"    Points in window  : {npts}")
if npts > 0:
    print(f"    Actual start      : {new_utc[0]}")
    print(f"    Actual end        : {new_utc[-1]}")

# ── Step 6: Gap detection (delegated to bin/gap_algorithms.py) ────────────────

print(f"\n[6] Gap detection (algo={GAP_ALGO})...")
if npts < 2:
    print("    Too few points — treating as single segment, no gap detection.")
    split_idx = []
    sp = None
    sr = 0.0
else:
    t0    = new_utc[0]
    t_sec = np.array([(t - t0) for t in new_utc], dtype=float)
    req_duration = float(req_end - req_start)

    # sp_nominal isn't actually needed by the algorithms (it's only used by
    # the pipeline's email alert); pass 0.0 here.
    gap_result = detect_gaps(GAP_ALGO, t_sec, sp_nominal=0.0,
                             req_duration=req_duration)
    sp        = gap_result.sp
    sr        = gap_result.sr
    gap_idx   = gap_result.gap_idx
    split_idx = list(gap_result.segment_splits)
    is_full   = gap_result.is_full
    dt_all    = gap_result.diagnostics.get("dt_all", np.diff(t_sec))

    print(f"    sp={sp:.9f}s  sr={sr:.9f}Hz  is_full={is_full}  "
          f"n_gaps={gap_result.n_gaps}  n_segments={gap_result.n_segments}")

    if GAP_ALGO == "anomaly":
        d = gap_result.diagnostics
        print(f"    dt_true={d['dt_true']!r}  n_ideal={d['n_ideal']}  "
              f"true_missing={d['true_missing']}  "
              f"jitter_unstable={d['jitter_unstable']}  "
              f"frac_maxabs={d['frac_maxabs']:.4f}")

    if len(gap_idx):
        print(f"    {len(gap_idx)} gap(s):")
        for gi in gap_idx:
            gi = int(gi)
            missing = round(float(dt_all[gi]) / sp) - 1 if sp > 0 else 0
            print(f"      {new_utc[gi]} → {new_utc[gi+1]}  "
                  f"Δt={float(dt_all[gi]):.2f}s (~{missing} missing)")

# ── Step 7: MiniSEED conversion ───────────────────────────────────────────────

print("\n[7] Converting to MiniSEED...")

# Load station/network param
net_sta_param = read_param(os.path.join(PARAM_DIR, f"{REFERENCE_US}.txt"))

# Get channel list for the most recent deployment (index -1 = deployment 1 in Python 1-based convention)
# For seedlink we always use the latest deployment's channel list
dep_key = "channels_2" if "channels_2" in net_sta_param else "channels_1" if "channels_1" in net_sta_param else "channels"
channel_ls_raw = net_sta_param[dep_key][0]
channel_ls = [c.strip() for c in channel_ls_raw[1:-1].split(",")]

dtype_key = dep_key.replace("channels", "data_types")
if dtype_key not in net_sta_param:
    dtype_key = "data_types_1" if "data_types_1" in net_sta_param else "data_types"
datatype_raw = net_sta_param[dtype_key][0]
datatype_ls  = datatype_raw if isinstance(datatype_raw, dict) else ast.literal_eval(datatype_raw)

print(f"    Channels : {channel_ls}")

# Split timestamps at detected gaps
new_utc_arr = np.array([str(x) for x in new_utc], dtype=object)
data_split  = np.split(new_utc_arr, split_idx)

files_written = []

for channel in channel_ls:
    var_name   = datatype_ls[channel]
    data_point = fh.variables[var_name][:]
    chan_param = read_param(os.path.join(PARAM_DIR, f"{REFERENCE_US}_{channel}.txt"))

    sr_val = 1.0 / sp if (sp and sp > 0) else float(chan_param["c_sample_rate"][0])
    r_value = float(chan_param["r_value"][0])

    seg_offset = start_idx
    for seg in data_split:
        seg_start = seg[0]
        seg_end   = seg[-1]

        seg_data = data_point[seg_offset : seg_offset + len(seg)].copy()
        if isinstance(seg_data, MaskedArray):
            seg_data = seg_data.filled(np.nan)

        # Unit conversion (e.g. PSIA → Pa)
        seg_data = seg_data / r_value

        stats = {
            "network"       : net_sta_param["net"][0],
            "station"       : net_sta_param["sta"][0],
            "location"      : chan_param["c_loc"][0],
            "channel"       : chan_param["cha"][0],
            "npts"          : len(seg_data),
            "sampling_rate" : sr_val,
            "starttime"     : UTCDateTime(seg_start),
            "mseed"         : {"dataquality": DATA_QUALITY},
        }

        st = Stream([Trace(data=seg_data, header=stats)])

        # Filename: NET.STA.LOC.CHA.YEAR.DOY.HH.MM.SS.sss-YEAR.DOY.HH.MM.SS.sss.mseed
        def fmt_ts(ts_str):
            dt = datetime.datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            doy = dt.timetuple().tm_yday
            return f"{dt.year}.{doy:03d}.{ts_str[11:23].replace(':', '.')}"

        mseed_name = (
            f"{stats['network']}.{stats['station']}."
            f"{stats['location']}.{stats['channel']}."
            f"{fmt_ts(seg_start)}-{fmt_ts(seg_end)}.mseed"
        )
        write_path = os.path.join(OUTPUT_DIR, mseed_name)

        encoding = str(st[0].data.dtype).upper()
        st.write(write_path, format="MSEED", encoding=encoding, reclen=REC_LEN)
        files_written.append(write_path)
        print(f"    Wrote: {mseed_name}")

        seg_offset += len(seg)

fh.close()

# ── Summary ───────────────────────────────────────────────────────────────────

sep()
print(f"  MiniSEED files written : {len(files_written)}")
print(f"  Output directory       : {OUTPUT_DIR}")
sep()
print("Done.")
