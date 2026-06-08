from obspy import UTCDateTime, Trace, Stream
from netCDF4 import Dataset
import numpy as np
import os
import sys
import time
import urllib.request
import xml.etree.cElementTree as ET
import requests
import datetime
import ast
import bisect
import csv
from numpy.ma import MaskedArray
from convert_utc import *
from read_param import *
from mail import *
from gap_algorithms import detect_gaps

# Send test email
# sendmail('TEST EMAIL SEND FROM COSZO')
# sys.exit()

# 2026-03-23 Mika: Updated the gap detection to be more robust. 
# 2026-01-07 Mika: This is a modified version of OOI_data_bundled.py that calculates a new sample period per data request. 

# Get args
import argparse as _argparse

_parser = _argparse.ArgumentParser(
    description="OOI data request → MiniSEED conversion (one 24 h window).",
    add_help=False,   # keep the script's existing print-and-exit ergonomics
)
_parser.add_argument("reference_name", help="Station ref designator (dashes, not underscores)")
_parser.add_argument("run_name",        help="Run param file name (without 'run_' prefix or '.txt')")
_parser.add_argument("transfer_method", choices=["seedlink", "miniseed2dmc"])
_parser.add_argument("--save-nc", action="store_true",
                     help="Save a local copy of the fetched NetCDF (manual / ad-hoc runs only; "
                          "the cron wrapper does not pass this flag).")

try:
    _cli = _parser.parse_args()
except SystemExit:
    print("Need to pass 'reference_name' (with dashes not underscores), 'run_name', and "
          "'transfer_method' (seedlink or miniseed2dmc) arguments. "
          "Optional: --save-nc for ad-hoc local NetCDF capture.")
    raise

reference_name_dash = _cli.reference_name
run_name            = _cli.run_name
transfer_method     = _cli.transfer_method
cli_save_nc         = _cli.save_nc
    
reference_name_underscore = reference_name_dash.replace("-", "_") # Used in param file names

mseed_file_ext = ".mseed"

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
output_path = os.path.join(root_path, "output")
param_path = os.path.join(root_path, "param")
bin_path = os.path.join(root_path, "bin")
run_path = os.path.join(root_path, "run")
if transfer_method == 'seedlink':
    log_path = os.path.join(root_path, "log")
elif transfer_method == 'miniseed2dmc':
    log_path = os.path.join(root_path, "log_mseed2dmc")
if transfer_method == 'seedlink':
    mseed_path = os.path.join(output_path, "mseed") # Data to be transferred by SeedLink via mseedscan
elif transfer_method == 'miniseed2dmc':
    mseed_path = os.path.join(output_path, "mseed2dmc") # Data to be transferred via miniseed2dmc
netcdf_path = os.path.join(output_path, "netcdf")  # Local copy of fetched NetCDFs
diag_log_path = os.path.join(output_path, "diagnostics")  # Per-event diagnostic logs
metrics_path  = os.path.join(output_path, "metrics")       # Per-day pipeline stats CSVs

sys.path.insert(0, bin_path)

# ── Diagnostic event logger ────────────────────────────────────────────────
# Appends one line per event to output/diagnostics/<event>_<station>_<run>.txt
# event_type: "gaps" | "sp_deviation" | "no_data" | "too_few_points"
def append_diag_log(event_type, station, run, details):
    os.makedirs(diag_log_path, exist_ok=True)
    fname = f"{event_type}_{station}_{run}.txt"
    fpath = os.path.join(diag_log_path, fname)
    ts    = str(UTCDateTime.now())[:19] + "Z"
    with open(fpath, "a") as f:
        f.write(f"{ts}  {details}\n")

# ── Per-day pipeline stats CSV ─────────────────────────────────────────────
# Append-only, idempotent on (date, station, run). One row per 24 h window.
# Lives at output/metrics/<station>_<run>_pipeline_stats.csv — these CSVs
# (and the diagnostics dir above) are git-tracked inside the monorepo, so
# the daily sync just commits + pushes whatever the cron has appended.
METRICS_FIELDS = [
    "date", "station", "run", "deployment",
    "algorithm", "algorithm_requested", "boundary_in_window",
    "n_points", "expected_npts", "is_full",
    "sp", "sr", "sp_nominal",
    "sp_deviation", "sp_deviation_alert_fired",
    # legacy-only diagnostics
    "multiplier", "gap_threshold",
    # anomaly-only diagnostics
    "dt_true", "n_ideal", "true_missing",
    "n_gaps_raw", "jitter_unstable", "frac_maxabs",
    # shared
    "n_gaps", "n_segments", "gap_total_missing_est",
]

def _metrics_csv_path(station, run):
    return os.path.join(metrics_path, f"{station}_{run}_pipeline_stats.csv")

def metrics_row_exists(station, run, date_str):
    fpath = _metrics_csv_path(station, run)
    if not os.path.exists(fpath):
        return False
    with open(fpath, newline="") as f:
        for r in csv.DictReader(f):
            if r["date"] == date_str:
                return True
    return False

def append_metrics_row(station, run, row):
    fpath = _metrics_csv_path(station, run)
    new_file = not os.path.exists(fpath)
    os.makedirs(metrics_path, exist_ok=True)
    with open(fpath, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=METRICS_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)

run_file_name = "run_" + run_name + ".txt"

url_designator = reference_name_dash.replace("-", "/", 2)

old_stdout = sys.stdout
old_stderr = sys.stderr

