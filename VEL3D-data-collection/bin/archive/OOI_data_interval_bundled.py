
#!/Users/mika/anaconda3/envs/ooi_env/bin/python
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
from convertutc import *

### History ###
# 2025-11-24 Mika: OOI_data.py skips data requests if:
# - Length of data is less than 5 samples
# - If the end time of the data request is more recent that 24 hours before the current time
# 2025-10-01 Mika: This message can be ignored: <frozen importlib._bootstrap>:241: RuntimeWarning: numpy.ndarray size changed, may indicate binary incompatibility.
# 2025-09-23 Mika: Reformatted for Python 3.10. MT
# 2017-09-20 Manoch: change start time from file_end_time+0.000001 to +0.000100 to avoid gaps.
# 2017-09-19 Manoch: comments and sampling fixes.
# 2025-12-12 Copilot: strict half-open + tolerance gap detection; improved messages (# MC).
# 2025-12-12 Copilot: Bundled per request window (not only hourly), keep micro-segments (# MC).
# 2025-12-12 Copilot: Duration-aware logs; per-segment sampling rate; per-channel tolerance (# MC).

# Get args
if len(sys.argv) != 3:
    print("Need to pass 'reference_name' (with dashes not underscores) and 'run_name' arguments")
    sys.exit()
reference_name_dash = sys.argv[1]
run_name = sys.argv[2]
reference_name_underscore = reference_name_dash.replace("-", "_")
mseed_file_ext = ".seed"
rootPath = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
outputPath = os.path.join(rootPath, "output")
paramPath = os.path.join(rootPath, "param")
binPath = os.path.join(rootPath, "bin")
runPath = os.path.join(rootPath, "run")
logPath = os.path.join(rootPath, "log")
mseedPath = os.path.join(outputPath, "mseed")
run_file_name = "run_" + run_name + ".txt"
urlDesignator = reference_name_dash.replace("-", "/", 2)
old_stdout = sys.stdout
log_file_name = str(UTCDateTime.now())[0:10] + "-" + reference_name_dash + "-" + run_name + ".log"
log_file = open(os.path.join(logPath, log_file_name), "a")
sys.stdout = log_file
sys.path.insert(0, binPath)
from readparam import *
from mail import *

run = readParam(os.path.join(paramPath, run_file_name))
print("========================== ")
print(str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + " - GET OOI DATA " + reference_name_dash + "-" + run_name)

print("Requesting information from OOI sensor.")  # MT
USERNAME = run["USERNAME"][0]
TOKEN = run["TOKEN"][0]
endtime_file_name = "endtime_" + reference_name_dash + "_" + run_name + ".txt"
file_end_time = UTCDateTime(open(os.path.join(runPath, endtime_file_name), "r").read())

# Start time nudged to avoid M2M boundary effects (per 2017 note). MT
startTime = str(file_end_time + 0.000100)
print("START TIME:", startTime)

# Gap file optional
if int(run.get("gap", [0])[0]) == 1:
    gap_file_name = "gap_" + reference_name_dash + "_" + run_name + ".txt"

# End time = start + interval (supports any duration)
endTime = str(UTCDateTime(startTime) + float(run["time_interval"][0]))
print("END TIME:", endTime)
difTime = UTCDateTime(endTime) - UTCDateTime(startTime)

# Truncation control (Now - trunctime) or fixed data_endtime
if (run["data_endtime"][0] == "NOW" or UTCDateTime(run["data_endtime"][0]) >= UTCDateTime.now()):
    print("truncTime set = now() - trunctime parameter " + str(float(run["trunctime"][0])))
    truncTime = UTCDateTime.now() - float(run["trunctime"][0])
else:
    print("truncTime set = data_endtime")
    truncTime = UTCDateTime(run["data_endtime"][0])

