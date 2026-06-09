#!/usr/bin/env python3
"""
Generate VEL3D-B parameter files (station + per-channel) in ../param/.

Mirrors the PREST param-file schema (see read_param.py / create_metadata.py) but
for the three RCA VEL3D-B (Nobska MAVS-4) 3-D point-velocity meters.

Sources of truth:
  - SEED codes/loc/net: OOI_channel_codes.md (Non-Tier 1 OOI Instrument Data Channel list)
  - Deployment dates/locations/UIDs: OOI M2M deployment events API (pulled 2026-06-09)
  - Stream/vars/rate: OOI M2M metadata (vel3d_b_sample, 1 Hz, m/s science vars)

Decisions (see OOI_channel_codes.md):
  net=OO, loc=20, band=L (1 Hz), velocity codes LO2/LO1/LOZ, temp LKO,
  r_value=1.0 (units M/S; velocity already m/s, declination-corrected to true N).

NOTE on c_end "None" mid-sequence: OOI sometimes returns no stop time for a
non-final deployment. Where that happens we chain c_end to the next deployment's
start (marked CHAINED below) so StationXML epochs stay valid. Verify against
deployment records if exact recovery times matter.
"""
import os

PARAM_DIR = os.path.join(os.path.dirname(__file__), "..", "param")

# ── Channel definitions (uniform across all VEL3D-B deployments: all 1 Hz / L) ──
# cha, netcdf_var, description, azimuth, dip, response units/desc
CHANNELS = [
    dict(cha="LO2", var="eastward_turbulent_velocity",
         desc="eastward_turbulent_velocity, Eastward Sea Water Velocity",
         az="90.0", dip="0.0",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="LO1", var="northward_turbulent_velocity",
         desc="northward_turbulent_velocity, Northward Sea Water Velocity",
         az="0.0", dip="0.0",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="LOZ", var="upward_turbulent_velocity",
         desc="upward_turbulent_velocity, Upward Sea Water Velocity",
         az="0.0", dip="-90.0",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="LKO", var="temperature",
         desc="temperature, Seawater Temperature",
         az="0.0", dip="0.0",
         r_in="C", r_in_d="degrees Celsius", r_out="C", r_out_d="degrees Celsius"),
]

LOC = "20"
SENSOR = "3-D Single Point Velocity Meter (Nobska MAVS-4): VEL3D Series B"

# ── Per-station metadata. deployments = list of (start, end, lat, lon, depth, uid) ──
# end=None means ongoing/last; "CHAIN" means use next deployment's start (OOI gap).
STATIONS = [
    dict(
        refdes="RS01SLBS_MJ01A_12_VEL3DB101", sta="HYSB1",
        site="RSN Hydrate Slope Base", comment="Oregon Slope Base Seafloor",
        s_lat="44.509747", s_lon="-125.405266", s_elev="-2907",
        deployments=[
            ("2014-09-13T00:00:01", "2016-07-17T00:00:00", "ATAPL-67979-00004"),
            ("2016-07-17T07:30:00", "2024-08-09T17:33:00", "ATAPL-67979-00007"),
            ("2024-08-10T04:01:00", None,                  "ATAPL-67979-00006"),
        ],
    ),
    dict(
        refdes="RS01SUM1_LJ01B_12_VEL3DB104", sta="HYS14",
        site="RSN Hydrate Summit 1-4", comment="Southern Hydrate Summit 1 Seafloor",
        s_lat="44.569173", s_lon="-125.148073", s_elev="-777",
        deployments=[
            ("2014-09-09T00:27:00", "2016-07-23T00:00:00", "ATAPL-67979-00001"),
            ("2016-07-24T04:21:00", "2017-08-11T00:30:00", "ATAPL-67979-00006"),
            ("2017-08-11T23:00:00", "2018-06-26T03:30:00", "ATAPL-67979-00004"),
            ("2018-06-26T17:30:00", "CHAIN",               "ATAPL-67979-00002"),
            ("2019-06-25T13:52:10", "2024-08-20T17:04:44", "ATAPL-67979-00002"),
            ("2024-08-21T17:15:00", None,                  "ATAPL-67979-00004"),
        ],
    ),
    dict(
        refdes="RS03AXBS_MJ03A_12_VEL3DB301", sta="AXBA1",
        site="RSN Axial Base 1", comment="Axial Base Seafloor",
        s_lat="45.820213", s_lon="-129.736419", s_elev="-2609",
        deployments=[
            ("2014-08-08T17:11:00", "2016-07-12T00:00:00", "ATAPL-67979-00005"),
            ("2016-07-13T00:00:00", "CHAIN",               "ATAPL-67979-00008"),
            ("2019-07-07T03:39:43", None,                  "ATAPL-67979-00008"),
        ],
    ),
]


def resolve_ends(deps):
    """Replace 'CHAIN' end with the next deployment's start time."""
    starts = [d[0] for d in deps]
    out = []
    for i, (start, end, uid) in enumerate(deps):
        if end == "CHAIN":
            end = starts[i + 1]
        out.append((start, end, uid))
    return out


