#!/usr/bin/env python3
"""
Generate VEL3D parameter files (station + per-channel) in ../param/.

Mirrors the PREST param-file schema (see read_param.py / create_metadata.py) for
the RCA / Endurance VEL3D 3-D point-velocity meters. Two instrument series are
supported:

  Series B — RSN Nobska MAVS-4 (cabled, 1 Hz). Stream `vel3d_b_sample`.
             Velocity + temperature all in the one science stream.
             SEED band L (1 Hz): LOE/LON/LOZ velocity, LKO temperature.

  Series C — Endurance Nortek "VEL3D Series C" (cabled benthic package).
             Velocity stream `vel3d_cd_velocity_data` at 8 Hz; temperature is
             NOT in it — it lives in `vel3d_cd_system_data` (temperature_centidegree,
             1 Hz). SEED band M (8 Hz): MOE/MON/MOZ velocity; band L (1 Hz) LKO
             temperature. Two streams per station. Rates VERIFIED from delivered
             data via temporal_anomaly_investigator.py Δt_true (velocity 0.125 s,
             temp 1.000 s; temp confirmed 1 Hz from 2016 through 2025, not the
             ~18 s an early synchronous-API probe wrongly suggested).

Sources of truth:
  - SEED codes/loc/net: OOI_channel_codes.md (Non-Tier 1 OOI Instrument Data Channel list)
  - Deployment dates/locations/UIDs: OOI M2M deployment events API
      (B pulled 2026-06-09; C pulled 2026-06-15)
  - Stream/vars/rate: OOI M2M metadata + Rutgers datareview
      (B: vel3d_b_sample, 1 Hz; C: vel3d_cd_velocity_data 8 Hz + vel3d_cd_system_data)

Decisions (see OOI_channel_codes.md):
  net=OO, loc=20 (uniform across all five current meters). r_value=1.0 (velocity
  is already L1 m/s, declination-corrected to true N). Series-C temperature source
  variable `temperature_centidegree` is in centi-degrees C and must be scaled x0.01
  -> degC in the pipeline (the StationXML output units are degC; see §3 TODO).

NOTE on c_end "None" mid-sequence: OOI sometimes returns no stop time for a
non-final deployment. Where that happens we chain c_end to the next deployment's
start (marked CHAIN below) so StationXML epochs stay valid. Verify against
deployment records if exact recovery times matter. (The Series-C records came back
fully chained — no gaps.)
"""
import os

PARAM_DIR = os.path.join(os.path.dirname(__file__), "..", "param")

LOC = "20"

# OOI M2M deployment-inventory base, consumed by OOI_metadata.py as `base_url`
# (it appends /{subsite}/{node}[/{sensor}[/{deployment}]]).
BASE_URL = "https://ooinet.oceanobservatories.org/api/m2m/12587/events/deployment/inv"

# ── Series B channels: Nobska MAVS-4, 1 Hz, all vars in vel3d_b_sample ──────────
# cha, netcdf_var, description, azimuth, dip, sample rate (Hz), stream, response units
CHANNELS_B = [
    dict(cha="LOE", var="eastward_turbulent_velocity",
         desc="eastward_turbulent_velocity, Eastward Sea Water Velocity",
         az="90.0", dip="0.0", rate="1.0", stream="vel3d_b_sample",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="LON", var="northward_turbulent_velocity",
         desc="northward_turbulent_velocity, Northward Sea Water Velocity",
         az="0.0", dip="0.0", rate="1.0", stream="vel3d_b_sample",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="LOZ", var="upward_turbulent_velocity",
         desc="upward_turbulent_velocity, Upward Sea Water Velocity",
         az="0.0", dip="-90.0", rate="1.0", stream="vel3d_b_sample",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="LKO", var="temperature",
         desc="temperature, Seawater Temperature",
         az="0.0", dip="0.0", rate="1.0", stream="vel3d_b_sample",
         r_in="C", r_in_d="degrees Celsius", r_out="C", r_out_d="degrees Celsius"),
]

