# PREST → VEL3D Conversion Checklist

This directory is a verbatim copy of `PREST-data-collection`. Everything below is
still pressure-instrument-specific (PREST = Tidal Seafloor Pressure / Tsunameter)
and must be adapted for **VEL3D** (3-D point-velocity meter) data collection.

Reference designators, the directory name, and file/dir names have all carried
over from PREST and need changing.

---

## 0. Decide scope first (blocking questions)

Before touching code, we need to nail down the VEL3D specifics. These drive
almost every change below:

- [x] **Which VEL3D instruments / reference designators** do we collect?
      **Answer — five current meters:**
      VEL3D-**B** (Nobska MAVS-4, RSN, 1 Hz):
      - `RS01SLBS-MJ01A-12-VEL3DB101` (HYSB1)
      - `RS01SUM1-LJ01B-12-VEL3DB104` (HYS14)
      - `RS03AXBS-MJ03A-12-VEL3DB301` (AXBA1)
      VEL3D-**C** (Nortek, Endurance cabled benthic, 8 Hz) — added 2026-06-15:
      - `CE02SHBP-LJ01D-07-VEL3DC108` (SHBP)
      - `CE04OSBP-LJ01C-07-VEL3DC107` (OSBP)
- [x] **OOI stream name(s)** for VEL3D (drives `stream_tag` + `ncml_url`,
      see §3). PREST uses `<run>_real_time`; BOTPT uses `botpt_<run>_sample`.
      **VEL3D-B: `vel3d_b_sample`** (method `streamed`), confirmed from OOI
      instrument metadata (Rutgers datareview for `RS01SLBS-MJ01A-12-VEL3DB101`).
      Carries `eastward/northward/upward_turbulent_velocity` (PD878/879/880) +
      `temperature` (PD440). NOTE: unlike PREST, the stream is a FIXED name, not
      `<run>_real_time` — the §3 branch must hardcode `vel3d_b_sample`, e.g.
      `stream_tag = "streamed/vel3d_b_sample?..."` and
      `ncml = "deployment%04i_%s-streamed-vel3d_b_sample.ncml"`.
      (`vel3d_b_engineering` also exists but is timing/cal only — not collected.)
      **VEL3D-C: `vel3d_cd_velocity_data`** (method `streamed`, 8 Hz) for velocity
      (`vel3d_c_eastward/northward/upward_turbulent_velocity`), **plus a SECOND
      stream `vel3d_cd_system_data`** (1 Hz) for temperature
      (`temperature_centidegree`, centi-°C → ×0.01 → °C). Confirmed live from M2M
      2026-06-15. So §3 needs TWO stream branches, and VEL3D-C stations pull two
      streams. Per-channel stream is now recorded in the param files as `c_stream`
      (added by `make_vel3d_params.py`) so the pipeline can route by channel
      instead of hardcoding one stream per run.
- [ ] **Channels & physical variables** VEL3D reports
      (eastward/northward/upward sea-water velocity, plus temp/heading/pitch/roll?).
      PREST reports `absolute_pressure` + `pressure_temp`.
- [ ] **SEED channel codes + location codes** to assign each VEL3D variable.
- [ ] **Sample rate** for VEL3D (PREST is 0.066667 Hz = 15 s period).
- [ ] **Output units + response sensitivity** (velocity is m/s; PREST converts
      PSI↔Pa via `r_value = 0.0001450377`).
- [x] Do we still need the `botpt_params/` material at all? **Dropped** — BOTPT
      is out of scope for `sea-water-velocity` (user confirmed 2026-06-09).
      `param/botpt_params/` and `run/botpt_endtimes/` deleted. Inert `lily`/`nano`
      (BOTPT) code branches still remain in the live scripts — see §4 note.

> Recommend answering §0 before doing the rest, since the param files and the
> main-script branches depend on these values.

---

## 1. Directory & repo naming