# Proceed only if window end is not too fresh and window length is sufficient
if UTCDateTime(endTime) <= UTCDateTime(truncTime) and difTime >= float(run["diftime"][0]):
    baseUrl = run["baseUrl"][0]
    dataUrl = run["dataUrl"][0]

    # Stream tag
    if 'prest' in run_name:
        streamTag = "streamed/" + run_name + "_real_time?include_provenance=true&format=application/netcdf"
    elif 'lily' in run_name or 'nano' in run_name:
        streamTag = "streamed/botpt_" + run_name + "_sample?include_provenance=true&format=application/netcdf"

    # Deployment info
    url = "/".join([baseUrl, urlDesignator])
    response = requests.get(url, auth=(USERNAME, TOKEN))
    deploymentId = response.json()[-1]
    print("url for deploymentId: ", url, "response", response.status_code)
    print('Most recent deploymentId:', deploymentId)
    url = "/".join([url, str(deploymentId)])
    response = requests.get(url, auth=(USERNAME, TOKEN))
    deploymentInfo = response.json()[0]
    print("url for deploymentInfo: ", url, "response", response.status_code)

    # Data request
    dateTag = "".join(["beginDT=", startTime, "&", "endDT=", endTime])
    url = "/".join([dataUrl, urlDesignator, streamTag])
    url = "&".join([url, dateTag])
    response = requests.get(url, auth=(USERNAME, TOKEN))
    print("url for data: ", url, "response", response.status_code)

    if "No data for request" in response.json():
        print("No data for request. Sending email to investigate and alert OOI helpdesk.")
        sendmail("OOI WARNING: No data for request! " + reference_name_dash + "-" + run_name,
                 "OOI data collection may have failed! Investigate and alert OOI Help Desk " + url)
        sys.exit()

    if "allURLs" not in response.json():
        print("1 - Request [FAILED] - exiting! Skipping this time interval:",
              next(iter(response.json().keys())) + ":",
              next(iter(response.json().values())))
        with open(os.path.join(runPath, endtime_file_name), "w") as output:
            output.write(str(UTCDateTime(startTime) + float(run["time_interval"][0])))
        print(endtime_file_name, "set =", str(UTCDateTime(startTime) + float(run["time_interval"][0])))
        if int(run.get("gap", [0])[0]) == 1:
            with open(os.path.join(runPath, gap_file_name), "a") as output:
                output.write("%s %s %s\n" % ("1 - Data request failed. Skipping this time interval:",
                                             str(UTCDateTime(startTime)),
                                             str(UTCDateTime(startTime) + float(run["time_interval"][0]))))
        if response.status_code >= 400:
            print("Error! Response = " + str(response.status_code))
            sendmail("OOI ERROR: OOI request " + reference_name_dash + "-" + run_name + " got status code " + str(response.status_code),
                     "URL [" + url + "]")
        sys.exit()

    responseUrl = response.json()["allURLs"][1]
    statusUrl = "/".join([responseUrl, "status.json"])  # async status

    # Wait up to maxCycle cycles
    count = 0
    maxCycle = int(run["maxCycle"][0])
    delay = int(run["delay"][0])
    success = False
    response_codes = ""
    while count <= maxCycle:
        count += 1
        time.sleep(delay)
        status = requests.get(statusUrl)
        print("url for status", statusUrl, "response", status.status_code)
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
                 "Status URL [" + statusUrl + "] got response codes " + response_codes)
        print("2 - [FAILED]! COUNTS =", count, "Data request still incomplete after 5 status requests. Skipping this time interval", "Response codes:", response_codes)
        with open(os.path.join(runPath, endtime_file_name), "w") as output:
            output.write(str(UTCDateTime(startTime) + float(run["time_interval"][0])))
        print(endtime_file_name, "set =", str(UTCDateTime(startTime) + float(run["time_interval"][0])))
        if int(run.get("gap", [0])[0]) == 1:
            with open(os.path.join(runPath, gap_file_name), "a") as output:
                output.write("%s %s %s\n" % ("2 - Request incomplete after 5 status requests. Skipping this time interval:",
                                             str(UTCDateTime(startTime)),
                                             str(UTCDateTime(startTime) + float(run["time_interval"][0]))))
        sys.exit()

    # Read the netCDF location from NCML
    print("Reading netCDF file.")
    complete = []
    dataTag = status.json()
    for key in dataTag.keys():
        if isinstance(dataTag[key], str):
            complete = dataTag[key]

    if complete == "complete":
        if 'prest' in run_name:
            ncmlUrl = "deployment%04i_%s-streamed-%s_real_time.ncml" % (deploymentId, reference_name_dash, run_name)
        elif 'lily' in run_name or 'nano' in run_name:
            ncmlUrl = "deployment%04i_%s-streamed-botpt_%s_sample.ncml" % (deploymentId, reference_name_dash, run_name)
        ncmlUrl = "/".join([responseUrl, ncmlUrl])
        print(ncmlUrl)

        ncml = urllib.request.urlopen(ncmlUrl)
        tree = ET.ElementTree(file=ncml)
        ncml.close()
        root = tree.getroot()
        netcdfUrl = None
        for child in root:
            if "aggregation" in child.tag:
                for element in child:
                    netCDF = element.get("location").strip()
                    print(netCDF)
                    netcdfUrl = "/".join([responseUrl, netCDF])
        netcdfUrl = netcdfUrl.replace(run["HTTPServer_server"][0], run["OPENDAP_server"][0])

        try:
            fh = Dataset(netcdfUrl)
            t = fh.variables[str(run["datatime"][0])][:]
            print('Number of data points in NetCDF file:', len(t))
            print("Successfully opened NetCDF file.")
        except Exception as e:
            print("Failed to open NetCDF file:", netcdfUrl)
            print("Error:", str(e))
            sys.exit()

        # MC: use unrounded median for arithmetic; round only for logging
        sp_unrounded_global = float(np.median(np.diff(t))) if len(t) >= 3 else 1.0  # seconds per sample
        sp_log = round(sp_unrounded_global, 6)
        print(f"[MC] Derived global sampling period: {sp_log:.6f} s")

        # Convert times to UTCDateTime
        utc_t = [UTCDateTime(str(utcdata1900(float(x)))) for x in t]
        requested_start = UTCDateTime(startTime)
        requested_end = UTCDateTime(endTime)
        data_start = utc_t[0]
        data_end = utc_t[-1]

        # Subset to requested window
        if requested_start != data_start or requested_end != data_end:
            start_idx = np.searchsorted(utc_t, requested_start, "left")
            end_idx = np.searchsorted(utc_t, requested_end, "right")
            new_t = t[start_idx:end_idx]
            new_utc = utc_t[start_idx:end_idx]
        else:
            new_t = t
            new_utc = utc_t

        # Build gap detection arrays
        dt_all = np.diff(new_t)
        rd = int(run.get("round_digit", [6])[0])  # default 6 decimals
        # We'll compute tolerance per channel/segment below; here only for logging
        statement = [False]
        for i in range(1, len(new_t)):
            gap_val = round(float(dt_all[i-1]), rd)
            statement.append(False)  # actual gap flag set later per segment with per-channel tol
        split_idx = np.where(statement)[0]
        print(">>> initial gap indices (placeholder):", split_idx)

        # Prepare splits on UTC strings (we'll recompute real gaps per segment)
        new_utc_str = [str(x) for x in new_utc]
        new_utc_arr = np.array(new_utc_str, dtype=object)
        # For simplicity keep current contiguous grouping; per-segment tolerance will decide keeps
        data_split = np.split(new_utc_arr, split_idx)

        # Start-boundary messages (informational)
        if (str(file_end_time)[11:27] != "23:59:59.999999Z" and file_end_time + sp_unrounded_global != UTCDateTime(data_split[0][0])):
            delta_start = (UTCDateTime(data_split[0][0]) - (file_end_time + sp_unrounded_global))
            if delta_start < sp_unrounded_global:
                print("5* [MC] Start-boundary adjustment: first sample " + str(UTCDateTime(data_split[0][0])) +
                      f" occurs {delta_start:.6f}s after expected {file_end_time + sp_unrounded_global}. Dropping boundary sample to avoid overlap.")
            else:
                print("6* [MC] Start-of-window gap: first sample " + str(UTCDateTime(data_split[0][0])) +
                      f" arrives {delta_start:.6f}s after expected {file_end_time + sp_unrounded_global} (> sp). Splitting segments.")

        last_written_end_dt = None  # MC: track last clamped end across channels/segments

        # --- Bundled per request window writing --- # MC
        channel_ls = run["Channel"][0][1:-1].split(",")
        datatype_ls = run["datatype"][0]
        network_station_param = readParam(os.path.join(paramPath, reference_name_underscore + ".txt"))

        window_seconds = float(UTCDateTime(endTime) - UTCDateTime(startTime))  # MC: duration-aware

        for k in range(len(channel_ls)):
            name = ast.literal_eval(datatype_ls)[channel_ls[k]]
            data_point = fh.variables[name][:] * float(run.get("unit_convert", [1.0])[0])

            # Index offset to slice data_point aligned with subset
            l_idx = (np.searchsorted(utc_t, requested_start, "left") if requested_start != data_start or requested_end != data_end else 0)

            interval_stream = Stream()  # MC
            channel_param = readParam(os.path.join(paramPath, reference_name_underscore + "_" + channel_ls[k] + ".txt"))

            # MC: per-channel tolerance configuration
            # Prefer GAP_TOL_FACTOR (fraction of period); fallback to TOL_SECONDS; min floor MIN_TOL_SECONDS
            gap_tol_factor = float(channel_param.get("GAP_TOL_FACTOR", ["0.20"])[0])  # default 0.20 of sp
            tol_seconds_config = float(channel_param.get("TOL_SECONDS", ["-1"]) [0])  # -1 means unused
            min_tol_seconds = float(channel_param.get("MIN_TOL_SECONDS", ["0.005"])[0])  # default 5 ms

            # Optional: minimum segment duration purely for logging (not a hard skip)
            min_segment_seconds = float(channel_param.get("MIN_SEGMENT_SECONDS", ["0.0"])[0])

            for s in range(len(data_split)):
                ti = data_split[s]
                if len(ti) == 0:
                    continue
                start_time_str = ti[0]
                end_time_str = ti[-1]

                # Clamp end (strict half-open): next_start - sp_global, else requested_end - sp_global
                cur_start_dt = UTCDateTime(start_time_str)
                cur_end_dt = UTCDateTime(end_time_str)
                if s < len(data_split) - 1:
                    next_start_dt = UTCDateTime(data_split[s + 1][0])
                    cur_end_dt = min(cur_end_dt, next_start_dt - sp_unrounded_global)
                else:
                    cur_end_dt = min(cur_end_dt, requested_end - sp_unrounded_global)

                # MC: keep only timestamps <= clamped end; guard windows shorter than one sample period
                ti_dt = [UTCDateTime(ts) for ts in ti]
                n_keep = bisect.bisect_right(ti_dt, cur_end_dt)
                if n_keep == 0:
                    print(f"[MC] Segment from {ti_dt[0]} has no samples <= {cur_end_dt}, skipping.")
                    continue

                # Compute per-segment sampling period/rate from timestamps (robust across rates)
                if n_keep >= 3:
                    # Use timestamps for period estimate
                    seg_diffs = np.diff([ts.timestamp for ts in ti_dt[:n_keep]])
                    sp_seg = float(np.median(seg_diffs))
                elif n_keep == 2:
                    sp_seg = float(ti_dt[1].timestamp - ti_dt[0].timestamp)
                else:
                    sp_seg = sp_unrounded_global  # fallback for single-sample segments
                sp_rate_seg = 1.0 / sp_seg

                # Per-channel tolerance: prefer absolute seconds if set; else fraction of sp_seg; enforce minimum
                if tol_seconds_config > 0:
                    tol_seg = max(tol_seconds_config, min_tol_seconds)
                else:
                    tol_seg = max(gap_tol_factor * sp_seg, min_tol_seconds)

                # Slice corresponding data samples
                data_i_point = data_point[l_idx : l_idx + n_keep]
                l_idx += n_keep

                # Convert masked arrays
                if isinstance(data_i_point, MaskedArray):
                    data_i_point = data_i_point.filled(np.nan)

                # Log micro-segment by duration (optional)
                if min_segment_seconds > 0:
                    seg_duration = float(ti_dt[n_keep - 1] - ti_dt[0])
                    if seg_duration < min_segment_seconds:
                        print(f"[MC] Micro-segment kept (duration {seg_duration:.3f}s < {min_segment_seconds}s): "
                              f"{len(data_i_point)} samples; {channel_ls[k]} {cur_start_dt} -> {cur_end_dt}")

                # Stats for this Trace (per-segment rate)
                stats = {
                    "network": network_station_param["Net"][0],
                    "station": network_station_param["Sta"][0],
                    "location": channel_param["C_loc"][0],
                    "channel": channel_param["Cha"][0],
                    "npts": len(data_i_point),
                    "sampling_rate": sp_rate_seg,
                    "mseed": {"dataquality": run["dataquality"][0]},
                    "starttime": cur_start_dt,
                }

                # Unit convert to desired output units
                data_i_point = data_i_point / float(channel_param.get("R_Value", ["1.0"])[0])

                interval_stream.append(Trace(data=data_i_point, header=stats))

                # Track last clamped end across all segments/channels
                last_written_end_dt = cur_end_dt if (last_written_end_dt is None or cur_end_dt > last_written_end_dt) else last_written_end_dt

            # If we collected any traces for this channel, write ONE file for the request window
            if len(interval_stream) > 0:
                start_time_for_name = str(interval_stream[0].stats.starttime)
                end_time_for_name = str(last_written_end_dt)
                # Duration-aware expected sample count (using first segment rate as proxy)
                sp_rate_first = float(interval_stream[0].stats.sampling_rate)
                expected_n = int(round(window_seconds * sp_rate_first))
                actual_n = int(sum(tr.stats.npts for tr in interval_stream))
                print(f"[MC] Expected ~{expected_n} samples over {window_seconds:.1f}s at {sp_rate_first:.6f} Hz, wrote {actual_n}")

                print("Writing bundled miniSEED file for this window.")
                encoding_name = str(interval_stream[0].data.dtype).upper()
                interval_stream.write(
                    os.path.join(
                        mseedPath,
                        network_station_param["Net"][0] + "." + network_station_param["Sta"][0] + "." + channel_param["C_loc"][0] + "." + channel_param["Cha"][0]
                        + "." + start_time_for_name[0:4] + "." + "{:0>3}".format(str(time.strptime(start_time_for_name[0:10], "%Y-%m-%d").tm_yday))
                        + "." + start_time_for_name[11:23].replace(":", ".")
                        + "-" + end_time_for_name[0:4] + "." + "{:0>3}".format(str(time.strptime(end_time_for_name[0:10], "%Y-%m-%d").tm_yday))
                        + "." + end_time_for_name[11:23].replace(":", ".") + mseed_file_ext,
                    ),
                    format="MSEED",
                    encoding=encoding_name,
                    reclen=int(run["reclen"][0]),
                )
                print('Bundled file ntraces:', len(interval_stream), 'total npts:', actual_n)
            else:
                print(f"[MC] No segments to write for channel {channel_ls[k]} in this window.")

        # After all channels, write endtime_<...>.txt
        if last_written_end_dt is not None:
            with open(os.path.join(runPath, endtime_file_name), "w") as output:
                output.write(str(last_written_end_dt))
            print(endtime_file_name, "set =", str(last_written_end_dt), "[MC bundled]")
        else:
            # No data segments; advance by interval to avoid stalling
            with open(os.path.join(runPath, endtime_file_name), "w") as output:
                output.write(str(UTCDateTime(startTime) + float(run["time_interval"][0])))
            print(endtime_file_name, "set =", str(UTCDateTime(startTime) + float(run["time_interval"][0])), "[MC no data]")

elif difTime < float(run["diftime"][0]):
    print("SKIP request: difTime LT [" + str(run["diftime"][0]) + "]")
    with open(os.path.join(runPath, endtime_file_name), "w") as output:
        output.write(str(UTCDateTime(endTime)))
    print(endtime_file_name, "set =", str(UTCDateTime(endTime)))

elif UTCDateTime(endTime) > UTCDateTime(truncTime):
    print("SKIP request: end time [" + UTCDateTime(endTime).strftime("%Y-%m-%d %H:%M:%S") + "] GT truncation time [" + UTCDateTime(truncTime).strftime("%Y-%m-%d %H:%M:%S") + "] so no data requested")
    sys.exit()

sys.stdout = old_stdout
