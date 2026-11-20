#!/home/coszo/miniconda3/envs/ooi_env/bin/python
"""Daily metadata check: compare OOI deployment info against our param files.

For every sensor in param/run_metadata.txt that has a param file:
  1. Fetch the most recent OOI deployment for that sensor.
  2. Compare it with the open (current) channel epochs in the param files:
     deployment start time, deployment stop time, sensor UID, and position
     (within POS_TOL_DEG / DEPTH_TOL_M).
  3. If they disagree: email the operator, do NOT touch that station's XML.
     The param files must be updated first (they are the source of truth).
  4. If they agree: rebuild the StationXML from the params into a temp dir and
     replace output/xml/<file> only if the station content actually changed
     (the <Created> and <Module> header lines are ignored). A replaced file means
     the metadata changed and must be re-submitted to EarthScope (dropoff).

Exit status is 1 if anything needs attention, else 0.
"""

# 2025-01-07 Mika: Removed code that creates dataless seed from stationxml. Only need to provide EarthScope with metadata in one format or the other.
# 2025-09-23 Mika: This script has been reformatted to be compatible with Python 3.10.
# 2026-11-18: Replaced the disabled comparison with a real check; XML is only
#             rewritten when its content changes.
# 2026-11-20: Request-only errors are retried once at 14:00 UTC before emailing.

import ast
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile

import requests

from mail import sendmail
from read_param import read_param

# Sensor types this repo is responsible for (matched against OOI sensor names).
SENSOR_WORDS = ["VEL3D"]  # VEL3DB + VEL3DC

# Tolerances for position checks. Params hold one fixed site position, while OOI
# records a slightly different position per deployment (observed up to ~20 m).
POS_TOL_DEG = 0.001  # ~110 m in latitude
DEPTH_TOL_M = 10.0

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
param_path = os.path.join(root_path, "param")
bin_path = os.path.join(root_path, "bin")
xml_path = os.path.join(root_path, "output", "xml")
create_metadata = os.path.join(bin_path, "create_metadata.py")

USERNAME = os.environ.get("OOI_USERNAME")
TOKEN = os.environ.get("OOI_TOKEN")
if not USERNAME or not TOKEN:
    raise RuntimeError("Missing OOI credentials. Set OOI_USERNAME and OOI_TOKEN in the environment.")
AUTH = (USERNAME, TOKEN)

run_params = read_param(os.path.join(param_path, "run_metadata.txt"))
reference_id = ast.literal_eval(run_params["reference_id"][0])
base_url = run_params["base_url"][0]


def get_json(url):
    r = requests.get(url, auth=AUTH, timeout=60)
    r.raise_for_status()
    return r.json()


def ms_to_iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def same_time(a, b):
    """Compare two ISO times (or None / 'None') to the second."""
    a = None if a in (None, "None") else a
    b = None if b in (None, "None") else b
    if a is None or b is None:
        return a is b
    p = lambda s: dt.datetime.fromisoformat(s.replace("Z", "")[:19])
    return p(a) == p(b)


