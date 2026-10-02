#!/usr/bin/env python3
"""daily_alerts.py — email alerts for one daily SeedLink job (run_daily_seedlink.sh).

Replaces the per-request emails of the old M2M live pipeline. Sends ONE email
per job, and only when something needs attention:

  - job crashed                 backfill exited non-zero (log tail attached)
  - day(s) FAILED               OOI / server error — retried automatically next run
  - new data gap                OOI confirmed no data for a day
  - sample-rate deviation       |Δt_true − nominal| > sp_alert_abs_floor, or
                                relative > sp_alert_rel_frac (run file)
  - OOI excess-sample period    duplicate / foreign records removed
  - heavy fragmentation         > FRAG_ALERT_PCT of a day dropped as < 60 s pieces
  - deployment ending           current deployment ends within deploy_warn_days
  - deployment not in params    OOI's deployment API lists more deployments than
                                the param files (add it via make_*_params.py)

Usage: daily_alerts.py <REFDES> <STREAM|-> <START> <END> <RUN_LOG> <EXIT_CODE>
"""
import csv
import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "bin"))

from obspy import UTCDateTime                                     # noqa: E402
from read_param import read_param                                 # noqa: E402
from mail import sendmail                                         # noqa: E402
from diagnose_timing import get_deployment_for_date, load_credentials  # noqa: E402
from temporal_anomaly_investigator import _metrics_csv_path, series_label  # noqa: E402

FRAG_ALERT_PCT = 10.0


def _run_param():
    run_files = [f for f in os.listdir(os.path.join(REPO_ROOT, "param"))
                 if f.startswith("run_") and f.endswith(".txt") and f != "run_metadata.txt"]
    return read_param(os.path.join(REPO_ROOT, "param", run_files[0])) if run_files else {}


def _ooi_deployment_count(station):
    """Number of deployments OOI lists for this refdes (None on any error)."""
    try:
        import requests
        run = _run_param()
        base = run["base_url"][0]
        u, t = load_credentials()
        r = requests.get("/".join([base, station.replace("-", "/", 2)]), auth=(u, t), timeout=60)
        r.raise_for_status()
        return len(r.json())
    except Exception:
        return None


def _param_deployments(station, stream):
    """(number of deployment epochs, c_end of the last one) from the param files."""
    ref = station.replace("-", "_")
    sp = read_param(os.path.join(REPO_ROOT, "param", f"{ref}.txt"))
    if "channels" in sp:
        chans = [c.strip() for c in sp["channels"][0].strip("[]").split(",")]
    else:
        keys = sorted(k for k in sp if k.startswith("channels_"))
        chans = [c.strip() for c in sp[keys[-1]][0].strip("[]").split(",")]
    for c in chans:
        cp = read_param(os.path.join(REPO_ROOT, "param", f"{ref}_{c}.txt"))
        if stream in (None, cp.get("c_stream", [None])[0]):
            ends = cp["c_end"]
            n = len(ends) if "channels" in sp else len([k for k in sp if k.startswith("channels_")])
            return n, ends[-1].strip()
    return None, None


