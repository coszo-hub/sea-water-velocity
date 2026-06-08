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
from numpy.ma import MaskedArray
from convert_utc import *
from read_param import *
from mail import *

# Send test email
# sendmail('TEST EMAIL SEND FROM COSZO')
# sys.exit()

# 2026-01-07 Mika: This is a modified version of OOI_data_bundled.py that calculates a new sample period per data request. 

# Get args
if len(sys.argv) != 4:
    print("Need to pass 'reference_name' (with dashes not underscores), 'run_name', and 'transfer_method' (seedlink or miniseed2dmc) arguments")
    sys.exit()
    
reference_name_dash = sys.argv[1] # Used for logging, retrieving end time of previous data request, alert emails
run_name = sys.argv[2] # Retrieve the run parameter file name
transfer_method = sys.argv[3] # seedlink or miniseed2dmc

if transfer_method not in ['seedlink', 'miniseed2dmc']:
    print(f"Invalid transfer_method: {transfer_method}. Must be 'seedlink' or 'miniseed2dmc'.")
    sys.exit()
    
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

sys.path.insert(0, bin_path)

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

    # Optional flag to write a local copy of the returned NetCDF
    save_netcdf = int(run.get("save_netcdf", [0])[0]) == 1

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
        if int(run["deployment"][0]) == -1:
            deployment = int(run["deployment"][0]) # Most recent deployment
        else:
            deployment = int(run["deployment"][0]) - 1 # Minus one for Python indexing
        print("deployment number =", deployment)
        if transfer_method == 'seedlink':
            deployment_id = response.json()[-1] # Most recent deployment, MT
        elif transfer_method == 'miniseed2dmc':
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
            
            npts = len(new_t)
            
            if npts < 2:
                
                print("Too few points to analyze gaps (npts < 2)")
                
                # --- Fallback when npts < 2: advance and persist next start time ---
                requested_start = UTCDateTime(start_time)
                requested_end   = UTCDateTime(end_time)

                # Option 1: simplest – jump to the requested end of this window
                next_start_time = requested_end

                # (Optional) If you prefer to inch forward when exactly 1 point is present,
                # you can instead try to advance by a nominal sample period:
                # if npts == 1:
                #     # If you know the nominal rate (e.g., from channel_param['c_sample_rate']),
                #     # use it; otherwise keep the requested_end fallback.
                #     try:
                #         net_sta_param = read_param(os.path.join(param_path, reference_name_underscore + ".txt"))
                #         first_channel = net_sta_param["channel"][0][1:-1].split(",")[0]
                #         channel_param = read_param(os.path.join(param_path, reference_name_underscore + "_" + first_channel + ".txt"))
                #         sr_nominal = float(channel_param["c_sample_rate"][0])
                #         sp_nominal = 1.0 / sr_nominal if sr_nominal > 0 else None
                #         if sp_nominal:
                #             # new_utc[0] exists because npts == 1
                #             next_start_time = UTCDateTime(str(utcdata1900(float(new_t[0])))) + sp_nominal
                #     except Exception:
                #         pass  # fall back to requested_end

                # Persist for the next run so we don't re-request the same window
                with open(os.path.join(run_path, endtime_file_name), "w") as output:
                    output.write(str(next_start_time))
                print(endtime_file_name, "set =", next_start_time)

                # Optional: record the condition as a gap for traceability
                if gap_enabled:
                    with open(os.path.join(log_path, gap_file_name), "a") as f:
                        f.write(f"TOO_FEW_POINTS: npts={npts} {requested_start} -> {requested_end}\n")

                # Optional: notify operator (comment out if spammy)
                sendmail(f"OOI WARNING: Too few points (npts={npts}) {reference_name_dash}-{run_name}",
                         f"Requested window: {requested_start} -> {requested_end}\nURL: {url}")

                # Stop here cleanly; next cron/run will start from the advanced time
                sys.exit()
                
            else:
                
                # Step 1: Set reference time (first sample) and compute relative times
                t0 = new_utc[0]  # first timestamp as UTCDateTime
                t_sec = np.array([(t - t0) for t in new_utc], dtype=float)
                # t_sec is now seconds since the first sample – much easier for math
                # dtype=float ensures numpy treats it as floating-point numbers
        
                # # Step 2: Calculate the daily sample period (median period) and sample rate    
                # # We use median period because it's less affected by outliers than the mean.      
                # dt_all = np.diff(t_sec)  # all consecutive differences
                # sp = np.median(dt_all) # daily sample rate
                # sr = 1.0 / sp # daily sample rate
                # print(f"Calculated daily sample period (median Δt) = {sp:8.6f} s, calculated daily sample rate = {sr:8.6f} Hz")
                
                # =========================================================================================
                # Step 2: ROBUST, CALCULATED-ONLY SAMPLE PERIOD + DEPLOYMENT-AWARE ALERT
                #
                # Why this block exists:
                #   - On sparse/thinned days, the plain median(Δt) can be pulled toward short intervals
                #     and make normal ~15 s steps look like “gaps”. This block estimates the period
                #     from the shape of the Δt distribution (mode around the densest region) so the
                #     cadence survives missing stretches—using timestamps only (no nominal for detection).
                #
                # What it does:
                #   1) Build Δt (seconds between consecutive timestamps) and drop non-positive / non-finite.
                #   2) Keep the central mass with two lightweight, robust filters:
                #        • IQR filter (Q1–1.5·IQR to Q3+1.5·IQR)
                #        • Percentile trim (5th–95th)
                #   3) Find the histogram peak and take the median of Δt values near that peak (±10% band).
                #      That’s our robust, calculated period (sp_eff). Rate sr_eff = 1/sp_eff.
                #   4) Alias sp_eff/sr_eff to your original names sp/sr so the rest of the script “just works”.
                #   5) Send an email alert *only* if |sp - nominal| ≥ threshold (deployment-aware nominal).
                #
                # Notes:
                #   - Gap detection, expected counts, continuity, and MiniSEED headers now use sp/sr from
                #     this robust estimator (still fully calculated; nominal is used *only* for alerting).
                # =========================================================================================

                # --- 1) Build Δt from timestamps (in seconds) ---
                dt_all = np.diff(t_sec)  # consecutive time differences (float seconds since first sample)

                # Keep only usable intervals (no zeros/negatives; drop NaN/inf)
                dt = np.asarray(dt_all, dtype=float)
                dt = dt[np.isfinite(dt)]
                dt = dt[dt > 0.0]

                if dt.size == 0:
                    # If we can’t form any sensible Δt, skip robust estimation;
                    # set placeholders so downstream code won’t crash.
                    sp_eff = float('nan')
                    sr_eff = 0.0
                    print("Δt set is empty after filtering → skipping robust period estimation for this window")
                else:
                    # --- 2) Light-touch robustness: keep the “middle” Δt values ---

                    # 2a) IQR filter: trims extreme tails while preserving the central distribution
                    q1, q3 = np.percentile(dt, [25, 75])
                    iqr = q3 - q1
                    lo_iqr = max(0.0, q1 - 1.5 * iqr)     # forbid negative lower bound
                    hi_iqr = q3 + 1.5 * iqr
                    dt_iqr = dt[(dt >= lo_iqr) & (dt <= hi_iqr)]
                    if dt_iqr.size == 0:
                        dt_iqr = dt  # fallback: if IQR wiped everything, revert to raw positives

                    # 2b) Percentile trim: drop the most extreme 5% on each side (handles lopsided tails)
                    lo_p, hi_p = np.percentile(dt_iqr, [5.0, 95.0])
                    dt_trim = dt_iqr[(dt_iqr >= lo_p) & (dt_iqr <= hi_p)]
                    if dt_trim.size == 0:
                        dt_trim = dt_iqr  # fallback: if trimming removed all, use IQR set

                    # --- 3) Estimate the period from the Δt distribution’s peak ---
                    # Build a modest-bin histogram; pick the peak bin; then take the median of Δt values
                    # within (or near) that bin. Using the median around the mode keeps the estimate stable.
                    bins = max(20, int(np.sqrt(len(dt_trim))))
                    hist, edges = np.histogram(dt_trim, bins=bins)

                    if hist.sum() == 0:
                        # If the histogram is degenerate, fall back to the median of trimmed data
                        sp_calc_robust = float(np.median(dt_trim))
                    else:
                        peak_idx = int(np.argmax(hist))
                        # Detect if there is a clear dominant sampling period
                        total_counts = hist.sum()
                        peak_fraction = hist[peak_idx] / total_counts if total_counts > 0 else 0
                        if peak_fraction < 0.5:
                            vals, counts = np.unique(np.round(dt_trim, 3), return_counts=True)
                            top = np.argsort(counts)[::-1][:3]
                            rate_info = "\nTop sample periods:\n"
                            for i in top:
                                rate_info += f"{vals[i]} sec ({counts[i]} samples)\n"
                            msg = (
                                f"WARNING: Multi-rate data detected\n\n"
                                f"Peak fraction: {peak_fraction:.2f}\n"
                                f"Time range: {start_time} to {end_time}\n\n"
                                f"Likely redeployment or parameter change.\n"
                                f"Recommend rerunning with updated configuration."
                            )
                            msg += rate_info
                            print(msg)
                            sendmail("Multi-rate Data Detected", msg)    
                                                    
                            
                        bin_left, bin_right = edges[peak_idx], edges[peak_idx + 1]

                        # Center of peak bin and a ±10% widening band to include near-peak values
                        center = 0.5 * (bin_left + bin_right)
                        half_w = 0.5 * (bin_right - bin_left)
                        widen  = 0.10 * center                                    # 10% of peak as tolerance
                        lo_win = max(0.0, center - (half_w + widen))               # guard against negatives
                        hi_win = center + (half_w + widen)

                        # Prefer the widened window; if empty, fall back to the bin itself
                        dt_peak = dt_trim[(dt_trim >= lo_win) & (dt_trim <= hi_win)]
                        if dt_peak.size == 0:
                            dt_peak = dt_trim[(dt_trim >= bin_left) & (dt_trim <= bin_right)]

                        if dt_peak.size > 0:
                            sp_calc_robust = float(np.median(dt_peak))
                        else:
                            sp_calc_robust = float(np.median(dt_trim))

                        # Final safety check
                        if not np.isfinite(sp_calc_robust) or sp_calc_robust <= 0:
                            print("WARNING: Invalid sample period from robust estimator → falling back to median(dt_trim)")
                            sp_calc_robust = float(np.median(dt_trim))

                    sr_calc_robust = 1.0 / sp_calc_robust if sp_calc_robust > 0 else 0.0

                    # Keep your original wording so log parsers remain compatible
                    print(f"Calculated daily sample period (median Δt) = {sp_calc_robust:8.6f} s, calculated daily sample rate = {sr_calc_robust:8.6f} Hz")
                    print("(robust Δt estimator applied; calculated-only, no nominal)")

                    sp_eff = sp_calc_robust
                    sr_eff = sr_calc_robust

                # --- 4) Alias to your original variable names so the rest of the script stays unchanged ---
                sp = sp_eff
                sr = sr_eff

                # --- 5) Email alert if calculated period deviates from the deployment’s nominal (hybrid threshold) ---
                #      Nominal is used ONLY for alerting; all detection uses calculated sp/sr above.
                try:
                    # Read the correct channel list for THIS deployment (supports channels_<dep>)
                    net_sta_param__alert = read_param(os.path.join(param_path, reference_name_underscore + ".txt"))
                    dep__alert = int(run["deployment"][0])

                    if f"channels_{dep__alert}" in net_sta_param__alert:
                        channel_ls_raw__alert = net_sta_param__alert[f"channels_{dep__alert}"][0]
                    else:
                        channel_ls_raw__alert = net_sta_param__alert["channels"][0]

                    first_channel__alert = channel_ls_raw__alert[1:-1].split(",")[0].strip()
                    channel_param_first__alert = read_param(
                        os.path.join(param_path, f"{reference_name_underscore}_{first_channel__alert}.txt")
                    )

                    # Look up nominal rate/period for this deployment
                    if "c_sample_rate" in channel_param_first__alert:
                        sr_nominal__alert = float(channel_param_first__alert["c_sample_rate"][0])
                        sp_nominal__alert = 1.0 / sr_nominal__alert if sr_nominal__alert > 0 else None
                    else:
                        sr_nominal__alert = 0.0
                        sp_nominal__alert = None

                    # Hybrid threshold scales well for 15 s AND 1 s deployments:
                    #   thr = max(abs_floor, rel_frac * nominal_period)
                    # Defaults: 50 ms floor and 5% relative; override in run_<name>.txt if desired.
                    abs_floor = float(run.get("sp_alert_abs_floor", [0.05])[0])   # seconds
                    rel_frac  = float(run.get("sp_alert_rel_frac",  [0.05])[0])   # fraction of nominal

                    if sp_nominal__alert and sp and np.isfinite(sp) and sp > 0:
                        sp_dev = abs(sp - sp_nominal__alert)
                        thr    = max(abs_floor, rel_frac * sp_nominal__alert)

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
                                f"Calculated (robust) period: {sp:.6f} s\n"
                                f"Nominal period (deployment-aware): {sp_nominal__alert:.6f} s\n"
                                f"Absolute deviation: {sp_dev:.6f} s\n"
                                f"Hybrid alert threshold: {thr:.6f} s "
                                f"(abs_floor={abs_floor:.3f}s, rel_frac={rel_frac:.3f})\n"
                                f"Data URL: {url}\n"
                            )
                            try:
                                sendmail(subj, body)
                                print(f"(sample period alert) email sent: Δ={sp_dev:.3f}s ≥ {thr:.3f}s")
                            except Exception as e_alert:
                                print(f"(sample period alert) email send failed: {e_alert}")
                    else:
                        print("(sample period alert) nominal or effective period unavailable; alert check skipped")

                except Exception as e_alert_block:
                    print(f"(sample period alert) unexpected error during alert check: {e_alert_block}")
                    
                # Step 3: Estimate how many points we *should* have in this window
                #         and set adaptive gap threshold based on sample period
                req_start = UTCDateTime(start_time)
                req_end = UTCDateTime(end_time)
                req_duration = req_end - req_start
                # Expected number of samples = time span / sample interval + 1
                expected_npts = round(req_duration / sp) + 1 if sp > 0 else npts
                # Allow small tolerance (absolute + relative) for rounding/clock adjustments
                tol = max(5, int(0.001 * expected_npts))  # e.g. ±5 or 0.1%
                is_full = abs(npts - expected_npts) <= tol

                if is_full:
                    print(f"→ Count matches expected (~{expected_npts}) → treating as continuous "
                        f"(only jitter expected)")
                else:
                    print(f"→ Possible MISSING data (got {npts}, expected ~{expected_npts})")
                    
                # Adaptive gap threshold based on sample period 
                # 
                # Goal: Choose a reasonable gap threshold that flags real dropouts while
                #       tolerating normal clock jitter / small timing irregularities.
                # 
                # Logic for selecting the multiplier (× sp):
                # 
                #   - Longer sample periods (e.g. 15 s long-period sensors) tend to have
                #     larger absolute timing jitter or synchronization uncertainty.
                #     → We use HIGHER multipliers (3–4×) → larger absolute thresholds (45–60 s)
                #       to avoid false positives from minor drifts.
                # 
                #   - Shorter sample periods (e.g. 1 s or faster) usually have tighter timing
                #     control and smaller acceptable jitter.
                #     → We use LOWER multipliers (2–2.5×) → smaller thresholds (2–2.5 s)
                #       to remain sensitive to actual short dropouts.
                # 
                #   - When the segment appears COMPLETE (point count matches expected within tolerance):
                #     → Assume only small jitter → use the more tolerant (higher) multiplier.
                # 
                #   - When the segment appears INCOMPLETE (clear missing chunks):
                #     → Be more conservative/sensitive → use the lower multiplier to better
                #       detect the boundaries of large gaps.
                # 
                # Breakpoints chosen for your PREST sensors:
                #   ≥ 10 s   → long-period regime (e.g. 15 s PREST)     → tolerant
                #   ≥ 0.5 s  → mid-range (covers ~1–2 s sampling)      → moderate
                #   < 0.5 s  → high-rate / short-period                 → sensitive
                # 
                # These values are empirical starting points based on:
                #   - Typical OBS/GSN/IRIS-style gap detection practices
                #   - Experience with long-period vs broadband/short-period timing behavior
                #   - Avoiding too many false gaps on slow-sampled data
                # 
                # You can adjust multipliers or breakpoints after inspecting real gap reports.

                if sp >= 10.0:          # Long-period: e.g., 15 s PREST
                    multiplier = 3.0 if not is_full else 4.0   # Threshold: 45–60 s
                elif sp >= 0.5:         # Intermediate: ~1–2 s periods
                    multiplier = 2.5 if not is_full else 3.5   # Threshold: ~2.5–3.5 s
                else:                              # High-rate: <0.5 s (faster than 2 Hz, if any)
                    multiplier = 2.0 if not is_full else 2.5   # Threshold: <1–1.25 s

                gap_threshold = multiplier * sp

                print(f"→ Using gap threshold = {gap_threshold:.2f} s "
                    f"({multiplier:.1f} × median Δt = {sp:.2f} s)")
                
                # Step 4: Identify intervals larger than the chosen threshold
                gap_idx = np.where(dt_all > gap_threshold)[0]  # indices where gap occurs (before gap_idx+1)
                if len(gap_idx) == 0:
                    print("No intervals exceed gap threshold → data appears continuous")
                    
                    # If dataset is not full, but there are not gaps, log in gap file.
                    if not is_full:
                        if gap_enabled:
                            with open(os.path.join(log_path, gap_file_name), "a") as f:
                                f.write(f"MISSING DATA: actual start={data_start}, actual end={data_end}, got {npts}, expected ~{expected_npts}\n")
                                
                else:
                    print(f"Found {len(gap_idx)} candidate gaps above {gap_threshold:.2f} s threshold")
                    for gi in gap_idx:
                        delta = dt_all[gi]
                        prev_ts = new_utc[gi]  # timestamp before gap
                        next_ts = new_utc[gi + 1]  # timestamp after gap
                        missing_est = round(delta / sp) - 1 if sp > 0 else 0
                        print(f"Gap candidate: {prev_ts} → {next_ts} "
                            f"Δt = {delta:7.2f} s ≈ {missing_est} missing samples")
                        
                        if gap_enabled:
                            with open(os.path.join(log_path, gap_file_name), "a") as f:
                                f.write(f"GAP: {prev_ts} {next_ts} Δt={delta:.2f}s "
                                        f"(threshold={gap_threshold:.2f}s, ~{missing_est} missing)\n")
            
                # Split time series into contiguous segments at detected gaps
                # Convert gap locations to split points (before first sample after gap)
                split_idx = (gap_idx + 1).tolist() # gap_idx: indices of last sample BEFORE each gap
                # Convert UTCDateTime to strings for safe numpy handling
                new_utc_str = [str(x) for x in new_utc]
                new_utc_arr = np.array(new_utc_str, dtype=object)
                # Split into list of arrays, each a continuous segment of timestamps
                data_split = np.split(new_utc_arr, split_idx)
                # Result: data_split[i] = timestamps for segment i (as object array of strings)
                # Number of segments = len(gap_idx) + 1

            net_sta_param = read_param(os.path.join(param_path, reference_name_underscore + ".txt")) # Network/station param file
            
            # Get deployment number for selecting correct channel list from net_sta_param file
            dep = int(run["deployment"][0])

            # Get deployment specific channel list from net_sta_param file
            if f"channels_{dep}" in net_sta_param:
                channel_ls_raw = net_sta_param[f"channels_{dep}"][0]
            else:
                channel_ls_raw = net_sta_param["channels"][0]

            channel_ls = channel_ls_raw[1:-1].split(",") 
            print(channel_ls)
            
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
                
            # Determine next request start time 
            if last_written_time is not None:
                if sp and np.isfinite(sp) and sp > 0:
                    next_start_time = last_written_time + sp
                else:
                    print("WARNING: Invalid sp in continuity → using requested end_time")
                    next_start_time = UTCDateTime(end_time)
            else:
                # Rare: nothing written at all; fall back so the pipeline progresses
                next_start_time = UTCDateTime(end_time)

            # Continuity trace (single-line, easy to grep) ---
            print(f"[CONTINUITY] last_written={last_written_time}  +Δt={sp:.6f}s  → next_start={next_start_time}")

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

