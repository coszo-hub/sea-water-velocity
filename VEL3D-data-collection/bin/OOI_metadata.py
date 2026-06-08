#!/home/coszo/miniconda3/envs/ooi_env/bin/python

import sys
import os
import ast
import requests
import json
import obspy

from convert_utc import *
from mail import *
from read_param import read_param

# 2025-01-07 Mika: Removed code that creates dataless seed from stationxml. Only need to provide EarthScope with metadata in one format or the other. 
# 2025-09-23 Mika: This script has been reformatted to be compatible with Python 3.10. I also fixed the indentations. My edits and comments are followed by MT.

# Set path for each folder
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
output_path = os.path.join(root_path, "output")  # MT
param_path = os.path.join(root_path, "param")
bin_path = os.path.join(root_path, "bin")
xml_path = os.path.join(output_path, "xml")

create_metadata = os.path.join(bin_path, "create_metadata.py")

# Request information for OOI sensor

# Get run_params from ../param/run_metadata.txt:
run_file_name = "run_metadata.txt"
run_params = read_param(os.path.join(param_path, run_file_name))

USERNAME = os.environ.get("OOI_USERNAME")
TOKEN = os.environ.get("OOI_TOKEN")

if not USERNAME or not TOKEN:
    raise RuntimeError(
        "Missing OOI credentials. "
        "Set OOI_USERNAME and OOI_TOKEN in the environment."
    )

# Build the referenceID array which is based on the reference designator
# Parse "referenceID=[["RS03CCAL","MJ03F"],["RS03ECAL","MJ03E"],["RS03INT2","MJ03D"]]"
reference_id = ast.literal_eval(run_params["reference_id"][0])
print(reference_id)

base_url = run_params["base_url"][0]

for j in range(len(reference_id)):

    print("===============================")
    print("Reference: ", reference_id[j][0], reference_id[j][1])

    url = "/".join([base_url, reference_id[j][0], reference_id[j][1]])
    response = requests.get(url, auth=(USERNAME, TOKEN))

    print(json.dumps(response.json()))

    sensor_list = response.json()

    for i in range(len(sensor_list)):

        # if json.dumps(sensorList[i]).find("BOTPTA") != -1:
        # Modified to include tidal pressure data - MT
        if any(word in json.dumps(sensor_list[i]) for word in ['BOTPTA', 'PREST']):

            file_name = json.dumps(sensor_list[i])
            param_file_name_dash = (
                reference_id[j][0] + "-" + reference_id[j][1] + "-" + file_name[1:-1]
            )
            param_file_name_underscore = param_file_name_dash.replace("-", "_")

            # Check to see if param file exists
            if os.path.isfile(os.path.join(param_path, param_file_name_underscore + ".txt")):

                # Compare with the current parameter file
                sys.path.append(param_path)
                net_sta_param = read_param(os.path.join(param_path, param_file_name_underscore + ".txt"))
                print("\n", param_file_name_dash, ", found it!", "\n")
                
                # Parse "e.g., channel = [LDO_01,LK1_01]""
                channel_list = net_sta_param["metadata_channels"][0][1:-1].split(",")

                # Deployment info
                url_designator = param_file_name_dash.replace("-", "/", 2)
                url2 = "/".join([base_url, url_designator])
                response2 = requests.get(url2, auth=(USERNAME, TOKEN))
                deployment_id = response2.json()[-1] # Most recent deployment, MT
                print("url for deploymentId: ", url2, "response", response.status_code)
                print('Most recent deployment_id:', deployment_id)
                url2 = "/".join([url2, str(deployment_id)])
                response2 = requests.get(url2, auth=(USERNAME, TOKEN))
                deployment_info = response2.json()[0]
                print("url for deployment_info: ", url, "response", response.status_code)
                
                # print(deployment_info)

                # Grab information for building stationxml
                (
                    latitude,
                    longitude,
                    depth,
                    uid,
                    description,
                    calibration,
                    event_start_time,
                ) = ([], [], [], [], [], [], [])

                for key in deployment_info.keys():

                    if key in ("location", "sensor"):

                        print(key, ":")

                        for this_key in deployment_info[key]:

                            if this_key == "latitude":
                                latitude.append(deployment_info[key][this_key])
                                print("\t", this_key, ":", deployment_info[key][this_key])

                            elif this_key == "longitude":
                                longitude.append(deployment_info[key][this_key])
                                print("\t", this_key, ":", deployment_info[key][this_key])

                            elif this_key == "depth":
                                depth.append(deployment_info[key][this_key])
                                print("\t", this_key, ":", deployment_info[key][this_key])

                            elif this_key == "uid":
                                uid.append(deployment_info[key][this_key])
                                print("\t", this_key, ":", deployment_info[key][this_key])

                            elif this_key == "description":
                                description.append(deployment_info[key][this_key])
                                print("\t", this_key, ":", deployment_info[key][this_key])

                            elif this_key == "calibration":
                                calibration.append(deployment_info[key][this_key])
                                print("\t", this_key, ":", deployment_info[key][this_key])
                    else:

                        if str(key) == "event_start_time":
                            event_start_time.append(deployment_info[key])
                            print(str(key), ":", utc(deployment_info[key]))

                new_lat = latitude[0]
                new_lon = longitude[0]
                new_elev = -depth[0]

                for i in range(len(channel_list)):

                    channel_param = read_param(
                        os.path.join(
                            param_path,
                            param_file_name_underscore + "_" + channel_list[i] + ".txt",
                        )
                    )
                    # if obspy.UTCDateTime(channel_param['C_start'][0]) != obspy.UTCDateTime(utc(eventStartTime[0])) or new_lat != float(channel_param['C_lat'][0]) or new_lon != float(channel_param['C_lon'][0]) or new_elev != float(channel_param['C_elev'][0]):
                # 	print("Need to update metadata!")
                # 		sendmail("Need to update metadata for " + paramFileNameDash)
                # 		sys.exit()

                print("\n", "Result: Same metadata!\n")

                # Create Stationxml via create_metadata.py
                os.system(create_metadata + " " + param_file_name_underscore)
                
                # Move stationxml to output/xml/
                os.system(
                    "mv " + net_sta_param["net"][0] + "_" + net_sta_param["sta"][0] + ".xml " + xml_path
                )

            else:

                print("\n\n[ERROR] parameter file: "), param_file_name_dash, "not found!"

                from mail import *

                sendmail("Not found " + param_file_name_dash)
