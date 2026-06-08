#!/home/coszo/miniconda3/envs/ooi_env/bin/python

#1/21/2025 Mika Thompson: This script has been cleaned up, but it has not been updated to be consistent with OOI_data_request_and_convert_mseed.py.

from obspy import UTCDateTime, read, Trace, Stream
from netCDF4 import Dataset
import numpy as np
import os
import obspy
import sys
import time
import urllib.request
import xml.etree.cElementTree as ET
import requests
import json
from convertutc import *

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
param_path = os.path.join(root_path, "param")
bin_path = os.path.join(root_path, "bin")
run_path = os.path.join(root_path, "run")
mseed_path = os.path.join(root_path, "mseed")
log_path = os.path.join(root_path, "log")

# old_stdout = sys.stdout

# log_file = open(logPath+"/"+str(UTCDateTime.now())[0:10]+"-message.log","a")
# sys.stdout = log_file
sys.path.insert(0, bin_path)

from read_param import *
from mail import *

run = read_param(os.path.join(param_path, "run.txt"))

reference_designator = sys.argv[1]
url_designator = reference_designator.replace("-", "/", 2)

# request in formation for OOI sensor
USERNAME = os.environ.get("OOI_USERNAME")
TOKEN = os.environ.get("OOI_TOKEN")

if not USERNAME or not TOKEN:
    raise RuntimeError(
        "Missing OOI credentials. "
        "Set OOI_USERNAME and OOI_TOKEN in the environment."
    )


start_time = str(
    UTCDateTime(open(os.path.join(run_path, "endtime.txt"), "r").read()) + 0.000001
)
print("\n*CURRENT TIME:", UTCDateTime.now())
print("START TIME:", startTime)

if UTCDateTime(start_time) + float(run["time_interval"][0]) <= UTCDateTime(
    start_time[0:10] + "T23:59:59.999999Z"
):
    end_time = str(UTCDateTime(start_time) + float(run["time_interval"][0]))
    print("END TIME:"), endTime
else:
    end_time = str(UTCDateTime(start_time[0:10] + "T23:59:59.999999Z"))
    print("END TIME:"), end_time

dif_time = UTCDateTime(end_time) - UTCDateTime(start_time)

if (
    run["data_endtime"][0] == "NOW"
    or UTCDateTime(run["data_endtime"][0]) >= UTCDateTime.now()
):
    trunc_time = UTCDateTime.now() - float(run["trunctime"][0])
else:
    trunc_time = UTCDateTime(run["data_endtime"][0])