def write_station_file(st):
    deps = resolve_ends(st["deployments"])
    chans = [f"{c['cha']}_{LOC}" for c in CHANNELS]
    chan_list = "[" + ",".join(chans) + "]"
    data_types = "{" + ",".join(f"'{c['cha']}_{LOC}':'{c['var']}'" for c in CHANNELS) + "}"
    path = os.path.join(PARAM_DIR, st["refdes"] + ".txt")
    with open(path, "w") as f:
        f.write("##parameter file##\n\n")
        f.write(f"# {st['comment']}\n\n")
        f.write(f"net = OO                                 #Network Name\n")
        f.write(f"n_start = 2014-01-01T00:00:00            #Network_StartDate\n")
        f.write(f"n_end = None                             #Network_EndDate\n")
        f.write(f"n_restatus = open                        #Network_RestrictedStatus\n")
        f.write(f"descript = Ocean Observatories Initiative #Description\n")
        f.write(f"sta = {st['sta']}                            #Station Name\n")
        f.write(f"s_start = {deps[0][0]}         #Station_StartDate --> first deployment; epochs documented at channel level\n")
        f.write(f"s_restatus = open                        #Station_RestrictedStatus\n")
        f.write(f"s_lat = {st['s_lat']}                     #Station_Latitude (representative; mean of deployments)\n")
        f.write(f"s_lon = {st['s_lon']}                   #Station_Longitude (representative; mean of deployments)\n")
        f.write(f"s_elev = {st['s_elev']}                          #Station_Elevation (m, seafloor; representative)\n")
        f.write(f"s_site = {st['site']}              #Station_Site\n")
        f.write(f"s_end = None                             #Station_endDate\n")
        f.write(f"metadata_channels = {chan_list}   #Channel names --> all epochs\n")
        f.write(f"channels = {chan_list}            #Channel names (uniform across deployments)\n")
        f.write(f"data_types = {data_types}   #channel --> NetCDF variable name\n")
    return path


def write_channel_file(st, c):
    deps = resolve_ends(st["deployments"])
    starts = "; ".join(d[0] for d in deps)
    ends = "; ".join((d[1] if d[1] else "None") for d in deps)
    ids = "; ".join(d[2] for d in deps)
    rates = "; ".join("1.0" for _ in deps)
    path = os.path.join(PARAM_DIR, f"{st['refdes']}_{c['cha']}_{LOC}.txt")
    with open(path, "w") as f:
        f.write("##parameter file##\n\n")
        f.write(f"# {st['comment']}\n")
        f.write(f"# Station Name: {st['sta']}\n\n")
        f.write(f"cha = {c['cha']}                                  #Channel Name\n")
        f.write(f"c_start = {starts}    #Channel_StartDate --> deployment start dates\n")
        f.write(f"c_end = {ends}    #Channel_EndDate --> deployment end dates\n")
        f.write(f"c_loc = {LOC}                                    #Channel_LocationCode\n")
        f.write(f"c_lat = {st['s_lat']}                         #Channel_Latitude\n")
        f.write(f"c_lon = {st['s_lon']}                       #Channel_Longitude\n")
        f.write(f"c_elev = {st['s_elev']}                              #Channel_Elevation\n")
        f.write(f"c_dep = 0.0                                  #Channel_Depth\n")
        f.write(f"c_az = {c['az']}                                #Channel_Azimuth (degrees E of N)\n")
        f.write(f"c_dip = {c['dip']}                              #Channel_Dip (degrees down from horizontal; NOTE create_metadata.py currently ignores dip)\n")
        f.write(f"c_type = GEOPHYSICAL                         #Channel_type\n")
        f.write(f"c_sample_rate = {rates}    #Channel_SampleRate (Hz; 1 Hz confirmed)\n")
        f.write(f"c_description = {c['desc']}    #Channel_description\n")
        f.write(f"c_sensor = {SENSOR}    #Channel_sensor_description\n")
        f.write(f"c_id = {ids}    #Sensor(UID) per deployment\n")
        f.write(f"r_value = 1.0                                #Response_Sensitivity_Value (no unit conversion; already {c['r_out']})\n")
        f.write(f"r_frequency = 1.0                            #Response_Sensitivity_Frequency\n")
        f.write(f"r_input_units = {c['r_in']}                            #Response_Sensitivity_Input_Units\n")
        f.write(f"r_input_description = {c['r_in_d']}        #Response_Sensitivity_Input_Units_description\n")
        f.write(f"r_output_units = {c['r_out']}                           #Response_Sensitivity_Output_Units\n")
        f.write(f"r_output_description = {c['r_out_d']}       #Response_Sensitivity_Output_Units_description\n")
    return path


def main():
    written = []
    for st in STATIONS:
        written.append(write_station_file(st))
        for c in CHANNELS:
            written.append(write_channel_file(st, c))
    for p in written:
        print("wrote", os.path.relpath(p, os.path.dirname(__file__)))
    print(f"\n{len(written)} files written.")


if __name__ == "__main__":
    main()
