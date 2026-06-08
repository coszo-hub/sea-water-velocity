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

- [ ] **Which VEL3D instruments / reference designators** do we collect?
      (e.g. the OOI RCA VEL3D reference designators + their station nicknames).
      PREST currently uses:
      - `RS01SLBS-MJ01A-06-PRESTA101` (HYSB1)
      - `RS01SUM1-LJ01B-09-PRESTB102` (HYS14)
      - `RS03AXBS-MJ03A-06-PRESTA301` (AXBA1)
- [ ] **OOI stream name(s)** for VEL3D (drives `stream_tag` + `ncml_url`,
      see §3). PREST uses `<run>_real_time`; BOTPT uses `botpt_<run>_sample`.
      VEL3D will have its own stream name — confirm from the OOI data portal.
- [ ] **Channels & physical variables** VEL3D reports
      (eastward/northward/upward sea-water velocity, plus temp/heading/pitch/roll?).
      PREST reports `absolute_pressure` + `pressure_temp`.
- [ ] **SEED channel codes + location codes** to assign each VEL3D variable.
- [ ] **Sample rate** for VEL3D (PREST is 0.066667 Hz = 15 s period).
- [ ] **Output units + response sensitivity** (velocity is m/s; PREST converts
      PSI↔Pa via `r_value = 0.0001450377`).
- [ ] Do we still need the `botpt_params/` material at all? (Likely **drop** — it
      is pressure/tilt BOTPT config, unrelated to VEL3D.)

> Recommend answering §0 before doing the rest, since the param files and the
> main-script branches depend on these values.

---

## 1. Directory & repo naming

- [x] Copy `PREST-data-collection` → `VEL3D-data-collection` (done).
- [ ] Confirm the parent repo is `sea-water-velocity` everywhere (it is for the
      checkout, but several files still hardcode `Tidal-Seafloor-Pressure`).

## 2. Param files (`param/`)

- [ ] Replace the three `RS..._PREST...` net/station param files with VEL3D
      reference designators (net/station/lat/lon/elev/site, channel list,
      `data_types` map).
- [ ] Replace the per-channel param files (`*_UDO_10.txt`, `*_UK1_10.txt`, etc.)
      with VEL3D channels: new `cha`, `c_loc`, `c_sample_rate`, `c_description`,
      `c_sensor`, and the response block (`r_value`, `r_input_units`,
      `r_output_units`, descriptions) for **velocity**, not pressure.
- [ ] Update `data_types = {...}` mappings (currently
      `'absolute_pressure'`, `'pressure_temp'`).
- [ ] Create the gitignored run config `param/run_vel3d.txt`
      (the analogue of the VM's `run_prest.txt` — `deployment`, `time_interval`,
      `trunc_time`, `data_quality`, gap-algorithm choice, etc.). **Note:**
      `param/run_prest.txt` and `run_metadata.txt` are gitignored, so they were
      never in the repo — confirm their schema from the VM before recreating.
- [ ] Update `param/.gitignore` (`run_prest.txt` → `run_vel3d.txt`).
- [ ] Decide on `param/botpt_params/` — almost certainly delete for VEL3D.

## 3. Main pipeline script (`bin/OOI_data_request_and_convert_mseed.py`)

- [ ] Add/replace the run-name branch for stream URL construction:
      - `stream_tag` — line ~202 (`if 'prest' in run_name: ... _real_time`).
      - `ncml_url` — line ~319 (`deployment%04i_%s-streamed-%s_real_time.ncml`).
      Add a `vel3d` branch with the correct OOI stream name (from §0).
- [ ] Verify channel-list / `data_types` handling works with VEL3D's
      multi-component channels (the per-deployment `channels_<dep>` /
      `data_types_<dep>` lookups around lines 698–765).
- [ ] Update unit-conversion logic (line ~853 onward) for velocity units.
- [ ] Search for any remaining literal `prest` / `pressure` assumptions.

## 4. Other `bin/` scripts referencing PREST/pressure

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
- [ ] Drop `run/botpt_endtimes/` unless BOTPT is in scope (it isn't).

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
