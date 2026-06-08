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
from numpy.ma import MaskedArray
from convertutc import *

### History ###
# 2025-12-12 Copilot: Option A (strict half-open) + tolerance gap detection; improved messages (# MC).
# 2025-12-12 Copilot: **Bundled per hour** (OOI_data_hourly_bundled.py): write one miniSEED file per channel per hour, keeping micro-segments (# MC).
# 2025-12-12 Mika: Removed restriction that only allowed data requests up to midnight. Reduced trunctime from 24 hours to 60 seconds in run_prest.txt.
# 2025-10-01 Mika: When running this code, this message can be ignored: <frozen importlib._bootstrap>:241: RuntimeWarning: numpy.ndarray size changed, may indicate binary incompatibility. Expected 16 from C header, got 96 from PyObject
# 2025-09-23 Mika: Reformatted for Python 3.10. MT
# 2017-09-20 Manoch: change start time from file_end_time+0.000001 to +0.000100 to avoid gaps.
# 2017-09-19 Manoch: comments and sampling fixes.

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
if int(run["gap"][0]) == 1:
    gap_file_name = "gap_" + reference_name_dash + "_" + run_name + ".txt"

# End time = start + interval
# Removed restriction that only allowed data requests up to midnight. MT.
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

# Proceed only if window is not too fresh and long enough
# Ingestion delay is usually around 5 s, but can range up to 30 s. trunctime is currently set to 60 s in run_prest.txt. MT
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
        if int(run["gap"][0]) == 1:
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
        if int(run["gap"][0]) == 1:
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

        # MC: median dt (unrounded for arithmetic), round only for log
        sp_unrounded = float(np.median(np.diff(t))) if len(t) >= 3 else 1.0  # MC
        sp_log = round(sp_unrounded, 6)  # MC
        print('Sampling interval (median):', sp_log)  # MC
        sp_rate = 1.0 / sp_unrounded  # MC
        tol = max(0.25 * sp_unrounded, 0.001)  # MC tolerance for jitter

        # Convert to UTCDateTime array
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

        # MC: do NOT hard-skip short overall windows; just warn
        if len(new_t) < int(run["data_sp"][0]):
            print(f"[MC] Overall window has only {len(new_t)} samples (< {run['data_sp'][0]}). Proceeding with bundling.")

        # Gap detection with tolerance
        dt = np.diff(new_t)
        rd = int(run["round_digit"][0]) # round_digit = 1
        sp_r = round(float(sp_unrounded), rd)
        tol_r = round(float(tol), rd)
        statement = [False]
        for i in range(1, len(new_t)):
            gap_val = round(float(dt[i-1]), rd)
            gap = abs(gap_val - sp_r) > tol_r
            statement.append(gap)
            if gap:
                print(f"Gap detected: spacing {gap_val}s (expected {sp_r}s, tol {tol_r}s) [MC]")
        split_idx = np.where(statement)[0]
        print(">>> gap indices:", split_idx)

        # Split into contiguous segments (as strings for start/end names)
        new_utc_str = [str(x) for x in new_utc]
        new_utc_arr = np.array(new_utc_str, dtype=object)
        data_split = np.split(new_utc_arr, split_idx)

        # Start-boundary messages
        if (str(file_end_time)[11:27] != "23:59:59.999999Z" and file_end_time + sp_unrounded != UTCDateTime(data_split[0][0])):
            delta_start = (UTCDateTime(data_split[0][0]) - (file_end_time + sp_unrounded))
            if delta_start < sp_unrounded:
                print("5* [MC] Start-boundary adjustment: first sample " + str(UTCDateTime(data_split[0][0])) +
                      f" occurs {delta_start:.6f}s after expected {file_end_time + sp_unrounded}. Dropping boundary sample to avoid overlap.")
            else:
                print("6* [MC] Start-of-window gap: first sample " + str(UTCDateTime(data_split[0][0])) +
                      f" arrives {delta_start:.6f}s after expected {file_end_time + sp_unrounded} (> sp). Splitting segments.")

        # MC: Determine last clamped end across all segments for endtime file
        last_written_end_dt = None  # MC

        # --- Bundled per-hour writing --- # MC
        channel_ls = run["Channel"][0][1:-1].split(",")
        datatype_ls = run["datatype"][0]
        param = readParam(os.path.join(paramPath, reference_name_underscore + ".txt"))

        for k in range(len(channel_ls)):
            name = ast.literal_eval(datatype_ls)[channel_ls[k]]
            data_point = fh.variables[name][:] * float(run["unit_convert"][0])

            # Index offset to slice data_point in parallel to data_split
            l_idx = (np.searchsorted(utc_t, requested_start, "left") if requested_start != data_start or requested_end != data_end else 0)

            # Build one Stream per channel and append Traces per segment
            hour_stream = Stream()  # MC
            channel_param = readParam(os.path.join(paramPath, reference_name_underscore + "_" + channel_ls[k] + ".txt"))

            for s in range(len(data_split)):
                ti = data_split[s]
                if len(ti) == 0:
                    continue
                start_time_str = ti[0]
                end_time_str = ti[-1]

                # Clamp end (half-open): to next segment start - sp, else to requested_end - sp
                cur_start_dt = UTCDateTime(start_time_str)
                cur_end_dt = UTCDateTime(end_time_str)
                if s < len(data_split) - 1:
                    next_start_dt = UTCDateTime(data_split[s + 1][0])
                    cur_end_dt = min(cur_end_dt, next_start_dt - sp_unrounded)  # MC
                else:
                    cur_end_dt = min(cur_end_dt, requested_end - sp_unrounded)  # MC

                # Keep samples whose timestamps <= clamped end
                ti_dt = [UTCDateTime(ts) for ts in ti]
                end_keep_idx = 0
                for idx, ts_dt in enumerate(ti_dt):
                    if ts_dt <= cur_end_dt:
                        end_keep_idx = idx
                    else:
                        break
                n_keep = end_keep_idx + 1

                # Slice corresponding data samples
                data_i_point = data_point[l_idx : l_idx + n_keep]
                l_idx = l_idx + n_keep

                # Convert masked arrays
                if isinstance(data_i_point, MaskedArray):
                    data_i_point = data_i_point.filled(np.nan)

                # MC: Do NOT hard-skip micro-segments; tag in logs
                if n_keep < int(run["data_sp"][0]):
                    print(f"[MC] Micro-segment kept: {n_keep} samples (< {run['data_sp'][0]}). Channel {channel_ls[k]} from {cur_start_dt} to {cur_end_dt}.")

                # Stats for this Trace
                stats = {
                    "network": param["Net"][0],
                    "station": param["Sta"][0],
                    "location": channel_param["C_loc"][0],
                    "channel": channel_param["Cha"][0],
                    "npts": len(data_i_point),
                    "sampling_rate": sp_rate,
                    "mseed": {"dataquality": run["dataquality"][0]},
                    "starttime": cur_start_dt,  # MC
                }

                # Unit convert to desired units (e.g., PSI -> Pa)
                data_i_point = data_i_point / float(channel_param["R_Value"][0])

                hour_stream.append(Trace(data=data_i_point, header=stats))  # MC

                # Track last clamped end across all segments
                last_written_end_dt = cur_end_dt if (last_written_end_dt is None or cur_end_dt > last_written_end_dt) else last_written_end_dt  # MC

            # If we collected any data for this channel, write ONE hourly file
            if len(hour_stream) > 0:
                # File name from first trace start and last clamped end
                start_time_for_name = str(hour_stream[0].stats.starttime)
                end_time_for_name = str(last_written_end_dt)
                print("Writing bundled hourly miniSEED file.")  # MC
                encoding_name = str(hour_stream[0].data.dtype).upper()
                hour_stream.write(
                    os.path.join(
                        mseedPath,
                        param["Net"][0] + "." + param["Sta"][0] + "." + channel_param["C_loc"][0] + "." + channel_param["Cha"][0]
                        + "." + start_time_for_name[0:4] + "." + "{:0>3}".format(str(time.strptime(start_time_for_name[0:10], "%Y-%m-%d").tm_yday))
                        + "." + start_time_for_name[11:23].replace(":", ".")
                        + "-" + end_time_for_name[0:4] + "." + "{:0>3}".format(str(time.strptime(end_time_for_name[0:10], "%Y-%m-%d").tm_yday))
                        + "." + end_time_for_name[11:23].replace(":", ".") + mseed_file_ext,
                    ),
                    format="MSEED",
                    encoding=encoding_name,
                    reclen=int(run["reclen"][0]),
                )
                print('Bundled file ntraces:', len(hour_stream), 'total npts:', sum(tr.stats.npts for tr in hour_stream))  # MC
            else:
                print(f"[MC] No segments to write for channel {channel_ls[k]} in this hour.")

        # After all channels, write endtime_<...>.txt using last clamped end
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
