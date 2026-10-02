# VM SeedLink setup — VEL3D + PREST (handoff for the agent on the coszo VM)

Goal: run the **near-real-time (SeedLink) path** for both OOI instrument repos on the
coszo VM. Each day a cron job produces yesterday's MiniSEED (same code and rules as the
historical backfill), stages it in `output/mseed/`, and the existing **ringserver**
(MSeedScan) loads it into the ring, where EarthScope's SeedLink client pulls it.

Everything on the repo side is done (cron blocks, daily wrapper, cursors, alerts,
cleanup, metrics sync). This file lists what has to happen **on the VM**.

| Repo (GitHub, coszo-hub) | Instrument folder | Stations (SEED, net OO) |
|---|---|---|
| `sea-water-velocity` | `VEL3D-data-collection/` | HYSB1, HYS14, AXBA1 (loc 20, `LOE LON LOZ LKO`, 1 Hz); CZSHF (loc 20) and CZOFF (**loc 21**) (`MOU MOV MOW` 8 Hz, `LKO` 1 Hz) |
| `absolute-seafloor-pressure` | `PREST-data-collection/` | HYSB1, HYS14, AXBA1 (loc 10, pressure `?DO` + temperature `?K1`) |

Both instruments share station codes (HYSB1/HYS14/AXBA1) but differ by location code
(10 = PREST, 20 = VEL3D), so one ringserver serves both without conflict.

## How the daily job works (for orientation)

`bin/run_daily_seedlink.sh <REFDES> <STREAM|-> <goldcopy|m2m>` (one cron line per series):
1. reads its cursor `run/endtime_<REFDES>_<stream or run>.txt` (the next day to produce);
2. runs `bin/backfill_mseed_from_nc.py --variability --min-piece-seconds 60 --flat` for
   every day from cursor − 7 days to **yesterday (UTC)**, writing MiniSEED into
   `output/mseed/` and one timing-CSV row per day (`output/temporal_anomaly/metrics/`);
   days already in the CSV are skipped, so failed days are retried automatically;
3. data source: the **OOI gold copy** (THREDDS/OPeNDAP, no credentials, no request queue)
   for VEL3D velocity, VEL3D-B and PREST; **OOI M2M** (credentials) for VEL3D-C
   temperature, which the gold copy does not carry;
4. emails (via `bin/mail.py` → `mailx`) only if something needs attention
   (`bin/daily_alerts.py`): crash, failed/no-data days, sample-rate deviation, OOI
   duplicate/foreign records removed, heavy fragmentation, deployment ending, or a new
   OOI deployment missing from the param files;
5. advances the cursor; `bin/detect.py` emails if any cursor goes > 24 h without moving.

Cursors are pre-seeded in git to **2026-10-01** (both instruments are complete through
2026-09-30), so the first cron run produces 2026-10-01 → yesterday.

## Steps on the VM

### 0. Snapshot what is there now
```bash
crontab -l > ~/crontab.backup.$(date +%Y%m%d)
cp ~/conf/ring.conf ~/conf/ring.conf.backup.$(date +%Y%m%d)
df -h /home            # /home was a 20 GB LV — see "Disk" below
```

### 1. Clones
Find the existing clones (`ls ~`; `git -C <dir> remote -v`). The PREST repo was renamed
from `Tidal-Seafloor-Pressure` to `absolute-seafloor-pressure`; an old clone still works
(GitHub redirects) — either keep its path or re-clone. Note both instrument-folder paths;
they go into the cron blocks and ring.conf below.
```bash
cd ~/sea-water-velocity && git pull           # or clone coszo-hub/sea-water-velocity
cd <PREST clone> && git pull                  # coszo-hub/absolute-seafloor-pressure
```
If either working tree has local edits, inspect them before pulling (`git status`,
`git stash`). The cursors in `run/` are tracked: the pulled versions (2026-10-01) are the
correct starting point.

### 2. Conda env and credentials
```bash
conda env update -n ooi_env -f ~/sea-water-velocity/VEL3D-data-collection/bin/environment.yml
```
Each instrument folder needs `.ooi_env` (mode 600, gitignored) with `OOI_USERNAME` and
`OOI_TOKEN` — copy the existing one used by the PREST pipeline into
`VEL3D-data-collection/.ooi_env` if it isn't there.