# ── Series C channels: Nortek VEL3D-C. Velocity 8 Hz (band M) in
#    vel3d_cd_velocity_data; temperature 1 Hz (band L) in vel3d_cd_system_data. ──
CHANNELS_C = [
    dict(cha="MOE", var="vel3d_c_eastward_turbulent_velocity",
         desc="vel3d_c_eastward_turbulent_velocity, Eastward Sea Water Velocity",
         az="90.0", dip="0.0", rate="8.0", stream="vel3d_cd_velocity_data",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="MON", var="vel3d_c_northward_turbulent_velocity",
         desc="vel3d_c_northward_turbulent_velocity, Northward Sea Water Velocity",
         az="0.0", dip="0.0", rate="8.0", stream="vel3d_cd_velocity_data",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    dict(cha="MOZ", var="vel3d_c_upward_turbulent_velocity",
         desc="vel3d_c_upward_turbulent_velocity, Upward Sea Water Velocity",
         az="0.0", dip="-90.0", rate="8.0", stream="vel3d_cd_velocity_data",
         r_in="M/S", r_in_d="meters per second", r_out="M/S", r_out_d="meters per second"),
    # Temperature: 1 Hz (band L), source var temperature_centidegree (centi-degC; x0.01 -> degC in pipeline).
    dict(cha="LKO", var="temperature_centidegree",
         desc="temperature, Seawater Temperature (source temperature_centidegree, centi-degC; x0.01 -> degC)",
         az="0.0", dip="0.0", rate="1.0", stream="vel3d_cd_system_data",
         r_in="C", r_in_d="degrees Celsius", r_out="C", r_out_d="degrees Celsius"),
]

SENSOR_B = "3-D Single Point Velocity Meter (Nobska MAVS-4): VEL3D Series B"
SENSOR_C = "3-D Single Point Velocity Meter (Nortek): VEL3D Series C"

SERIES = {
    "B": dict(channels=CHANNELS_B, sensor=SENSOR_B),
    "C": dict(channels=CHANNELS_C, sensor=SENSOR_C),
}

# ── Per-station metadata. deployments = list of (start, end, uid) ──────────────
# end=None means ongoing/last; "CHAIN" means use next deployment's start (OOI gap).
STATIONS = [
    dict(
        series="B",
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
        series="B",
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
        series="B",
        refdes="RS03AXBS_MJ03A_12_VEL3DB301", sta="AXBA1",
        site="RSN Axial Base 1", comment="Axial Base Seafloor",
        s_lat="45.820213", s_lon="-129.736419", s_elev="-2609",
        deployments=[
            ("2014-08-08T17:11:00", "2016-07-12T00:00:00", "ATAPL-67979-00005"),
            ("2016-07-13T00:00:00", "CHAIN",               "ATAPL-67979-00008"),
            ("2019-07-07T03:39:43", None,                  "ATAPL-67979-00008"),
        ],
    ),
    # ── Endurance VEL3D Series C (added 2026-06-15; M2M deployment records) ──────
    dict(
        series="C",
        refdes="CE02SHBP_LJ01D_07_VEL3DC108", sta="SHBP",
        site="Endurance OR Shelf Cabled Benthic", comment="Oregon Shelf Cabled Benthic Experiment Package",
        s_lat="44.637165", s_lon="-124.305849", s_elev="-81",
        deployments=[
            ("2014-09-10T15:43:00", "2015-08-01T00:00:00", "ATOSU-69829-00003"),
            ("2015-08-02T05:30:00", "2016-07-21T00:00:00", "ATOSU-69829-00001"),
            ("2016-07-22T22:50:00", "2017-09-09T03:30:00", "ATOSU-69829-00003"),
            ("2017-09-10T14:30:00", "2018-06-29T02:30:00", "ATOSU-69829-00001"),
            ("2018-06-30T02:30:00", "2019-06-19T23:31:00", "ATOSU-69829-00003"),
            ("2019-06-23T03:36:00", "2020-08-12T21:24:00", "ATOSU-69829-00001"),
            ("2020-08-25T17:34:05", "2021-08-04T20:20:00", "ATOSU-69829-00003"),
            ("2021-08-23T19:27:00", "2022-08-18T23:51:00", "ATOSU-69829-00001"),
            ("2022-08-19T05:55:00", "2023-08-14T19:20:00", "ATOSU-69829-00003"),
            ("2023-08-22T14:50:00", "2024-08-15T20:13:00", "ATOSU-69829-00001"),
            ("2024-08-19T14:55:00", "2025-08-15T00:20:00", "ATOSU-69829-00003"),
            ("2025-08-28T15:17:00", None,                  "ATOSU-69829-00001"),
        ],
    ),
    dict(
        series="C",
        refdes="CE04OSBP_LJ01C_07_VEL3DC107", sta="OSBP",
        site="Endurance OR Offshore Cabled Benthic", comment="Oregon Offshore Cabled Benthic Experiment Package",
        s_lat="44.369407", s_lon="-124.953627", s_elev="-581",
        deployments=[
            ("2014-08-15T00:12:00", "2015-08-02T00:00:00", "ATOSU-69829-00004"),
            ("2015-08-03T22:00:00", "2016-07-21T00:00:00", "ATOSU-69829-00002"),
            ("2016-07-22T22:50:00", "2017-08-10T04:00:00", "ATOSU-69829-00004"),
            ("2017-08-10T16:00:00", "2018-06-25T00:00:00", "ATOSU-69829-00002"),
            ("2018-06-25T00:00:00", "2019-06-19T04:17:00", "ATOSU-69829-00004"),
            ("2019-06-23T22:52:00", "2020-08-18T23:40:00", "ATOSU-69829-00002"),
            ("2020-08-19T02:23:26", "2021-08-04T20:20:00", "ATOSU-69829-00004"),
            ("2021-08-23T22:54:08", "2022-08-19T06:13:00", "ATOSU-69829-00002"),
            ("2022-08-20T02:22:00", "2023-08-15T05:00:00", "ATOSU-69829-00004"),
            ("2023-08-29T00:50:00", "2024-08-15T07:25:00", "ATOSU-69829-00002"),
            ("2024-08-22T15:10:00", "2025-08-27T19:23:00", "ATOSU-69829-00004"),
            ("2025-08-28T03:38:00", None,                  "ATOSU-69829-00002"),
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
    channels = SERIES[st["series"]]["channels"]
    chans = [f"{c['cha']}_{LOC}" for c in channels]
    chan_list = "[" + ",".join(chans) + "]"
    data_types = "{" + ",".join(f"'{c['cha']}_{LOC}':'{c['var']}'" for c in channels) + "}"
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
    sensor = SERIES[st["series"]]["sensor"]
    starts = "; ".join(d[0] for d in deps)
    ends = "; ".join((d[1] if d[1] else "None") for d in deps)
    ids = "; ".join(d[2] for d in deps)
    rates = "; ".join(c["rate"] for _ in deps)
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
        f.write(f"c_dip = {c['dip']}                              #Channel_Dip (degrees down from horizontal)\n")
        f.write(f"c_type = GEOPHYSICAL                         #Channel_type\n")
        f.write(f"c_sample_rate = {rates}    #Channel_SampleRate (Hz)\n")
        f.write(f"c_stream = {c['stream']}    #OOI stream carrying this channel's variable\n")
        f.write(f"c_description = {c['desc']}    #Channel_description\n")
        f.write(f"c_sensor = {sensor}    #Channel_sensor_description\n")
        f.write(f"c_id = {ids}    #Sensor(UID) per deployment\n")
        f.write(f"r_value = 1.0                                #Response_Sensitivity_Value (no metadata scaling; already {c['r_out']})\n")
        f.write(f"r_frequency = 1.0                            #Response_Sensitivity_Frequency\n")
        f.write(f"r_input_units = {c['r_in']}                            #Response_Sensitivity_Input_Units\n")
        f.write(f"r_input_description = {c['r_in_d']}        #Response_Sensitivity_Input_Units_description\n")
        f.write(f"r_output_units = {c['r_out']}                           #Response_Sensitivity_Output_Units\n")
        f.write(f"r_output_description = {c['r_out_d']}       #Response_Sensitivity_Output_Units_description\n")
    return path


def write_run_metadata():
    """Emit param/run_metadata.txt (reference_id + base_url) from STATIONS.

    This is the roster consumed by OOI_metadata.py. Generating it here keeps the
    station list single-sourced: add a dict to STATIONS and it propagates.
    """
    pairs, seen = [], set()
    for st in STATIONS:
        subsite, node = st["refdes"].split("_")[:2]
        key = (subsite, node)
        if key not in seen:
            seen.add(key)
            pairs.append([subsite, node])
    ref_literal = "[" + ",".join(f'["{s}","{n}"]' for s, n in pairs) + "]"
    path = os.path.join(PARAM_DIR, "run_metadata.txt")
    with open(path, "w") as f:
        f.write("##parameter file##\n\n")
        f.write("# Generated by make_vel3d_params.py — roster for OOI_metadata.py.\n")
        f.write("# Edit STATIONS in make_vel3d_params.py and re-run; do not hand-edit.\n\n")
        f.write(f"reference_id = {ref_literal}    #[[subsite,node],...] for each VEL3D station\n")
        f.write(f"base_url = {BASE_URL}    #OOI M2M deployment-inventory base\n")
    return path


def main():
    written = []
    for st in STATIONS:
        written.append(write_station_file(st))
        for c in SERIES[st["series"]]["channels"]:
            written.append(write_channel_file(st, c))
    written.append(write_run_metadata())
    for p in written:
        print("wrote", os.path.relpath(p, os.path.dirname(__file__)))
    print(f"\n{len(written)} files written.")


if __name__ == "__main__":
    main()
