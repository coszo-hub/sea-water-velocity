#!/home/coszo/miniconda3/envs/ooi_env/bin/python

###Editor: Vivian Tang, Jun29th, 2017.###
###Create metadata###
# read parameter files form "param" folder
#
# History
#  2025-09-23 Mika: This script has been reformatted to be compatible with Python 3.10. I also fixed the indentations. My edits and comments are followed by MT.
# Manoch 2017-09-27: Now using the ObsPy Frequency object for Decimation
# an issue that is addressed in the newer release of ObsPy (see Lion's comments below:
# Lion: An artifact of some of our internal objects - it is already fixed in the latest
# master and will thus be fixed in the next ObsPy version

from obspy.core.inventory import (
    Inventory,
    util,
    Network,
    Station,
    Channel,
    Site,
    Response,
)

import obspy
from obspy.core.inventory.util import Frequency

# from datetime import datetime
import os
import sys
import importlib
from datetime import datetime  # MT

from read_param import read_param

# Get arguments.
if len(sys.argv) != 2:
    print("Need to pass 'reference_name argument (with underscores not dashes)'")
    sys.exit()

reference_name_underscore = sys.argv[1]

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
param_path = os.path.join(root_path, "param")
bin_path = os.path.join(root_path, "bin")

net_sta_param = read_param(os.path.join(param_path, reference_name_underscore + ".txt"))

inv = Inventory(
    networks=[],
    source="COSZO",
) 

if str(net_sta_param["n_end"][0]) == "None":
    end_date = None
else:
    end_date = obspy.UTCDateTime(net_sta_param["n_end"][0])

net = Network(
    code=net_sta_param["net"][0],
    stations=[],
    description=net_sta_param["descript"][0],
    start_date=obspy.UTCDateTime(net_sta_param["n_start"][0]),
    end_date=end_date,
    restricted_status=net_sta_param["n_restatus"][0],
)

sta = []
stations = []

for i in range(len(net_sta_param["s_start"])):

    if str(net_sta_param["s_end"][i]) == "None":
        end_date=None
    else:
        end_date = obspy.UTCDateTime(net_sta_param["s_end"][i])

    sta = Station(
        code=net_sta_param["sta"][0],
        start_date=obspy.UTCDateTime(net_sta_param["s_start"][i]),
        end_date=end_date,
        latitude=net_sta_param["s_lat"][0],
        longitude=net_sta_param["s_lon"][0],
        elevation=net_sta_param["s_elev"][0],
        site=Site(name=net_sta_param["s_site"][0]),
        restricted_status=net_sta_param["s_restatus"][0],
    )

    stations.append(sta)

run_file_name = "run_metadata.txt"
run = read_param(os.path.join(param_path, run_file_name))
cha_list = net_sta_param["metadata_channels"][0][1:-1].split(",")

cha = []
channels = []

# Manoch 2017-09-27: Now using the ObsPy Frequency object for Decimation
# an issue that is addressed in the newer release of ObsPy,mb

for i in range(len(cha_list)):

    channel_param = read_param(
        os.path.join(param_path, reference_name_underscore + "_" + cha_list[i] + ".txt")
    )

    for j in range(len(channel_param["c_start"])):

        sample_rate = float(channel_param["c_sample_rate"][j])

        sensitivity = obspy.core.inventory.response.InstrumentSensitivity(
            value=float(channel_param["r_value"][0]),
            frequency=channel_param["r_frequency"][0],
            input_units=channel_param["r_input_units"][0],
            input_units_description=channel_param["r_input_description"][0],
            output_units=channel_param["r_output_units"][0],
            output_units_description=channel_param["r_output_description"][0],
        )

        response_stage = obspy.core.inventory.response.PolesZerosResponseStage(
            stage_sequence_number=1,
            stage_gain=float(channel_param["r_value"][0]),
            stage_gain_frequency=channel_param["r_frequency"][0],
            input_units=channel_param["r_input_units"][0],
            input_units_description=channel_param["r_input_description"][0],
            output_units=channel_param["r_output_units"][0],
            output_units_description=channel_param["r_output_description"][0],
            pz_transfer_function_type="DIGITAL (Z-TRANSFORM)",
            normalization_frequency=1.0,
            normalization_factor=1.0,
            zeros=[],
            poles=[],
            resource_id=channel_param["c_id"][j],
            resource_id2=None,
            name="Stage01",
            decimation_input_sample_rate=Frequency(sample_rate),
            decimation_factor=1,
            decimation_offset=0,
            decimation_delay=Frequency(0.0),
            decimation_correction=Frequency(0.0),
        )

        if str(channel_param["c_end"][j]) == "None":
            end_date=None
        else:
            end_date = obspy.UTCDateTime(channel_param["c_end"][j])

        res = Response(
            instrument_sensitivity=sensitivity, response_stages=[response_stage]
        )

        #Removed these parameters from the channel metadata because they are optional and Orest Kawka and I are not sure what values to use. MT
        # - dip=(channel_param["c_Dip"][j]), 
        # - types=channel_param["c_type"][j].split(","), 
        # - clock_drift_in_seconds_per_sample=(channel_param["c_clockdrift"][j]),
        # - calibration_units=channel_param["Cal_unit"][j],
        # - calibration_units_description=channel_param["Cal_unit_descript"][j],

        cha = Channel(
            code=channel_param["cha"][0],
            location_code=channel_param["c_loc"][0],
            start_date=obspy.UTCDateTime(channel_param["c_start"][j]),
            end_date=end_date,
            latitude=channel_param["c_lat"][0],
            longitude=(channel_param["c_lon"][0]),
            elevation=(channel_param["c_elev"][0]),
            depth=(channel_param["c_dep"][0]),
            azimuth=(channel_param["c_az"][0]),
            sample_rate=(channel_param["c_sample_rate"][j]),
            description=channel_param["c_description"][0],
            comments=[
                util.Comment(
                    channel_param["c_id"][j],
                    id=None,
                    begin_effective_time=obspy.UTCDateTime(channel_param["c_start"][j]),
                    end_effective_time=None,
                    authors=None,
                )
            ],
            sensor=util.Equipment(description=channel_param["c_sensor"][0]),
            response=res,
        )

        channels.append(cha)

print(f"Built stations (pre-filter): {len(stations)}")  # expect 2
print(f"Built channels (pre-filter): {len(channels)}")  # depends on your channel files

# Now tie it all together.
inv.networks.append(net)

# Added this check to avoid TypeError None <= datetime(...). MT
def leq(a, b):
    if a is None and b is None: 
        return True
    if a is None:               
        return False
    if b is None:              
        return True
    return a <= b

# Added this check to avoid TypeError None <= datetime(...). MT
def geq(a, b):
    if a is None and b is None: 
        return True
    if a is None:               # a is −∞? If you want −∞ for start, return False
        return False
    if b is None:               # b is −∞ → a >= −∞ is always True
        return True
    return a >= b

for sta in stations:
    if leq(sta.end_date, net.end_date) and geq(sta.start_date, net.start_date):
        net.stations.append(sta)
        for cha in channels:
            if leq(cha.end_date, sta.end_date) and geq(cha.start_date, sta.start_date):
                sta.channels.append(cha)

for s in net.stations:
    print("Station:", s.code, s.start_date, s.end_date, "channels:", len(s.channels))

inv.write(
    net_sta_param["net"][0] + "_" + net_sta_param["sta"][0] + ".xml",
    format="STATIONXML",
    validate=True,
)  # MT
# inv.write(net_sta_param['Net'][0] + "_" + net_sta_param['Sta'][0] + ".xml", format="stationxml", validate=True)