### 3. Dry run one series per instrument (before cron)
```bash
cd ~/sea-water-velocity/VEL3D-data-collection
bin/run_daily_seedlink.sh RS01SUM1-LJ01B-12-VEL3DB104 vel3d_b_sample goldcopy
ls output/mseed | head; cat run/endtime_RS01SUM1-LJ01B-12-VEL3DB104_vel3d_b_sample.txt
cd <PREST clone>/PREST-data-collection
bin/run_daily_seedlink.sh RS01SUM1-LJ01B-09-PRESTB102 - goldcopy
```
Expect one MiniSEED set per day since 2026-10-01 in `output/mseed/` (flat, no year
folders) and the cursor moved to today. Also check that email works on the VM:
`echo test | mailx -s "coszo seedlink test" -r coszo@uw.edu <recipient in bin/mail.py>`.

### 4. ringserver: scan both staging folders
The VM already has `~/ringserver`, `~/conf/ring.conf`, `~/ring/`, `~/start_ringserver`.
In `ring.conf`, the MSeedScan line(s) currently point at the old PREST folder. Replace
them with one line per instrument (adjust paths to step 1):
```
MSeedScan /home/coszo/<PREST clone>/PREST-data-collection/output/mseed/ StateFile=/home/coszo/ring/scan_prest.state InitCurrentState=y
MSeedScan /home/coszo/sea-water-velocity/VEL3D-data-collection/output/mseed/ StateFile=/home/coszo/ring/scan_vel3d.state InitCurrentState=y
```
`InitCurrentState=y` means files already present when ringserver (re)starts are treated
as seen. **Restart ringserver BEFORE the dry-run files exist**, or temporarily use
`InitCurrentState=n` for the first start so the 2026-10-01+ files from step 3 are loaded.
Check `RingSize` — VEL3D-C adds ~20 MB/day per 8 Hz station; the ring should hold at
least several days of all streams (≥ 1 GB is comfortable).

Restart with the existing `~/start_ringserver` (stop the running instance first), then:
```bash
~/slinktool/slinktool -Q localhost:18000 | grep -E 'OO +(HYSB1|HYS14|AXBA1|CZSHF|CZOFF)'
```
VEL3D streams should appear (e.g. `OO HYS14 20 LOE D`, `OO CZOFF 21 MOU D`).

### 5. Cron (one crontab holds both blocks)
Edit the path variable at the top of each block (`VEL3D=…`, `PREST=…`), then:
```bash
cat <PREST clone>/PREST-data-collection/crons_prest_seedlink.txt \
    ~/sea-water-velocity/VEL3D-data-collection/crons_vel3d_seedlink.txt | crontab -
crontab -l
```
Schedule (UTC): PREST 17:50–17:55, VEL3D 18:01–18:15, metadata checks, `detect.py`,
`sync_metrics.sh` (18:40 PREST / 18:45 VEL3D), cleanup 19:00.

### 6. Git push from the VM
`bin/sync_metrics.sh` commits the timing CSVs, cursors and regenerated summary figures
(the GitHub README "Timing summary" embeds those figures) and pushes to `main`. The VM
needs a deploy key with **write** access to **both** repos (the old one only covered
`Tidal-Seafloor-Pressure`).

### 7. EarthScope
EarthScope's SeedLink client must be told to pull the **new VEL3D streams** (it only
expects PREST today): network `OO`, stations HYSB1/HYS14/AXBA1 loc `20` channels
`LOE LON LOZ LKO`; CZSHF loc `20` and CZOFF loc `21` channels `MOU MOV MOW LKO`. The
VEL3D StationXML must reach EarthScope first (Dropoff — authorization pending,
`bin/dropoff_earthscope.sh xml`). Their client connects to this VM's SeedLink port
(18000 by default — check `ring.conf`) the same way it does for PREST; confirm the
firewall rule that admits EarthScope is still in place after any ringserver changes.

## Disk
Staged MiniSEED is deleted after 7 days (`bin/cleanup_seedlink.sh`, 19:00 UTC), M2M
NetCDFs after 7 days, gold-copy fallback downloads after 2 days, per-day figures after
90 days. Steady state ≈ 0.4 GB for both instruments. Watch `df -h /home` in
`log/cleanup_seedlink.log`.

## Logs and where to look
- `log/seedlink_<STA>_<stream>_<YYYYMM>.log` (VEL3D), `log/seedlink_<STA>_<YYYYMM>.log` (PREST)
- `log/system.log` (detect.py), `log/sync_metrics.log`, `log/cleanup_seedlink.log`
- ringserver's own log in `~/ring/` or as configured in `ring.conf`

## Rollback
`crontab ~/crontab.backup.<date>` and restore `~/conf/ring.conf.backup.<date>`, restart
ringserver. The repos' old M2M live pipeline (`bin/run_ooi_requests.sh … seedlink`) is
still present and unchanged if it is ever needed.
