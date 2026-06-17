# VEL3D conversion — session resume / handoff

Last updated: 2026-06-16. Repo: `coszo-hub/sea-water-velocity` (SSH remote), branch `main`.
Working dir: `/home/coszo/sea-water-velocity/VEL3D-data-collection`.

## Goal
Convert `PREST-data-collection` (Tidal Seafloor Pressure) → **VEL3D-data-collection**
(3-D point-velocity current meters) for OOI RCA/Endurance. Produce StationXML for
EarthScope + miniSEED for DMC transfer. **Adjust the existing PREST scripts** for
VEL3D — do NOT write new ones (the one allowed addition, `make_vel3d_params.py`, is
a param-file generator and is kept). Tracked checklist: `CONVERSION_TODO.md`.

## Working conventions (IMPORTANT)
- **Git author is always `Maleen Kidiwela <seismic@uw.edu>`** (GitHub `MaleenKidiwela`).
  **Never add a Claude / AI co-author trailer** to commits or PRs.
- Read utilities + runbook **in full before acting**. Don't set metadata (rates, band
  codes) from one-off API probes — derive them the PREST way, from the investigator's
  `dt_true` on **delivered** data. (The sync M2M API decimates and once gave a wrong
  18 s temp rate; the async-delivered NetCDF the pipeline converts is the truth.)
- Credentials: `OOI_USERNAME`/`OOI_TOKEN`. The working `.ooi_env` lives at
  `/home/coszo/coszo-data-collection_old/.ooi_env` (gitignored). It was copied to the
  repo root `VEL3D-data-collection/.ooi_env` (mode 600) so the wrappers work.
  Load with: `set -a && source /home/coszo/coszo-data-collection_old/.ooi_env && set +a`.
- Conda env: `ooi_env` (run via `conda run -n ooi_env python ...`).

## The 5 stations
| Nick | Reference designator | Series | net/loc | Stream(s) | Channels @ rate | Deps |
|---|---|---|---|---|---|---|
| HYSB1 | RS01SLBS-MJ01A-12-VEL3DB101 | B (Nobska) | OO/20 | vel3d_b_sample | LOE/LON/LOZ/LKO @ 1 Hz (band L) | 3 |
| HYS14 | RS01SUM1-LJ01B-12-VEL3DB104 | B | OO/20 | vel3d_b_sample | LOE/LON/LOZ/LKO @ 1 Hz | 6 |
| AXBA1 | RS03AXBS-MJ03A-12-VEL3DB301 | B | OO/20 | vel3d_b_sample | LOE/LON/LOZ/LKO @ 1 Hz | 3 |
| SHBP | CE02SHBP-LJ01D-07-VEL3DC108 | C (Nortek) | OO/20 | vel3d_cd_velocity_data + vel3d_cd_system_data | MOE/MON/MOZ @ 8 Hz (band M) + LKO @ 1 Hz (band L) | 12 |
| OSBP | CE04OSBP-LJ01C-07-VEL3DC107 | C | OO/20 | (same two streams) | MOE/MON/MOZ @ 8 Hz + LKO @ 1 Hz | 12 |

Key facts (all data-verified via investigator `dt_true`):
- **VEL3D-B**: uniformly **1 Hz**, all stations/deployments (band L). No per-station rate
  split (unlike PREST AXBA1 which was 15 s).
- **VEL3D-C velocity**: **8 Hz** (`Δt_true=0.125 s`), band M → MOE/MON/MOZ. L1 vars
  `vel3d_c_eastward/northward/upward_turbulent_velocity`, declination-corrected to TRUE
  north (verified: L0→L1 = constant +14.18° = local declination) → E/N codes valid.
  (StationXML still writes `Azimuth=0` / no `Dip` — orientation is labelled by the
  code only, not asserted as a value; 2026-06-17 decision, matches PREST.)
- **VEL3D-C temp**: **1 Hz** (band L → LKO). Source var `temperature_centidegree`
  (centi-°C), needs **×0.01 → °C** in the pipeline. NOT in the velocity stream — it's a
  SECOND stream (`vel3d_cd_system_data`). NOTE: earlier wrongly set to UKO/band-U/0.0556
  from a sync probe; corrected to LKO/1 Hz in commit 5ccda57.
- Per-channel `c_stream` param key routes B (single stream) vs C (two streams).

## Commits so far (all on main, pushed)
- `742b7eb` Add VEL3D-C stations (SHBP, OSBP); make metadata path VEL3D-aware
- `5ccda57` Make data pipeline + investigator VEL3D-aware; fix VEL3D-C temp to LKO/1Hz
- `8061584` Create log_path dir in conversion script (fixes mseed2dmc startup crash)
- `ad95f08` Add VEL3D week-test timing plots; fix per-day figure dir stream collision
- `76e72df` Rename summary-plot folders to VEL3D-B / VEL3D-C

## Scripts adjusted (existing PREST scripts — edited, not created)
- `bin/OOI_data_request_and_convert_mseed.py` — `--stream` selector; per-channel
  `c_stream` routing; `temperature_centidegree ×0.01`; NCML-404 retry (aggregation lags
  behind status=complete); auto-mkdir mseed_path + log_path. Per-stream endtime cursor
  when `--stream` given.
- `bin/OOI_metadata.py` — sensor filter `['BOTPTA','PREST']` → `['VEL3D']`.
- `bin/create_metadata.py` — writes channel `azimuth` (now `0` for all channels) and
  does NOT write `Dip` (2026-06-17 decision: no authoritative orientation; matches
  PREST). It briefly wrote `dip=-90` on verticals; reverted.
- `bin/diagnose_timing.py` — STATIONS=VEL3D refs; `get_deployment_for_date(...,stream=)`
  picks channel by `c_stream` (no `*DO*` for VEL3D); `fetch_nc_timestamps(...,stream=)`
  builds stream tag/NCML from stream; NCML-404 retry.
