# Session resume / handoff — VEL3D + PREST (updated 2026-10-02)

Read this first in a new session. It supersedes all older notes in this file
(the 2026-06 version referenced SHBP/OSBP, MOE/MON/MOZ, the coszo VM as the
processing host, "no Dip" — all obsolete).

## 0. Working conventions (IMPORTANT)
- Git author: Maleen Kidiwela. **Never add a Claude / AI co-author trailer** to commits
  or PRs (user's global rule overrides any harness attribution reminder).
- User wants **short, direct answers** (status + next command), no long option tables.
- Commands for the user's machines: macOS/zsh. zsh does NOT word-split `set -- $var`
  and errors on unmatched globs — use bash scripts / explicit args, `find … -delete`.
  Never `pkill -f <pattern>` with a pattern that appears in your own command line
  (it kills your shell) — use `pgrep -f '[x]yz' | xargs kill`.
- Claude Code safety check blocks `rm -rf $VAR/...` — use literal absolute paths.
- Before removing/overwriting user data: copy + verify (counts/bytes) first.

## 1. Machines & paths
- **This Mac (quakehunter)** = processing host since 2026-10-01.
  - VEL3D repo: `~/Documents/sea-water-velocity` (GitHub coszo-hub/sea-water-velocity, https)
  - PREST repo: `~/Documents/absolute-seafloor-pressure` (coszo-hub/absolute-seafloor-pressure, ssh;
    formerly Tidal-Seafloor-Pressure)
  - **COSZO RAID** `/Volumes/COSZO` (29 TB):
    `/Volumes/COSZO/VEL3D/{mseed2dmc,mseed2dmc_sent,outlier,netcdf_system_data,figures_per_day,goldcopy_cache,logs}`
    `/Volumes/COSZO/PREST/{mseed2dmc,outlier,goldcopy_cache,logs}`
  - Repo symlinks (local only, excluded via .git/info/exclude or .gitignore):
    VEL3D `output/mseed2dmc`, `output/mseed2dmc_sent`, `output/outlier`,
    `output/temporal_anomaly/netcdf`, `output/temporal_anomaly/figures/per_day`,
    `output/goldcopy_cache` → RAID; PREST `output/goldcopy_cache` → RAID.
  - OOI creds: `VEL3D-data-collection/.ooi_env` (gitignored, user-filled). PREST folder on this
    Mac has NO .ooi_env (VM has one).
  - conda envs: `ooi_env` (pipeline), `earthscope` (es CLI 1.2.0 + py-spy).
- **earthnote Mac** (~/Documents/sea-water-velocity): earlier B-station run with the OLD
  'gaps' segmenting — those MiniSEED must NOT be uploaded (delete after checking git status).
- **coszo VM** (/home/coszo): runs the live SeedLink path. Has ringserver (~/ringserver,
  ~/conf/ring.conf, ~/ring, ~/start_ringserver, ~/slinktool, slarchive, dalitool);
  /home was a 20 GB LV. A separate "VM agent" will do the VM-side setup.

## 2. Stations
| SEED | OOI refdes | Series | Rate | Channels (loc) |
|---|---|---|---|---|
| HYSB1 | RS01SLBS-MJ01A-12-VEL3DB101 | VEL3D-B | 1 Hz | LOE LON LOZ LKO (20) |
| HYS14 | RS01SUM1-LJ01B-12-VEL3DB104 | VEL3D-B | 1 Hz | LOE LON LOZ LKO (20) |
| AXBA1 | RS03AXBS-MJ03A-12-VEL3DB301 | VEL3D-B | 1 Hz | LOE LON LOZ LKO (20) |
| CZSHF | CE02SHBP-LJ01D-07-VEL3DC108 | VEL3D-C | 8 Hz vel / 1 Hz temp | MOU MOV MOW LKO (20) |
| CZOFF | CE04OSBP-LJ01C-07-VEL3DC107 | VEL3D-C | 8 Hz vel / 1 Hz temp | MOU MOV MOW LKO (**21**) |
- CZOFF uses loc **21 for all deployments** (20 reserved for a future NEW station at the
  site); all others loc 20. PREST stations HYSB1/HYS14/AXBA1 use loc 10 (UDO/UK1 or LDO/LK1).
- VEL3D-C streams: `vel3d_cd_velocity_data` (velocity) + `vel3d_cd_system_data`
  (= **temperature**, temperature_centidegree, conversion 100). VEL3D-B: one stream
  `vel3d_b_sample` (velocity + temperature). PREST: `prest_real_time`.
- **Deployment 13** added 2026-10-01 for CZSHF (from 2026-08-09T22:38) and CZOFF
  (2026-08-10T18:27) — params + StationXML regenerated (commit 7a55891).
- Metadata sample rates are **nominal** (1.0 / 8.0 Hz) — correct, user confirmed. MiniSEED
  headers carry the measured daily rate (1/Δt_true; B ≈ 0.99994 Hz). EarthScope MUSTANG
  `sample_rate_channel` tolerance is 1% (abs 0.01) — worst day 0.07%: OK, no change.

## 3. Key discoveries / rules now in the code (both repos)
1. **OOI gold copy** (`thredds.dataexplorer.oceanobservatories.org`, `ooigoldcopy/public/`,
   OPeNDAP, no credentials/queue) replaces M2M for VEL3D velocity, VEL3D-B and PREST
   (`--source goldcopy`); ~6–12 s/day. M2M full-day 8 Hz requests stalled > 30 min.
   Gold copy LACKS VEL3D-C temperature → M2M (`--source m2m`, one-pass) for that stream.
   Some gold-copy files fail over OPeNDAP (HTTP 500) → full-file download fallback cache.
2. **MiniSEED 'timing' segmenting** (default; `gaps` = old behaviour, byte-identical):
   new trace where timestamps leave the grid by > ½ sample → every sample within ½ sample
   of OOI time. The old method misplaced runs of samples (up to ~2 s) on ~40% of sampled days.
3. **Excess-sample cleanup**: OOI double ingestion / interleaved foreign records (CZOFF
   2015-10, 2018-09/10, 2019-04; CZSHF 2016-12 → 2019-04). On days with > 2% excess:
   drop off-grid records whose `internal_timestamp` ≠ time (rescue ones filling empty grid
   slots), then exact duplicates (keys: velocity raw counts + ensemble_counter;
   temperature date_time_string + temp/heading/pitch/roll/sound speed).
4. **Fragments**: `--min-piece-seconds 60` — traces < 60 s not written, longer kept
   (max 1440 files/channel/day); drops logged to `<RAID>/outlier/dropped_pieces.csv`.
5. Deployment lookup falls back to a deployment starting during the day (first partial days).
6. Retry semantics: only OOI-confirmed gaps (incl. 404 "No data for request", "All data
   removed by deployment mask") recorded as no-data; transient failures retried.
7. CSV appends add a missing trailing newline first (PREST CSVs had glued rows).
8. Figure titles: station names (`HYSB1 - VEL3D`, `CZSHF - temperature`, `HYSB1 - PREST`).

## 4. DONE
**VEL3D full regeneration (all 5 stations, 2014 → 2026-09-30)** on this Mac:
- MiniSEED: `/Volumes/COSZO/VEL3D/mseed2dmc/<YEAR>/` — 159 GB (AXBA1 27k, HYSB1 21k,
  HYS14 20k, CZSHF 263k, CZOFF 186k files). Per-trace files (user's choice; EarthScope
  merges day files on their end).
- 7 timing CSVs complete, no missing/duplicate days (commit cc24fc1). C-temperature
  doubled-day rows recomputed with dedupe (27 CZSHF, 7 CZOFF).
- dt_true outliers: 0 (worst day 0.07% off nominal) → nothing to segregate.
- Summary figures (committed): `figures/summary/VEL3D-B/`, `VEL3D-C/{velocity,temp}/`
  (fig2/fig3 per group), all-series `fig2_jitter_all.png`, `fig3_gap_count_all.png`,
  `fig1_dt_true_outliers.png` (the plain fig1 is no longer made; `--plain-dt-true` to force).
  Per-day figures (8,506 files) on RAID, untracked.
- README "Timing summary" section on both GitHub front pages embeds the 3 figures
  (fig1_dt_true_outliers, fig2_jitter[_all], fig3_gap_count[_all]).
**PREST**: already processed & SENT through 2026-05-01 (old code — do NOT change).
2026-05-02 → 2026-09-30 done with the ported new code: MiniSEED
`/Volumes/COSZO/PREST/mseed2dmc/2026/` (914 files, 492 MB); 152 rows appended per station
CSV (565aa38); summary figures force-tracked (PREST .gitignore ignores *.png).
**EarthScope upload DONE 2026-10-02** (Dropoff authorized that day):
- VEL3D StationXML (5 files, incl. dep 13; CZOFF loc 21) → `vel3d/stationxml/`, AUTHORIZED.
- VEL3D MiniSEED: all **517,242 files (159 GB, 2014 → 2026-09-30)** → `vel3d/mseed/<YEAR>/`;
  every year's server count verified = local count (AUTHORIZED/ACCEPTED, 0 FAILED). Files
  moved to `/Volumes/COSZO/VEL3D/mseed2dmc_sent/` (staging `mseed2dmc/` now empty).
- PREST 2026-05-02 → 09-30: 914 files → `prest/mseed/2026/`, all AUTHORIZED; moved to
  `/Volumes/COSZO/PREST/mseed2dmc_sent/`. PREST StationXML NOT resent (already at EarthScope).
- `bin/dropoff_earthscope.sh mseed [YEAR] --archive`: per-year batches with per-year locks
  (run years in parallel — each `es` process is capped at 10 S3 connections ≈ 12–24 files/s;
  4 in parallel ≈ 50 files/s); `find -H` (staging is a symlink); `--part-concurrency`.
  The `es` CLI crashes a whole batch on one transient error (NoSuchUpload, IncompleteBody,
  ExpiredToken) — just re-run; finished files are skipped (state in ~/.earthscope/default/dropoff).
  Never kill a batch mid-upload without deleting its incomplete state files.
  PREST repo has its own copy (7bece8a; `DROPOFF_MSEED_DIR=/Volumes/COSZO/PREST/mseed2dmc`).
**SeedLink repo side** (VEL3D 3be35e9, PREST f0b3983): see section 6.

## 5. TODO
1. (DONE 2026-10-02) EarthScope upload — see section 4.
2. **VM SeedLink setup** — the VM agent follows `VM_SEEDLINK_SETUP.md` (repo root of
   sea-water-velocity). Then: EarthScope must add the new VEL3D streams to their SeedLink
   client (after StationXML is in).
3. Optional: split the merged **2020-02-15** line in the 3 PREST variability CSVs
   (two rows glued, 51 fields) — user hasn't answered.
4. Delete the old earthnote-Mac B MiniSEED (after `git status` there); don't upload it.
5. CONVERSION_TODO.md / OOI_channel_codes.md still contain stale bits (SHBP/OSBP, MOE…) — tidy if wanted.
6. Gold-copy caches on RAID (75 GB VEL3D, 89 MB PREST) are scratch — delete when no longer useful.

## 6. SeedLink (near-real-time) design — repo side done, VM side pending
- `bin/run_daily_seedlink.sh <REFDES> <STREAM|-> <goldcopy|m2m>` (both repos): from cursor−7 d
  to yesterday (UTC) → `backfill_mseed_from_nc.py --variability --min-piece-seconds 60
  --flat --recent-retry-days 3 --mseed-dir output/mseed` (ringserver MSeedScan dir);
  then `bin/daily_alerts.py`; cursor `run/endtime_<REFDES>_<stream>.txt` (VEL3D) or
  `_prest.txt` (PREST), seeded **2026-10-01**, rewritten only on progress.
- `bin/daily_alerts.py` (replaces the old M2M pipeline emails; via bin/mail.py/mailx,
  recipient hardcoded in mail.py): crash, FAILED days, new gaps, rate deviation
  (run-file sp_alert_abs_floor / sp_alert_rel_frac), OOI extra records, > 10% fragmented,
  deployment ending (deploy_warn_days), OOI deployment count > params.
- `bin/detect.py` (unchanged): emails if any run/endtime_*.txt is > 24 h old.
- `bin/cleanup_seedlink.sh`: staged mseed 7 d, M2M nc 7 d, gold-copy cache 2 d, logs 180 d,
  per-day figures 90 d (skipped if per_day is a symlink).
- `bin/sync_metrics.sh` (repo-agnostic): pull, regenerate summary figures, commit CSVs +
  cursors + summary PNGs, push (VM needs a write deploy key for BOTH repos).
- Cron blocks: `VEL3D-data-collection/crons_vel3d_seedlink.txt` (7 series 18:01–18:15 UTC +
  metadata 18:25, detect 18:35, sync 18:45, cleanup 19:00) and
  `PREST-data-collection/crons_prest_seedlink.txt` (17:50–17:54, 18:20/18:30/18:40/19:05).
  **Install together** (one crontab): `cat prest vel3d | crontab -`.
- Tested end-to-end on this Mac in scratch (HYS14 gold copy, CZSHF temp m2m, PREST HYS14);
  alerts tested with a synthetic log.

## 7. Useful commands
- Backfill one series (gold copy, one pass → MiniSEED + CSV):
  `conda run -n ooi_env python bin/backfill_mseed_from_nc.py --source goldcopy --variability
  --min-piece-seconds 60 --station <REFDES> --stream <STREAM> --start A --end B`
- Summary figures: `python bin/temporal_anomaly_investigator.py --mode plot --all-series`
  (VEL3D) / `--mode plot` (PREST), then `python bin/plot_dt_true_outliers.py`.
- Outliers: `bin/find_dt_true_outliers.py` → `bin/segregate_outlier_mseed.py --dry-run`.
- Memory files: ~/.claude/projects/-Users-quakehunter-Documents-sea-water-velocity/memory/