- [x] Copy `PREST-data-collection` → `VEL3D-data-collection` (done).
- [ ] Confirm the parent repo is `sea-water-velocity` everywhere (it is for the
      checkout, but several files still hardcode `Tidal-Seafloor-Pressure`).

## 2. Param files (`param/`)

- [x] Replace the three `RS..._PREST...` net/station param files with VEL3D
      reference designators — **done**: created **5** station files via
      `bin/make_vel3d_params.py` — 3 VEL3D-B (`RS01SLBS_MJ01A_12_VEL3DB101`,
      `RS01SUM1_LJ01B_12_VEL3DB104`, `RS03AXBS_MJ03A_12_VEL3DB301`) + 2 VEL3D-C
      (`CE02SHBP_LJ01D_07_VEL3DC108`, `CE04OSBP_LJ01C_07_VEL3DC107`, added
      2026-06-15). (Old PREST param files still present — remove on user OK.)
- [x] Replace the per-channel param files — **done**: 4 channels each.
      VEL3D-B: `LOE`/`LON`/`LOZ` velocity + `LKO` temp, band `L`, 1 Hz.
      VEL3D-C: `MOE`/`MON`/`MOZ` velocity (band `M`, 8 Hz) + `LKO` temp
      (band `L`, 1 Hz — rates verified from delivered data via the investigator's
      Δt_true). All loc `20`, `r_value=1.0`. Each channel file now
      carries `c_stream`. Codes per `OOI_channel_codes.md`.
      **Validated:** `create_metadata.py CE04OSBP_LJ01C_07_VEL3DC107` builds a
      48-channel StationXML (4 chan × 12 deployments) that passes obspy `validate=True`.
- [x] Update `data_types = {...}` mappings — **done**: velocity components →
      `eastward/northward/upward_turbulent_velocity`, temp → `temperature`.
- [x] **Code dep:** ~~uncomment dip in `create_metadata.py` so `LOZ` vertical gets
      `Dip=-90`~~ — **REVERSED (2026-06-17).** Decision is now `Azimuth=0` on all
      channels and **no `Dip`** (no authoritative orientation; matches PREST). `dip=`
      removed from the `Channel(...)` constructor; `c_dip` commented out in params.
      See `OOI_channel_codes.md` → "Azimuth / Dip metadata — DECISION (2026-06-17)".
- [ ] Verify chained `c_end` (AXBA1 dep2, HYS14 dep4 had no OOI stop time).
- [ ] Create the gitignored run config `param/run_vel3d.txt`
      (the analogue of the VM's `run_prest.txt` — `deployment`, `time_interval`,
      `trunc_time`, `data_quality`, gap-algorithm choice, etc.). **Note:**
      `param/run_prest.txt` and `run_metadata.txt` are gitignored, so they were
      never in the repo — confirm their schema from the VM before recreating.
- [x] Update `param/.gitignore` (`run_prest.txt` → `run_vel3d.txt`) — **done**.
- [x] Decide on `param/botpt_params/` — **deleted** (BOTPT out of scope).

## 3. Main pipeline script (`bin/OOI_data_request_and_convert_mseed.py`)

- [ ] Add/replace the run-name branch for stream URL construction:
      - `stream_tag` — line ~202 (`if 'prest' in run_name: ... _real_time`).
      - `ncml_url` — line ~319 (`deployment%04i_%s-streamed-%s_real_time.ncml`).
      Add a `vel3d` branch with the correct OOI stream name (from §0).
      - **VEL3D-B:** single stream `vel3d_b_sample`.
      - **VEL3D-C:** TWO streams — `vel3d_cd_velocity_data` (8 Hz velocity) AND
        `vel3d_cd_system_data` (1 Hz temperature). Prefer routing per channel via
        the new `c_stream` param key rather than one stream per run.
      - **VEL3D-C temp conversion:** `temperature_centidegree` is centi-°C; apply
        ×0.01 → °C in the unit-conversion step (line ~853). (Velocity is already L1
        m/s, no conversion.)