- `bin/temporal_anomaly_investigator.py` — `--stream`; reads `run_vel3d.txt`; threads
  stream into deployment lookup + fetch + per-stream metrics CSV + per-day figure dir.
- `bin/make_vel3d_params.py` — (kept, the one new file) generates all param files +
  `param/run_metadata.txt` from the single `STATIONS` roster. Edit a dict + rerun to
  add a station. B and C series channel templates inside.

## How to run things
- **Metadata / StationXML** (PREST-identical): `bin/run_ooi_requests.sh all all seedlink`
  → `OOI_metadata.py` → per-station `OO_<STA>.xml` in `output/xml/`. (Or `create_metadata.py
  <REF_UNDERSCORE>` directly.) Needs `param/run_metadata.txt` (generated by make_vel3d_params).
- **Regenerate params + roster + StationXML**: `python bin/make_vel3d_params.py` then the
  metadata run.
- **Conversion**: `python bin/OOI_data_request_and_convert_mseed.py <REF-DASH> vel3d
  <seedlink|miniseed2dmc> [--stream <s>] [--save-nc]`. VEL3D-C needs `--stream
  vel3d_cd_velocity_data` and `--stream vel3d_cd_system_data` (two runs). For
  `miniseed2dmc`, set deployment in `param/run_vel3d.txt` (`deployment = N`); seedlink
  auto-uses latest. Output: seedlink→`output/mseed/`, mseed2dmc→`output/mseed2dmc/YYYY/`.
  Latest-deployment numbers: HYSB1=3, HYS14=6, AXBA1=3, SHBP=12, OSBP=12.
- **Investigator**: `python bin/temporal_anomaly_investigator.py --mode
  <single|collect|plot> --station <REF-DASH> --stream <s> [--date|--start/--end]
  [--only-gaps] [--save-nc]`. B stations need `--stream vel3d_b_sample`. Output in
  `output/temporal_anomaly/` (metrics CSV, per-day 4-panel figs, summary plots via
  `--mode plot`). Summary plots manually organized into `figures/summary/{VEL3D-B,
  VEL3D-C/velocity, VEL3D-C/temp}/` (plot_mode itself still writes flat `fig*.png`).

## Run config
- `param/run_vel3d.txt` (gitignored) — clone of PREST `run_prest.txt`. Key params:
  `time_interval=86400`, `data_endtime=NOW`, `gap_algo=anomaly`, `data_quality=D`,
  `delay=50` (status-poll wait per request — the main slowness), `deployment=N`
  (mseed2dmc only; the test runner sed-edits this per station — currently 12).
- `param/run_metadata.txt` (gitignored, generated) — `reference_id` + `base_url` for
  OOI_metadata.py.

## Test done (this session)
Week 2025-09-01..07, all 5 stations, `mseed2dmc` + `--save-nc`. **140 mseed** in
`output/mseed2dmc/2025/` verified: B 4ch×7d, C 3vel×7d + temp×7d. Spot-checks: C velocity
MOE 8 Hz ±1.2 m/s mean≈0; C temp LKO 1 Hz mean 5.638 °C (centi→°C works). Investigator
collect run produced variability CSVs + figures (committed in ad95f08).
StationXML for all 5 built + obspy-validated.

## Open items / next steps
- CONVERSION_TODO.md remaining: cron file `crons_*` (§5, rename + repoint paths/refs),
  `run/` production cursor seeding (§6), README rewrite (§7), final grep sweep + remove
  old PREST param files + inert BOTPT branches (§8), `sync_metrics.sh` retarget.
- Investigator efficiency: it RE-FETCHES from OOI even though we saved all `.nc` with
  `--save-nc` (4.1 GB in `output/netcdf/`). The right fix is to teach it to read local
  `.nc` instead of re-requesting — user flagged the redundancy. NOT done yet.
- VEL3D-C **per-day** figures were not committed (collided under old code, now fixed by
  the stream-in-dir change); regenerating clean ones currently needs a re-fetch.
- **OSBP temp** investigator CSV/plot has only 1 day (collect was cut to avoid the
  redundant re-download). Complete it (fast 1 Hz stream) or via local-nc fix.
- Conversion **pipeline-stats CSVs** (`output/metrics/*_vel3d_pipeline_stats.csv`, 5
  files) are untracked; PREST tracks these — decide whether to commit.
- Test artifacts NOT committed and safe to clean to reclaim disk: `output/mseed2dmc/`
  (330 MB), `output/netcdf/` (4.1 GB), `output/metrics/`, `output/diagnostics/`, `run/`
  cursor files. Disk: `/home` is a 67.5 GB LVM volume but check `df -h /home` — earlier
  the ext4 fs hadn't been grown into the LV (RAM 15 GB ok; if `/home` shows ~20 GB run
  `sudo resize2fs /dev/mapper/coszo--vg-home`).

## Gotchas
- NCML 404 race: async NCML lags behind status=complete; retry on 404 (added).
- `log_mseed2dmc/` must exist for mseed2dmc — script now auto-creates log_path.
- `pkill -f <pat>` self-matches the killing command's own args — kill by PID instead.
- OOI velocity has occasional spike/fill values (~12 m/s) — OOI data quality, pipeline is
  faithful; QC/outlier handling is separate (segregate_outlier_mseed.py etc.).
- mseed sampling_rate is the DATA-derived rate (gap algo), not nominal `c_sample_rate`;
  param rate/band is advisory metadata only (README line ~38).

## Memory files (in ~/.claude/.../memory/)
ooi-credentials-location, git-author-identity, no-claude-coauthor-in-commits,
read-fully-before-acting-vel3d.