if UTCDateTime(end_time) <= UTCDateTime(trunc_time) and dif_time >= float(
    run["dif_time"][0]
):

    base_url = (
        "https://ooinet.oceanobservatories.org/api/m2m/12587/events/deployment/inv"
    )
    data_url = "https://ooinet.oceanobservatories.org/api/m2m/12576/sensor/inv"
    
    # Modified to include tidal pressure data - MT
    if 'lily' in run["Sensor_name"][0] or 'nano' in run["Sensor_name"][0]:
        streamTag = (
            "streamed/botpt_"
            + str(run["Sensor_name"][0])
            + "_sample?include_provenance=true&format=application/netcdf"
        )
    elif 'prest' in run["Sensor_name"][0]:
        streamTag = (
        "streamed/" + str(run["Sensor_name"][0])
        + "_real_time?include_provenance=true&format=application/netcdf"
        )
        
    url = "/".join([baseUrl, urlDesignator])

    # Request Deployment Information

    response = requests.get(url, auth=(USERNAME, TOKEN))
    deploymentId = response.json()[0]

    url = "/".join([url, str(deploymentId)])
    response = requests.get(url, auth=(USERNAME, TOKEN))
    deployment_info = response.json()[0]

    # Asynchronous Request

    date_tag = "".join(["beginDT=", start_time, "&", "endDT=", end_time])
    url = "/".join([data_url, url_designator, stream_tag])
    url = "&".join([url, date_tag])

    response = requests.get(url, auth=(USERNAME, TOKEN))

    if "allURLs" not in response.json():
        with open(os.path.join(runPath, "endtime.txt"), "w") as output:
            output.write(str(UTCDateTime(start_time) + float(run["time_interval"][0])))
        print(
            "[FAILED]!",
            list(response.json().keys())[0] + ":",
            list(response.json().values())[0],
        )
        sys.exit()

    response_url = response.json()["allURLs"][1]
    status_url = "/".join([response_url, "status.json"])
    print("[Status URL]", status_url)

    # wait maximum of 5 cycles

    count = 0
    max_cycle = int(run["max_cycle"][0])
    delay = int(
        run["delay"][0]
    )  # wait time in seconds (Most likely 60 seconds for the operational mode
    success = False
    while count <= max_cycle:
        count += 1
        time.sleep(delay)
        status = requests.get(status_url)
        print(status.content)
        if status.status_code == 404:
            print("[TRY]", count, "[DONE]", "Not yet")
        elif status.status_code == 200:
            print("[TRY]", count, "[DONE]", "Yes")
            success = True
            break
    if not success:
        # here we need to notify the operator via email
        sendmail("Not be able to request data more than 5 times")
        print("[FAILED]! COUNTS =", count, "Request data more than 5 times")
        with open(os.path.join(run_path, "endtime.txt"), "w") as output:
            output.write(str(UTCDateTime(start_time) + float(run["time_interval"][0])))
        sys.exit()

    # request was a success, we now read the netCDF file if it is complete
    complete = []
    data_tag = status.json()
    for key in data_tag.keys():
        file_tag = key.encode("ascii", "ignore")
        if isinstance(data_tag[key], str):
            complete = data_tag[key].encode("ascii", "ignore")

    # read the ncml file to get the netCDF file location
    if complete == "complete":
        ncml_url = "deployment%04i_%s%s%s%s%s" % (
            deployment_id,
            reference_designator,
            "-streamed-botpt_",
            str(run["Sensor_name"][0]),
            "_sample",
            ".ncml",
        )
        ncml_url = "/".join([response_url, ncml_url])

        ncml = urllib.request.urlopen(ncml_url)
        tree = ET.ElementTree(file=ncml)
        ncml.close()
        root = tree.getroot()
        netcdf_url = None
        for child in root:
            if "aggregation" in child.tag:
                for element in child:
                    netcdf = element.get("location").strip()
                    netcdf_url = "/".join([response_url, netcdf])

        # to read the netCDF file from URL without downloading it, we have to access the OPENDAP server
        # here I am converting the HTTPServer URL to OPENDAP URL. This is NOT the proper way of doing it
        # we should find a way to get the OPENDAP URL directly from the service
        netcdf_url = netcdf_url.replace(
            run["http_server"][0], run["opendap_server"][0]
        )
        fh = Dataset(netcdf_url)
        param = read_param(
            os.path.join(param_path, reference_designator.replace("-", "_") + ".txt")
        )
        param2 = read_param(
            os.path.join(
                param_path,
                reference_designator.replace("-", "_")
                + "_"
                + str(run["network"][0])
                + "_"
                + str(run["location_code"][0])
                + ".txt",
            )
        )
        t = fh.variables[str(run["data_time"][0])][:]
        bp = fh.variables[str(run["data_type"][0])][:] * float(run["unit_convert"][0])
        sp = round(float(t[2]) - float(t[1]), 2)
        sp_rate = 1.0 / sp
        statment = []
        utc_t = []
        for i in range(len(t)):
            utc_t.append(str(UTCDateTime(str(utcdata1900(float(t[i]))))))
            statment.append(round(np.diff(t)[i - 1], 1) != sp)
        data_split = np.split(utc_t, np.where(statment)[0])
        l = 0
        for j in range(len(data_split)):
            ti = data_split[j]
            end_time = ti[-1]
            start_time = ti[0]
            with open(os.path.join(run_path, "endtime.txt"), "w") as output:
                output.write(end_time)
            bpi = bp[l : l + len(ti)]
            l = l + len(ti)
            t_p = np.array([ti, bpi]).T
            tp = "\n".join("\t".join("%3s" % x for x in y) for y in t_p)
            data = np.fromstring(tp, dtype="|S1")

            # Fill header attributes
            stats = {
                "network": param["net"][0],
                "station": param["sta"][0],
                "location": param2["c_loc"][0],
                "channel": param2["cha"][0],
                "npts": len(data),
                "sampling_rate": sp_rate,
                "mseed": {"dataquality": run["dataquality"][0]},
            }
            # set current time
            stats["start_time"] = start_time
            st = Stream([Trace(data=data, header=stats)])
            # write as ASCII file (encoding=0)
            st.write(
                param["net"][0]
                + "."
                + param["sta"][0]
                + "."
                + param2["c_loc"][0]
                + "."
                + param2["cha"][0]
                + "."
                + start_time[0:4]
                + "."
                + "{:0>3}".format(
                    str(time.strptime(start_time[0:10], "%Y-%m-%d").tm_yday)
                )
                + "."
                + start_time[11:23].replace(":", ".")
                + "-"
                + end_time[0:4]
                + "."
                + "{:0>3}".format(
                    str(time.strptime(end_time[0:10], "%Y-%m-%d").tm_yday)
                )
                + "."
                + end_time[11:23].replace(":", ".")
                + ".mseed",
                format="MSEED",
                encoding=0,
                reclen=256,
            )
            os.system("mv *.mseed " + mseedPath)

elif dif_ime < float(run["dif_time"][0]):
    with open(os.path.join(runPath, "endtime.txt"), "w") as output:
        output.write(str(UTCDateTime(startTime) + float(run["diftime_interval"][0])))
    print("Skip this time interval!")

elif UTCDateTime(endTime) > UTCDateTime(truncTime):
    print("STOP: The request start time is larger than the data length (time).")
    sys.exit()

# sys.stdout = old_stdout