def main():
    station, stream, start, end, run_log, exit_code = sys.argv[1:7]
    stream = None if stream == "-" else stream
    label = series_label(station, stream)
    log = open(run_log, errors="replace").read() if os.path.exists(run_log) else ""
    run = _run_param()
    issues = []

    if exit_code != "0":
        tail = "\n".join(log.splitlines()[-30:])
        issues.append(f"JOB CRASHED (exit {exit_code}). Last log lines:\n{tail}")

    failed = re.findall(r"\] (\d{4}-\d{2}-\d{2})  FAILED[^\n]*— (.*)", log)
    if failed:
        issues.append("Day(s) FAILED (retried automatically next run):\n" +
                      "\n".join(f"  {d}: {why[:200]}" for d, why in failed))

    gaps = re.findall(r"\] (\d{4}-\d{2}-\d{2})  skip — ((?:gold copy has no|OOI|No data|No deployment)[^\n]*)", log)
    if gaps:
        issues.append("New data gap(s) recorded (OOI has no data):\n" +
                      "\n".join(f"  {d}: {why[:160]}" for d, why in gaps))

    extra = re.findall(r"\] (\d{4}-\d{2}-\d{2})  removed (\d+) extra records \(([\d.]+)%", log)
    if extra:
        issues.append("OOI excess-sample period — extra records removed (duplicates / invalid clock):\n" +
                      "\n".join(f"  {d}: {n} records ({p}% of the day)" for d, n, p in extra))

    # backfill prints the "dropped …" line just before that day's "algo=" line
    frag = [(m.group(2), m.group(1)) for m in re.finditer(
        r"dropped \d+ trace\(s\)/ch shorter than \S+ \(\d+ samples, ([\d.]+)% of the day\)\n"
        r"\s+\[\S+\] (\d{4}-\d{2}-\d{2})", log) if float(m.group(1)) > FRAG_ALERT_PCT]
    if frag:
        issues.append(f"Heavily fragmented day(s) (> {FRAG_ALERT_PCT:g}% dropped as < 60 s pieces):\n" +
                      "\n".join(f"  {d}: {p}%" for d, p in sorted(set(frag))))

    # sample-rate deviation on the days produced in this window
    abs_floor = float(run.get("sp_alert_abs_floor", ["0.05"])[0])
    rel_frac = float(run.get("sp_alert_rel_frac", ["0.05"])[0])
    csv_path = _metrics_csv_path(station, stream)
    if os.path.exists(csv_path):
        dev = []
        for r in csv.DictReader(open(csv_path, newline="")):
            if not (start <= r["date"] <= end) or r["has_data"] != "True":
                continue
            try:
                dt_true, sp = float(r["dt_true"]), float(r["sp_nominal"])
            except (TypeError, ValueError):
                continue
            if sp and (abs(dt_true - sp) > abs_floor or abs(dt_true - sp) / sp > rel_frac):
                dev.append(f"  {r['date']}: Δt_true={dt_true:.6f} s vs nominal {sp:.6f} s "
                           f"({100 * abs(dt_true - sp) / sp:.2f}%)")
        if dev:
            issues.append("Sample-rate deviation beyond alert thresholds "
                          f"(abs {abs_floor:g} s / rel {100 * rel_frac:g}%):\n" + "\n".join(dev))

    # deployments: ending soon / OOI has one the params don't
    warn_days = float(run.get("deploy_warn_days", ["30"])[0])
    today = UTCDateTime()
    try:
        get_deployment_for_date(station, today, os.path.join(REPO_ROOT, "param"), stream=stream)
    except ValueError:
        issues.append("No deployment in the param files covers today — the instrument was "
                      "recovered or a new deployment needs adding to the params/StationXML.")
    n_param, last_end = _param_deployments(station, stream)
    if last_end and last_end not in ("None", "CHAIN"):
        left = (UTCDateTime(last_end) - today) / 86400.0
        if 0 < left <= warn_days:
            issues.append(f"Current deployment ends {last_end[:10]} ({left:.0f} days).")
    n_ooi = _ooi_deployment_count(station)
    if n_ooi is not None and n_param is not None and n_ooi > n_param:
        issues.append(f"OOI lists {n_ooi} deployments but the param files have {n_param} — add the "
                      "new deployment (make_*_params.py → create_metadata.py) so metadata matches.")

    if not issues:
        print(f"alerts: none for {label} ({start} → {end})")
        return
    subject = f"OOI SeedLink [{label}]: {len(issues)} issue(s) {start}..{end}"
    body = f"{label}  ({station}{' / ' + stream if stream else ''})\nwindow {start} → {end}\n\n" + \
           "\n\n".join(issues)
    print(subject + "\n" + body)
    try:
        sendmail(subject, body)
        print("alert email sent")
    except Exception as e:                       # never let alerting break the job
        print(f"WARN: alert email failed: {e}")


if __name__ == "__main__":
    main()