log_file_name = str(UTCDateTime.now())[0:10] + "-" + reference_name_dash + "-" + run_name + ".log"
log_file = open(os.path.join(log_path, log_file_name), "a")

sys.stdout = log_file
sys.stderr = log_file

try:

    run = read_param(os.path.join(param_path, run_file_name))
    print("========================== ")
    print(str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + " - GET OOI DATA " + reference_name_dash + "-" + run_name)

    # Optional NetCDF capture: CLI --save-nc takes precedence; param-file
    # `save_netcdf` is deprecated and only honoured when the flag is absent
    # (kept for backwards compatibility; will be removed in Phase 4).
    save_netcdf = cli_save_nc or (int(run.get("save_netcdf", [0])[0]) == 1)

    USERNAME = os.environ.get("OOI_USERNAME")
    TOKEN = os.environ.get("OOI_TOKEN")

    if not USERNAME or not TOKEN:
        raise RuntimeError(
            "Missing OOI credentials. "
            "Set OOI_USERNAME and OOI_TOKEN in the environment."
        )

    if transfer_method == 'seedlink':
        endtime_file_name = "endtime_" + reference_name_dash + "_" + run_name + ".txt" 
    elif transfer_method == 'miniseed2dmc':
        endtime_file_name = "endtime_" + reference_name_dash + "_" + run_name + "_mseed2dmc.txt"      
    file_end_time = UTCDateTime(open(os.path.join(run_path, endtime_file_name), "r").read()) # End time of previous data request

    # Get data request start time (end time of previous request)
    start_time = str(file_end_time) 
    print("REQUESTED START TIME:", start_time)

    # Enable log file for recording data gaps (optional)
    gap_enabled = int(run.get("gap", [0])[0]) == 1
    if gap_enabled:
        gap_file_name = "gap_" + reference_name_dash + "_" + run_name + ".txt"

    diag_sp_alert_fired = False   # set True below if sp-deviation email is sent
    diag_sp_nominal     = None    # captured from alert block for diagnostic use
    metrics_sp_dev      = None    # captured from alert block for the per-day stats CSV
        
    # Get data request end time = start_time + time_interval from run_prest.txt. Time_interval = 86400 s (24 hours). MT
    end_time = str(UTCDateTime(start_time) + float(run["time_interval"][0]))
    print("REQUESTED END TIME:", end_time)

    # (Optional) Introduce a delay (e.g., trunc_time in run_prest.txt) in data requests to allow time for data ingestion into the OOI database. Currently, trunc_time = 0. MT
    if (run["data_endtime"][0] == "NOW" or UTCDateTime(run["data_endtime"][0]) >= UTCDateTime.now()):
        print("trunc_time set = now() - trunc_time parameter " + str(float(run["trunc_time"][0])))
        trunc_time = UTCDateTime.now() - float(run["trunc_time"][0])
    else:
        print("trunc_time set = data_endtime")
        trunc_time = UTCDateTime(run["data_endtime"][0])

    # If trunc_time > 0, introduces a delay in data requests to allow time for data ingestion into the OOI database. MT.
    # if UTCDateTime(endTime) <= UTCDateTime(truncTime) and difTime >= float(run["diftime"][0]): --> Original logic. I don't think the difTime comparison is needed. MT.
    if UTCDateTime(end_time) <= UTCDateTime(trunc_time):
        base_url = run["base_url"][0]
        data_url = run["data_url"][0]

        # Stream tag
        if 'prest' in run_name:
            stream_tag = "streamed/" + run_name + "_real_time?include_provenance=true&format=application/netcdf"
        elif 'lily' in run_name or 'nano' in run_name:
            stream_tag = "streamed/botpt_" + run_name + "_sample?include_provenance=true&format=application/netcdf"

        # Deployment info
        url = "/".join([base_url, url_designator])
        response = requests.get(url, auth=(USERNAME, TOKEN))
        if transfer_method == 'seedlink':
            deployment_id = response.json()[-1] # Most recent deployment, MT
        elif transfer_method == 'miniseed2dmc':
            deployment = int(run["deployment"][0]) - 1 # Minus one for Python indexing
            deployment_id = response.json()[deployment] # Selected deployment, MT
        print("url for deployment_id: ", url, "response", response.status_code)
        print('deployment_id:', deployment_id)
        url = "/".join([url, str(deployment_id)])
        response = requests.get(url, auth=(USERNAME, TOKEN))
        deployment_info = response.json()[0]
        print("url for deployment_info: ", url, "response", response.status_code)
        
        # Data request
        date_tag = "".join(["beginDT=", start_time, "&", "endDT=", end_time])
        url = "/".join([data_url, url_designator, stream_tag])
        url = "&".join([url, date_tag])
        response = requests.get(url, auth=(USERNAME, TOKEN))
        print("url for data: ", url, "response", response.status_code)
        
        # Send email to operator if there is no data for request
        if "No data for request" in response.json():
            print("No data for request. Sending email to investigate and alert OOI helpdesk.")
            sendmail("OOI WARNING: No data for request! " + reference_name_dash + "-" + run_name,
                    "OOI data collection may have failed! Investigate and alert OOI Help Desk " + url)
            append_diag_log("no_data", reference_name_dash, run_name,
                            f"NO_DATA: {start_time} → {str(UTCDateTime(start_time) + float(run['time_interval'][0]))}")
            sys.exit()

        # If data request fails, email is sent to operator, data gap is logged, advances endtime_...txt by time_interval, and exits program.
        if "allURLs" not in response.json():
            print("Request [FAILED] - exiting! Skipping this time interval:",
                next(iter(response.json().keys())) + ":",
                next(iter(response.json().values())))
            with open(os.path.join(run_path, endtime_file_name), "w") as output:
                output.write(str(UTCDateTime(start_time) + float(run["time_interval"][0])))
            print(endtime_file_name, "set =", str(UTCDateTime(start_time) + float(run["time_interval"][0])))
            if gap_enabled:
                with open(os.path.join(log_path, gap_file_name), "a") as output:
                    output.write("%s %s %s\n" % ("Data request failed. Skipping this time interval:",
                                                str(UTCDateTime(start_time)),
                                                str(UTCDateTime(start_time) + float(run["time_interval"][0]))))
            append_diag_log("no_data", reference_name_dash, run_name,
                            f"REQUEST_FAILED (HTTP {response.status_code}): "
                            f"{str(UTCDateTime(start_time))} → "
                            f"{str(UTCDateTime(start_time) + float(run['time_interval'][0]))}")
            if response.status_code >= 400:
                print("Error! Response = " + str(response.status_code))
                sendmail("OOI ERROR: OOI request " + reference_name_dash + "-" + run_name + " got status code " + str(response.status_code),
                        "URL [" + url + "]")
            sys.exit()
            
        # [CHATGPT FIX #2] Safely access allURLs to avoid index errors
        urls = response.json().get("allURLs", [])
        if len(urls) < 2:
            raise RuntimeError("Unexpected allURLs format in response")
        response_url = urls[1]
        status_url = "/".join([response_url, "status.json"])  # async status
        
        # Poll status.json up to 5 times with 50 s delay between tries. If the job never reaches 200/complete, it sends a warning via email, logs the data gap, advances endtime_...txt by the time_interval, and exits program.
        count = 0
        max_cycle = int(run["max_cycle"][0])
        delay = int(run["delay"][0])
        success = False
        response_codes = ""
        while count <= max_cycle:
            count += 1
            time.sleep(delay)
            status = requests.get(status_url)
            print("url for status", status_url, "response", status.status_code)
            print(status.content)
            if status.status_code == 200:
                print(str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")), "[TRY]", str(count), "[DONE]", "Success")
                success = True
                break
            else:
                print(str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")), "[TRY]", str(count), "[DONE]", "Not yet", "Status code", str(status.status_code))
                response_codes = response_codes + "," + str(status.status_code)
                
        if not success:
            sendmail("OOI WARNING: After 5 status requests, data request was still incomplete: " + reference_name_dash + "-" + run_name,
                    "Status URL [" + status_url + "] got response codes " + response_codes)
            print("[FAILED]! COUNTS =", count, "Data request still incomplete after 5 status requests. Skipping this time interval", "Response codes:", response_codes)
            with open(os.path.join(run_path, endtime_file_name), "w") as output:
                output.write(str(UTCDateTime(start_time) + float(run["time_interval"][0])))
            print(endtime_file_name, "set =", str(UTCDateTime(start_time) + float(run["time_interval"][0])))
            if gap_enabled:
                with open(os.path.join(log_path, gap_file_name), "a") as output:
                    output.write("%s %s %s\n" % ("Request incomplete after 5 status requests. Skipping this time interval:",
                        str(UTCDateTime(start_time)),
                        str(UTCDateTime(start_time) + float(run["time_interval"][0]))))
            append_diag_log("no_data", reference_name_dash, run_name,
                            f"POLL_TIMEOUT (codes={response_codes}): "
                            f"{str(UTCDateTime(start_time))} → "
                            f"{str(UTCDateTime(start_time) + float(run['time_interval'][0]))}")
            sys.exit()
            
        # Read the netCDF location from NCML
        print("Reading netCDF file.")
        complete = []
        # [CHATGPT FIX #3] Safely parse JSON from status response
        try:
            data_tag = status.json()
        except Exception as e:
            raise RuntimeError(f"Invalid JSON in status response: {e}")
        for key in data_tag.keys():
            if isinstance(data_tag[key], str):
                complete = data_tag[key]

        if complete == "complete":
            if 'prest' in run_name:
                ncml_url = "deployment%04i_%s-streamed-%s_real_time.ncml" % (deployment_id, reference_name_dash, run_name)
            elif 'lily' in run_name or 'nano' in run_name:
                ncml_url = "deployment%04i_%s-streamed-botpt_%s_sample.ncml" % (deployment_id, reference_name_dash, run_name)
            ncml_url = "/".join([response_url, ncml_url])
            print(ncml_url)
            ncml = urllib.request.urlopen(ncml_url)
            tree = ET.ElementTree(file=ncml)
            ncml.close()
            root = tree.getroot()
            
            # Build OPeNDAP URL (for reading) and HTTP URL (for saving exact file)
            # [CHATGPT FIX #1] Initialize netCDF to avoid UnboundLocalError if not found
            netCDF = None
            netcdf_url = None
            for child in root:
                if "aggregation" in child.tag:
                    for element in child:
                        netCDF = element.get("location").strip()
                        print(netCDF)
                        netcdf_url = "/".join([response_url, netCDF])
            # [CHATGPT FIX #1] Ensure netCDF was found in NCML
            if netCDF is None:
                raise RuntimeError("No NetCDF location found in NCML aggregation")

            netcdf_url = netcdf_url.replace(run["http_server"][0], run["opendap_server"][0])
            netcdf_http_url = ("/".join([response_url, netCDF]).replace(run["opendap_server"][0], run["http_server"][0]))

            # --- Optional: write NetCDF to local folder when enabled ---
            if save_netcdf:
                try:
                    local_nc_name = os.path.basename(netCDF)  # preserve server filename
                    local_nc_path = os.path.join(netcdf_path, local_nc_name)

                    print(f"Downloading NetCDF to: {local_nc_path}")
                    with requests.get(netcdf_http_url, stream=True) as r:
                        r.raise_for_status()
                        with open(local_nc_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
                                if chunk:
                                    f.write(chunk)
                    print(f"Saved NetCDF: {local_nc_path}")
                except Exception as e:
                    print("Warning: Failed to save NetCDF locally:", str(e))
                    # Continue; OPeNDAP reading below still proceeds
            
            # Open NetCDF file
            try:
                fh = Dataset(netcdf_url)
                t = fh.variables[str(run["data_time"][0])][:]
                print('Number of data points in NetCDF file:', len(t))
                print("Successfully opened NetCDF file.")
            except Exception as e:
                print("Failed to open NetCDF file:", netcdf_url)
                print("Error:", str(e))

                # On failure before any write, we use the requested end_time as the next start.
                next_start_time = UTCDateTime(end_time)

                # Persist for the next run so we don’t repeat this bad window
                with open(os.path.join(run_path, endtime_file_name), "w") as output:
                    output.write(str(next_start_time))
                print(endtime_file_name, "set =", next_start_time)

                # Optional: record the gap for traceability
                if gap_enabled:
                    with open(os.path.join(log_path, gap_file_name), "a") as f:
                        f.write(f"NETCDF OPEN FAIL: skipping {start_time} → {end_time}\n")

                # Notify operator about the failed open so it won’t go unnoticed
                try:
                    subj = f"OOI WARNING: NetCDF open failed (OPeNDAP/DAP) {reference_name_dash}-{run_name}"
                    body = (
                        "A NetCDF file could not be opened via OPeNDAP (DAP error).\n\n"
                        f"Reference/Run: {reference_name_dash}-{run_name}\n"
                        f"Requested window: {start_time} → {end_time}\n"
                        f"OPeNDAP URL: {netcdf_url}\n"
                        f"Exception: {str(e)}\n\n"
                        f"Action taken: skipped this interval and advanced endtime to {str(next_start_time)}.\n"
                    )
                    sendmail(subj, body)
                except Exception as email_err:
                    print("Warning: failed to send email notification:", str(email_err))

                sys.exit()
            
            # Convert times to UTCDateTime
            utc_t = [UTCDateTime(str(utcdata1900(float(x)))) for x in t]
            requested_start = UTCDateTime(start_time)
            requested_end   = UTCDateTime(end_time)
            data_start = utc_t[0]
            data_end   = utc_t[-1]
            
            print('ACTUAL START TIME:', data_start)
            print('ACTUAL END TIME:',   data_end)

            # Always compute indices so start_idx is defined
            start_idx = np.searchsorted(utc_t, requested_start, "left")
            end_idx   = np.searchsorted(utc_t, requested_end,   "right")
            new_t   = t[start_idx:end_idx]
            new_utc = utc_t[start_idx:end_idx]

            # Will hold the latest (rightmost) timestamp we actually write to MiniSEED
            last_written_time = None
                
            # ──────────────
            # GAP DETECTION
            # ──────────────
            
            # GAP DETECTION OVERVIEW
            #
            # This section identifies missing data ("gaps") in a time series using
            # only the timestamps returned by the data system.
            #
            # Why this is non-trivial:
            #   - Real-world data are rarely perfectly evenly sampled
            #   - Clock jitter, ingestion delays, and partial windows are common
            #   - Naive gap detection (e.g., fixed Δt threshold) produces many false positives
            #
            # High-level strategy:
            #   1) Ensure there is enough data to reason about timing
            #   2) Estimate the *actual* sampling period from the timestamps
            #   3) Decide whether the window appears complete overall
            #   4) Detect true discontinuities using an adaptive threshold
            #
            # Nominal (configured) sample rates are NOT used for detection.
            # They are consulted only for alerting and operator awareness.
    
            npts = len(new_t)
            
            if npts < 2:
                
                print("Too few points to analyze gaps (npts < 2)")
                
                # With fewer than two samples, we cannot compute time differences (Δt),
                # which makes gap detection impossible.
                #
                # Common causes:
                #   - Instrument offline
                #   - Data ingestion delay
                #   - Request window overlaps deployment boundary
                #
                # Strategy:
                #   - Do not attempt gap detection
                #   - Advance the request window so we do not repeatedly request
                #     the same empty or near-empty interval
                
                # --- Fallback when npts < 2: advance and persist next start time ---
                requested_start = UTCDateTime(start_time)
                requested_end   = UTCDateTime(end_time)

                # Option 1: simplest – jump to the requested end of this window
                next_start_time = requested_end

                # Persist for the next run so we don't re-request the same window
                with open(os.path.join(run_path, endtime_file_name), "w") as output:
                    output.write(str(next_start_time))
                print(endtime_file_name, "set =", next_start_time)

                # Optional: record the condition as a gap for traceability
                if gap_enabled:
                    with open(os.path.join(log_path, gap_file_name), "a") as f:
                        f.write(f"TOO_FEW_POINTS: npts={npts} {requested_start} -> {requested_end}\n")

                append_diag_log("too_few_points", reference_name_dash, run_name,
                                f"npts={npts}  window={requested_start} → {requested_end}")

                # Optional: notify operator (comment out if spammy)
                sendmail(f"OOI WARNING: Too few points (npts={npts}) {reference_name_dash}-{run_name}",
                         f"Requested window: {requested_start} -> {requested_end}\nURL: {url}")

                # Stop here cleanly; next cron/run will start from the advanced time
                sys.exit()
                
            else:
                
                # Step 1: Set reference time (first sample) and compute relative times

                # Convert absolute timestamps to seconds relative to the first sample.
                #
                # Why:
                #   - Absolute UTC timestamps are inconvenient for numerical analysis
                #   - Relative seconds simplify Δt computation, histograms, and statistics
                #
                # After this:
                #   t_sec[i] = seconds since first timestamp in this window
                
                t0 = new_utc[0]  # first timestamp as UTCDateTime
                t_sec = np.array([(t - t0) for t in new_utc], dtype=float)
                
                # t_sec is now seconds since the first sample – much easier for math
                # dtype=float ensures numpy treats it as floating-point numbers
                       
                # ── Step 2: gap detection via swappable algorithm ─────────────────
                # Algorithm choice from run_prest.txt: legacy | anomaly. The
                # selected detector returns a GapResult with sp, sr, gap_idx,
                # segment_splits, is_full, n_gaps, n_segments, plus per-algo
                # diagnostics. See bin/gap_algorithms.py.
                gap_algo = run.get("gap_algo", ["legacy"])[0].strip().lower()
                gap_algo_requested = gap_algo                # for reporting if we fall back
                boundary_in_window = False                    # set True if deployment changes mid-window

                # Look up sp_nominal from per-channel param file (used by both
                # the alert check and the algorithm dispatch).
                sp_nominal__alert = None
                try:
                    net_sta_param__alert = read_param(
                        os.path.join(param_path, reference_name_underscore + ".txt"))
                    dep__alert = deployment_id if transfer_method == 'seedlink' else int(run["deployment"][0])
                    if f"channels_{dep__alert}" in net_sta_param__alert:
                        channel_ls_raw__alert = net_sta_param__alert[f"channels_{dep__alert}"][0]
                    else:
                        channel_ls_raw__alert = net_sta_param__alert["channels"][0]
                    first_channel__alert = channel_ls_raw__alert[1:-1].split(",")[0].strip()
                    channel_param_first__alert = read_param(
                        os.path.join(param_path, f"{reference_name_underscore}_{first_channel__alert}.txt"))
                    if "c_sample_rate" in channel_param_first__alert:
                        sr_nominal__alert = float(channel_param_first__alert["c_sample_rate"][0])
                        if sr_nominal__alert > 0:
                            sp_nominal__alert = 1.0 / sr_nominal__alert
                    diag_sp_nominal = sp_nominal__alert

                    # Deployment-boundary check: if this deployment's c_end
                    # falls inside the requested window, the data crosses a
                    # rate change. Anomaly OLS on mixed-rate data produces
                    # garbage Δt_true, so fall back to legacy with a flag.
                    c_end_raw__alert = channel_param_first__alert.get("c_end", [None])[0]
                    if c_end_raw__alert and str(c_end_raw__alert).strip().lower() not in ("none", "null", ""):
                        try:
                            c_end__alert = UTCDateTime(str(c_end_raw__alert).strip())
                            if UTCDateTime(start_time) < c_end__alert < UTCDateTime(end_time):
                                boundary_in_window = True
                                print(
                                    f"WARNING: deployment boundary at {c_end__alert} "
                                    f"falls inside window {start_time} → {end_time}"
                                )
                                if gap_algo == "anomaly":
                                    print("  → falling back to legacy for this window "
                                          "(anomaly OLS unsafe on mixed-rate data)")
                                    gap_algo = "legacy"
                        except Exception as e_boundary:
                            print(f"(deployment boundary check) parse failed (non-fatal): {e_boundary}")
                except Exception as e_nom:
                    print(f"(sp_nominal lookup) failed (non-fatal): {e_nom}")

                # Dispatch detection
                req_start = UTCDateTime(start_time)
                req_end   = UTCDateTime(end_time)
                req_duration = float(req_end - req_start)
                gap_result = detect_gaps(
                    gap_algo, t_sec,
                    sp_nominal=sp_nominal__alert if sp_nominal__alert is not None else 0.0,
                    req_duration=req_duration,
                )

                sp            = gap_result.sp
                sr            = gap_result.sr
                gap_idx       = gap_result.gap_idx
                split_idx     = list(gap_result.segment_splits)
                is_full       = gap_result.is_full
                dt_all        = gap_result.diagnostics.get("dt_all", np.diff(t_sec))
                expected_npts = gap_result.diagnostics.get("expected_npts")
                multiplier    = gap_result.diagnostics.get("multiplier")
                gap_threshold = gap_result.diagnostics.get("gap_threshold")

                print(f"[{gap_algo}] sp={sp:.9f}s  sr={sr:.9f}Hz  "
                      f"n_points={npts}  n_gaps={gap_result.n_gaps}  "
                      f"n_segments={gap_result.n_segments}  is_full={is_full}")
                if gap_algo == "anomaly":
                    print(
                        f"[anomaly] dt_true={gap_result.diagnostics.get('dt_true')}  "
                        f"n_ideal={gap_result.diagnostics.get('n_ideal')}  "
                        f"true_missing={gap_result.diagnostics.get('true_missing')}  "
                        f"jitter_unstable={gap_result.diagnostics.get('jitter_unstable')}  "
                        f"frac_maxabs={gap_result.diagnostics.get('frac_maxabs'):.4f}"
                    )

                # ── sp_deviation email alert (algorithm-agnostic) ────────────────
                try:
                    abs_floor = float(run.get("sp_alert_abs_floor", [0.05])[0])
                    rel_frac  = float(run.get("sp_alert_rel_frac",  [0.05])[0])

                    if sp_nominal__alert and sp and np.isfinite(sp) and sp > 0:
                        sp_dev = abs(sp - sp_nominal__alert)
                        metrics_sp_dev = sp_dev
                        thr = max(abs_floor, rel_frac * sp_nominal__alert)

                        print(
                            f"(sample period alert check) sp_calc={sp:.6f}s, "
                            f"sp_nominal={sp_nominal__alert:.6f}s, |Δ|={sp_dev:.6f}s, "
                            f"thr=max({abs_floor:.3f}s, {rel_frac:.3f}×{sp_nominal__alert:.3f}s={rel_frac*sp_nominal__alert:.3f}s)={thr:.3f}s"
                        )

                        if sp_dev >= thr:
                            subj = (
                                f"OOI NOTICE: Sample period deviation {sp_dev:.3f}s "
                                f"(calc={sp:.3f}s, nominal={sp_nominal__alert:.3f}s) "
                                f"{reference_name_dash}-{run_name}"
                            )
                            body = (
                                f"Reference/Run: {reference_name_dash}-{run_name}\n"
                                f"Requested window: {start_time} → {end_time}\n"
                                f"Algorithm: {gap_algo}\n"
                                f"Calculated period: {sp:.6f} s\n"
                                f"Nominal period (deployment-aware): {sp_nominal__alert:.6f} s\n"
                                f"Absolute deviation: {sp_dev:.6f} s\n"
                                f"Hybrid alert threshold: {thr:.6f} s "
                                f"(abs_floor={abs_floor:.3f}s, rel_frac={rel_frac:.3f})\n"
                                f"Data URL: {url}\n"
                            )
                            try:
                                sendmail(subj, body)
                                print(f"(sample period alert) email sent: Δ={sp_dev:.3f}s ≥ {thr:.3f}s")
                                diag_sp_alert_fired = True
                            except Exception as e_alert:
                                print(f"(sample period alert) email send failed: {e_alert}")
                            append_diag_log("sp_deviation", reference_name_dash, run_name,
                                            f"sp_calc={sp:.6f}s  sp_nom={sp_nominal__alert:.6f}s  "
                                            f"dev={sp_dev:.6f}s  thr={thr:.6f}s  "
                                            f"algo={gap_algo}  window={start_time}")
                    else:
                        print("(sample period alert) nominal or effective period unavailable; alert check skipped")
                except Exception as e_alert_block:
                    print(f"(sample period alert) unexpected error during alert check: {e_alert_block}")

                # ── Gap / missing-data logging (operator narrative) ──────────────
                if len(gap_idx) == 0:
                    if not is_full:
                        if gap_enabled:
                            with open(os.path.join(log_path, gap_file_name), "a") as f:
                                f.write(
                                    f"MISSING DATA: actual start={data_start}, "
                                    f"actual end={data_end}, got {npts}, "
                                    f"expected ~{expected_npts if expected_npts is not None else 'n/a'}\n"
                                )
                        append_diag_log("gaps", reference_name_dash, run_name,
                                        f"MISSING_DATA[{gap_algo}]: got={npts} "
                                        f"expected={expected_npts if expected_npts is not None else 'n/a'} "
                                        f"start={data_start} end={data_end}")
                else:
                    print(f"Found {len(gap_idx)} gaps under {gap_algo} algorithm")
                    for gi in gap_idx:
                        gi = int(gi)
                        delta = float(dt_all[gi])
                        prev_ts = new_utc[gi]
                        next_ts = new_utc[gi + 1]
                        missing_est = round(delta / sp) - 1 if sp > 0 else 0
                        print(f"  gap: {prev_ts} → {next_ts}  Δt={delta:.2f}s  "
                              f"≈{missing_est} missing")
                        if gap_enabled:
                            with open(os.path.join(log_path, gap_file_name), "a") as f:
                                thr_str = f"threshold={gap_threshold:.2f}s, " if gap_threshold else ""
                                f.write(
                                    f"GAP[{gap_algo}]: {prev_ts} {next_ts} Δt={delta:.2f}s "
                                    f"({thr_str}~{missing_est} missing)\n"
                                )
                        append_diag_log("gaps", reference_name_dash, run_name,
                                        f"GAP[{gap_algo}]: {prev_ts} → {next_ts}  "
                                        f"dt={delta:.2f}s  ~{missing_est} missing")

                # Convert UTCDateTime to strings for safe numpy handling
                new_utc_str = [str(x) for x in new_utc]
                new_utc_arr = np.array(new_utc_str, dtype=object)
                # Split into list of arrays, each a continuous segment of timestamps
                data_split = np.split(new_utc_arr, split_idx)
                # Result: data_split[i] = timestamps for segment i (as object array of strings)
                # Number of segments = len(split_idx) + 1

            net_sta_param = read_param(os.path.join(param_path, reference_name_underscore + ".txt")) # Network/station param file
            
            # Get deployment number for selecting correct channel list from net_sta_param file.
            # For seedlink, use deployment_id from the API (most recent deployment).
            # For miniseed2dmc, use the deployment number from the run param file.
            if transfer_method == 'seedlink':
                dep = deployment_id
            else:
                dep = int(run["deployment"][0])

            # Get deployment specific channel list from net_sta_param file
            if f"channels_{dep}" in net_sta_param:
                channel_ls_raw = net_sta_param[f"channels_{dep}"][0]
            else:
                channel_ls_raw = net_sta_param["channels"][0]

            channel_ls = channel_ls_raw[1:-1].split(",")
            print(channel_ls)

            # ── Per-day pipeline stats CSV row ─────────────────────────────
            # Append once per (date, station, run). Idempotent — re-runs of
            # the same window (e.g. after a transient failure) skip rewrite.
            try:
                metrics_date_str = str(UTCDateTime(start_time))[:10]
                if not metrics_row_exists(reference_name_dash, run_name, metrics_date_str):
                    diag = gap_result.diagnostics
                    # Algorithm-agnostic missing-sample estimate (anomaly: true_missing
                    # is exact; legacy: sum of round(Δt/sp)−1 over gap intervals)
                    if "true_missing" in diag:
                        gap_total_missing_est = int(diag["true_missing"])
                    else:
                        gap_total_missing_est = int(diag.get("gap_total_missing_est", 0))

                    append_metrics_row(reference_name_dash, run_name, {
                        "date":                     metrics_date_str,
                        "station":                  reference_name_dash,
                        "run":                      run_name,
                        "deployment":               dep,
                        "algorithm":                gap_result.diagnostics.get("algorithm", gap_algo),
                        "algorithm_requested":      gap_algo_requested,
                        "boundary_in_window":       bool(boundary_in_window),
                        "n_points":                 int(npts),
                        "expected_npts":            int(expected_npts) if expected_npts is not None else "",
                        "is_full":                  bool(is_full),
                        "sp":                       round(float(sp), 9) if sp and np.isfinite(sp) else "",
                        "sr":                       round(float(sr), 9) if sr else "",
                        "sp_nominal":               round(float(diag_sp_nominal), 6) if diag_sp_nominal else "",
                        "sp_deviation":             round(float(metrics_sp_dev), 9) if metrics_sp_dev is not None else "",
                        "sp_deviation_alert_fired": bool(diag_sp_alert_fired),
                        # legacy-only
                        "multiplier":               round(float(multiplier), 3) if multiplier is not None else "",
                        "gap_threshold":            round(float(gap_threshold), 6) if gap_threshold is not None else "",
                        # anomaly-only
                        "dt_true":                  repr(diag["dt_true"]) if "dt_true" in diag else "",
                        "n_ideal":                  int(diag["n_ideal"]) if "n_ideal" in diag else "",
                        "true_missing":             int(diag["true_missing"]) if "true_missing" in diag else "",
                        "n_gaps_raw":               int(diag["n_gaps_raw"]) if "n_gaps_raw" in diag else "",
                        "jitter_unstable":          bool(diag["jitter_unstable"]) if "jitter_unstable" in diag else "",
                        "frac_maxabs":              round(float(diag["frac_maxabs"]), 6) if "frac_maxabs" in diag else "",
                        # shared
                        "n_gaps":                   int(gap_result.n_gaps),
                        "n_segments":               int(gap_result.n_segments),
                        "gap_total_missing_est":    gap_total_missing_est,
                    })
            except Exception as metrics_err:
                print(f"Warning: per-day stats CSV write failed (non-fatal): {metrics_err}")
            
            if f"data_types_{dep}" in net_sta_param:
                datatype_raw = net_sta_param[f"data_types_{dep}"][0]
            else:
                datatype_raw = net_sta_param["data_types"][0]

            # Get deployment specific data type list from net_sta_param file
            datatype_ls = datatype_raw if isinstance(datatype_raw, dict) else ast.literal_eval(datatype_raw)
            print(datatype_ls)
            
            for k in range(len(channel_ls)):
                
                # Converts datatype_ls to a dictionary, looks up name based on the channel. E.g., if channel_ls[k] == 'LDO_01', then name == 'absolute_pressure'. 
                name = datatype_ls[channel_ls[k]]
                data_point = fh.variables[name][:]
                
                l = start_idx 
                for s in range(len(data_split)):

                    ti = data_split[s]
                    end_time_splt = ti[-1]
                    start_time_splt = ti[0]

                    data_i_point = data_point[l : l+ len(ti)] # Grab the data from the netCDF file within the data_split window
                    data = data_i_point

                    # Masked array writing is not supported for miniSEED. Converting to a normal array, MT
                    if isinstance(data_i_point, MaskedArray):
                        data_i_point = data_i_point.filled(np.nan)

                    # Fill header attributes
                    channel_param = read_param(
                        os.path.join(
                            param_path,
                            reference_name_underscore + "_" + channel_ls[k] + ".txt",
                        )
                    )
                    
                    # Compare deployment end date to end_time. If it is within a specified number of days, send a notification email.
                    # Read c_end from the currently loaded channel_param
                    c_end_raw = channel_param.get("c_end", [None])[0]

                    # Proceed only if c_end is explicitly set (not None/null/empty)
                    if c_end_raw and str(c_end_raw).strip().lower() not in ("none", "null", ""):
                        c_end = UTCDateTime(c_end_raw)
                        req_end = UTCDateTime(end_time)   # <-- compare to end_time now

                        # Threshold (days) – optionally set in run file: deploy_warn_days = <int>
                        warn_days = int(run.get("deploy_warn_days", [3])[0])
                        warn_threshold_sec = warn_days * 86400

                        time_left_sec = c_end - req_end  # positive if we're before c_end

                        if 0 < time_left_sec <= warn_threshold_sec:
                            # Approaching end (within threshold but not past)
                            days_left = time_left_sec / 86400.0
                            subj = f"OOI NOTICE: Deployment ending soon for {reference_name_dash}-{run_name}"
                            body = (
                                f"Requested end_time: {req_end}\n"
                                f"Deployment end (c_end): {c_end}\n"
                                f"Days remaining: {days_left:.2f}\n"
                                f"Threshold: {warn_days} days\n"
                            )
                            try:
                                sendmail(subj, body)
                            except Exception as err:
                                print(f"Warning: failed to send 'ending soon' email: {err}")

                        elif time_left_sec <= 0:
                            # At or past end
                            subj = f"OOI NOTICE: Deployment end reached for {reference_name_dash}-{run_name}"
                            body = (
                                f"Requested end_time: {req_end}\n"
                                f"Deployment end (c_end): {c_end}\n"
                            )
                            try:
                                sendmail(subj, body)
                            except Exception as err:
                                print(f"Warning: failed to send 'end reached' email: {err}")
                    # If c_end is not set, nothing to warn about for this channel.
                    
                    print(f"Expected npts from calculated daily sample period (median Δt) is {expected_npts}")
                    sr_nominal = float(channel_param["c_sample_rate"][0])
                    sp_nominal = 1 / sr_nominal
                    expected_npts_nominal = round(req_duration / sp_nominal) + 1 
                    print(f"Expected npts from nominal sample period (for comparison) is {expected_npts_nominal}")

                    print("Filling miniSEED header attributes.")  

                    # Note: Daily calculated sample rate, sr, is included in the miniSEED header. Not the nominal sample rate, sr_nominal.
                    stats = {
                        "network": net_sta_param["net"][0],
                        "station": net_sta_param["sta"][0],
                        "location": channel_param["c_loc"][0],
                        "channel": channel_param["cha"][0],
                        "npts": len(data_i_point),
                        "sampling_rate": sr,
                        "mseed": {"dataquality": run["data_quality"][0]},
                    }
                    
                    # Unit conversion to desired output units
                    data_i_point = data_i_point / float(channel_param["r_value"][0])                   

                    # Set current time
                    # [CHATGPT FIX #4] Ensure ObsPy receives UTCDateTime, not string
                    stats["starttime"] = UTCDateTime(start_time_splt) # start time of the data_split segment
                    st = Stream([Trace(data=data_i_point, header=stats)])
                    encoding_name = str(st[0].data.dtype).upper()
                        
                    mseed_name = (
                        net_sta_param["net"][0]
                        + "."
                        + net_sta_param["sta"][0]
                        + "."
                        + channel_param["c_loc"][0]
                        + "."
                        + channel_param["cha"][0]
                        + "."
                        + start_time_splt[0:4]
                        + "."
                        + "{:0>3}".format(str(time.strptime(start_time_splt[0:10], "%Y-%m-%d").tm_yday))
                        + "."
                        + start_time_splt[11:23].replace(":", ".")
                        + "-"
                        + end_time_splt[0:4]
                        + "."
                        + "{:0>3}".format(str(time.strptime(end_time_splt[0:10], "%Y-%m-%d").tm_yday))
                        + "."
                        + end_time_splt[11:23].replace(":", ".")
                        + mseed_file_ext
                    )

                    # Choose write directory based on transfer_method and start year
                    if transfer_method == "miniseed2dmc":
                        year_dir = os.path.join(mseed_path, start_time_splt[0:4])  # e.g., output/mseed2dmc/2026
                        os.makedirs(year_dir, exist_ok=True)
                        write_path = os.path.join(year_dir, mseed_name)
                    else:
                        write_path = os.path.join(mseed_path, mseed_name)

                    print("Writing miniSEED file " + mseed_name)
                    
                    st.write(
                        write_path,               
                        format="MSEED",
                        encoding=encoding_name,
                        reclen=int(run["rec_len"][0]),
                    )
                    
                    last_written_time = UTCDateTime(end_time_splt)

                    l = l + len(ti)
                
            # Determine next request start time.
            # Each day stands alone: the next pickup is always the upper bound
            # of the current request window, NOT last_written + sp. This keeps
            # the cron's day-boundary arithmetic independent of any per-day
            # rate estimate (legacy median or OLS Δt_true). Per-day Δt_true
            # describes spacing WITHIN a day; we trust OOI to deliver the next
            # day's first sample without arithmetic prediction. Partial-data
            # windows therefore leave permanent gaps (consistent with the
            # "gaps are honest" design principle — we record and move on
            # rather than re-fetching from where partial data ended).
            next_start_time = UTCDateTime(end_time)

            print(f"[CONTINUITY] last_written={last_written_time}  "
                  f"→ next_start={next_start_time}  (= request end_time)")

            # Persist the next start time for the following run
            with open(os.path.join(run_path, endtime_file_name), "w") as output:
                output.write(str(next_start_time))
            print(endtime_file_name, "set =", next_start_time)
                        
    # If the end_time is more recent than trunc_time in the run param file, skip the request. trunc_time is currently set to 0. MT
    elif UTCDateTime(end_time) > UTCDateTime(trunc_time):

        print(
            "SKIP request: end time ["
            + UTCDateTime(end_time).strftime("%Y-%m-%d %H:%M:%S")
            + "] greater than truncation time ["
            + UTCDateTime(trunc_time).strftime("%Y-%m-%d %H:%M:%S")
            + "] so no data requested."
        )

        sys.exit()

finally:

    sys.stdout = old_stdout
    sys.stderr = old_stderr
    log_file.close()