def xml_body(path):
    """StationXML text without the lines that change on every rebuild."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return re.sub(r"^\s*<(Created|Module)>.*</\1>\s*$", "", text, flags=re.M)


def check_station(name, dash):
    """Return a list of mismatch strings (empty if params match OOI)."""
    ns = read_param(os.path.join(param_path, name + ".txt"))
    url = "/".join([base_url, dash.replace("-", "/", 2)])
    dep_num = max(get_json(url))
    dep = get_json(f"{url}/{dep_num}")[0]

    ooi_start = ms_to_iso(dep.get("eventStartTime"))
    ooi_stop = ms_to_iso(dep.get("eventStopTime"))
    ooi_uid = (dep.get("sensor") or {}).get("uid")
    loc = dep.get("location") or {}
    print(f"OOI deployment {dep_num}: start={ooi_start} stop={ooi_stop} uid={ooi_uid} "
          f"lat={loc.get('latitude')} lon={loc.get('longitude')} depth={loc.get('depth')}")

    problems = []
    open_epochs = 0
    for ch in ns["metadata_channels"][0][1:-1].split(","):
        cp = read_param(os.path.join(param_path, f"{name}_{ch}.txt"))
        starts, ends = cp["c_start"], cp["c_end"]
        last = len(starts) - 1
        ch_start, ch_end = starts[last], ends[last]

        if ch_end not in (None, "None"):
            # Closed channel. Only matters if it is the epoch OOI's latest deployment refers to.
            if same_time(ch_start, ooi_start) and not same_time(ch_end, ooi_stop):
                problems.append(f"{ch}: params end {ch_end}, OOI deployment {dep_num} stop is {ooi_stop}")
            continue

        open_epochs += 1
        print(f"  {ch}: open epoch start={ch_start} uid={cp.get('c_id', ['?'])[-1]}")
        if not same_time(ch_start, ooi_start):
            problems.append(f"{ch}: params current epoch starts {ch_start}, OOI deployment {dep_num} starts {ooi_start}")
        if ooi_stop is not None:
            problems.append(f"{ch}: params epoch is open, but OOI deployment {dep_num} ended {ooi_stop}")
        if "c_id" in cp and ooi_uid and cp["c_id"][-1] != ooi_uid:
            problems.append(f"{ch}: params sensor {cp['c_id'][-1]}, OOI sensor {ooi_uid}")
        lat, lon, elev = (float(cp[k][-1]) for k in ("c_lat", "c_lon", "c_elev"))
        if loc.get("latitude") is not None and abs(lat - loc["latitude"]) > POS_TOL_DEG:
            problems.append(f"{ch}: params lat {lat}, OOI {loc['latitude']}")
        if loc.get("longitude") is not None and abs(lon - loc["longitude"]) > POS_TOL_DEG:
            problems.append(f"{ch}: params lon {lon}, OOI {loc['longitude']}")
        if loc.get("depth") is not None and abs(-elev - loc["depth"]) > DEPTH_TOL_M:
            problems.append(f"{ch}: params elev {elev}, OOI depth {loc['depth']}")

    if open_epochs == 0 and ooi_stop is None:
        problems.append(f"no open channel epochs in params, but OOI deployment {dep_num} (start {ooi_start}) is ongoing")
    return problems


def rebuild_xml(name):
    """Rebuild StationXML; replace output/xml files only if content changed. Returns changed paths."""
    changed = []
    os.makedirs(xml_path, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, create_metadata, name, tmp], check=True)
        for fn in sorted(os.listdir(tmp)):
            new, cur = os.path.join(tmp, fn), os.path.join(xml_path, fn)
            if os.path.exists(cur) and xml_body(cur) == xml_body(new):
                print(f"  {fn}: unchanged, left as is")
                continue
            shutil.copyfile(new, cur)
            changed.append(cur)
            print(f"  {fn}: CONTENT CHANGED, written to output/xml")
    return changed


mismatches, changed, errors = {}, [], []

for subsite, node in reference_id:
    print("===============================")
    print("Reference:", subsite, node)
    try:
        sensors = get_json("/".join([base_url, subsite, node]))
    except Exception as e:
        errors.append(f"{subsite}-{node}: OOI sensor list request failed: {e}")
        continue

    for sensor in sensors:
        if not any(w in sensor for w in SENSOR_WORDS):
            continue
        dash = f"{subsite}-{node}-{sensor}"
        name = dash.replace("-", "_")
        if not os.path.isfile(os.path.join(param_path, name + ".txt")):
            print(f"[ERROR] parameter file not found for {dash}")
            errors.append(f"{dash}: OOI lists this sensor but there is no param file")
            continue

        print(f"\n{dash}")
        try:
            problems = check_station(name, dash)
        except Exception as e:
            errors.append(f"{dash}: check failed: {e}")
            continue

        if problems:
            mismatches[dash] = problems
            print("  Result: METADATA MISMATCH, XML not rebuilt")
            for p in problems:
                print("   -", p)
            continue

        print("  Result: params match OOI")
        try:
            changed += rebuild_xml(name)
        except Exception as e:
            errors.append(f"{dash}: StationXML rebuild failed: {e}")

repo = os.path.basename(root_path)
lines = []
if mismatches:
    lines.append("OOI deployment info differs from the param files. Update the params "
                 "(they are the source of truth for StationXML), then resubmit the XML to EarthScope.\n")
    for dash, probs in mismatches.items():
        lines.append(dash)
        lines += [f"  - {p}" for p in probs]
    lines.append("")
if changed:
    lines.append("StationXML content changed; resubmit to EarthScope (bin/dropoff_earthscope.sh xml):")
    lines += [f"  {p}" for p in changed]
    lines.append("")
if errors:
    lines.append("Errors:")
    lines += [f"  - {e}" for e in errors]

# OOI request errors are often transient. On the first run of the day, if errors are
# the only problem, skip the email and leave a marker; the 14:00 UTC cron re-runs the
# check only when that marker exists, and that second run emails if it still fails.
retry_marker = os.path.join(root_path, "log", ".metadata_retry_pending")
today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
is_retry = False
if os.path.exists(retry_marker):
    with open(retry_marker) as f:
        is_retry = f.read().strip() == today
    os.remove(retry_marker)

print("===============================")
if lines:
    body = "\n".join(lines)
    print(body)
    if errors and not mismatches and not changed and not is_retry:
        with open(retry_marker, "w") as f:
            f.write(today + "\n")
        print("Only request errors; no email yet. Retrying at 14:00 UTC.")
        sys.exit(1)
    if is_retry:
        body = "Failed again on the 14:00 UTC retry (first attempt at 13:00 UTC also failed).\n\n" + body
    subject = f"OOI metadata check ({repo}): " + ", ".join(
        s for s, on in (("MISMATCH", mismatches), ("XML changed", changed), ("errors", errors)) if on)
    sendmail(subject, body)
    sys.exit(1)

print("All stations match OOI; StationXML unchanged.")