- [ ] Verify channel-list / `data_types` handling works with VEL3D's
      multi-component channels (the per-deployment `channels_<dep>` /
      `data_types_<dep>` lookups around lines 698–765).
- [ ] Update unit-conversion logic (line ~853 onward) for velocity units.
- [ ] Search for any remaining literal `prest` / `pressure` assumptions.

## 4. Other `bin/` scripts referencing PREST/pressure

> **BOTPT code branches still live here.** The config/state dirs are deleted, but
> inert `lily`/`nano` (BOTPT) branches remain in `OOI_data_request_and_convert_mseed.py`
> (stream_tag ~L205, ncml_url ~L322), `convert_mseed.py` (~L85, ~L166), and the
> `'BOTPTA'` filter word in `OOI_metadata.py` (~L64). Remove these when the
> prest→vel3d stream branches are rewritten in §3. Note `convert_mseed.py`'s
> ncml block is BOTPT-only (no prest path), so it needs a real rewrite, not just deletion.

Files that mention prest/pressure/botpt and need review:
- [ ] `OOI_metadata.py`
- [ ] `convert_mseed.py`
- [ ] `backfill_mseed_from_nc.py`
- [ ] `gap_algorithms.py`
- [ ] `diagnose_timing.py`
- [ ] `plot_from_netcdf.py`
- [ ] `plot_dt_true_outliers.py`
- [ ] `segregate_outlier_mseed.py`
- [ ] `compare_gap_algos.py`
- [ ] `temporal_anomaly_investigator.py`
- [ ] `sync_metrics.sh` — repoints metrics push to `coszo-hub/Tidal-Seafloor-Pressure`
      and hardcodes `PREST-data-collection/output/...` paths; retarget to
      `sea-water-velocity` + `VEL3D-data-collection/`.

## 5. Cron file (`crons_prest_seedlink_and_mseed2dmc.txt`)

- [ ] Rename → `crons_vel3d_seedlink_and_mseed2dmc.txt`.
- [ ] Replace hardcoded paths
      `/home/coszo/Tidal-Seafloor-Pressure/PREST-data-collection/`
      → the VEL3D checkout path.
- [ ] Replace reference designators, `prest` run-name args, station nicknames
      (HYSB1/HYS14/AXBA1), and log file names.

## 6. `run/` state files

- [ ] Replace `endtime_<ref>_prest.txt` / `..._prest_mseed2dmc.txt` with the
      VEL3D reference designators + `vel3d` run name.
- [x] Drop `run/botpt_endtimes/` — **deleted** (BOTPT out of scope).

## 7. README.md

- [ ] Rewrite overview/instrument description (Tidal Seafloor Pressure →
      sea-water velocity), units, sensor names, and the
      `temporal_anomaly_investigator.py` / `diagnose_timing.py` examples that
      reference pressure stations.

## 8. Misc / cleanup

- [ ] `bin/archive/` — legacy `OOI_data*.py` variants. Decide keep vs. delete
      (they are PREST-era scratch files; recommend dropping in the VEL3D repo).
- [ ] `testk/` (`pull_data.py`, `verify_mseed.py`) — update or remove.
- [ ] `.ooi_env.example` — verify still correct (credentials are instrument-agnostic; likely no change).
- [ ] Global grep sweep before finishing:
      `grep -rIi -E 'prest|botpt|pressure|tsunameter|psi|pascal|Tidal-Seafloor-Pressure|HYSB1|HYS14|AXBA1' .`
      should return nothing instrument-relevant.

---

## Suggested order of work

1. Answer §0 (instruments, streams, channels, units).
2. Param files (§2) — the source of truth the scripts read.
3. Main script branches (§3) + run-name plumbing.
4. Cron + run-state + sync (§5, §6, §4 `sync_metrics.sh`).
5. README (§7).
6. Cleanup + final grep sweep (§8).
