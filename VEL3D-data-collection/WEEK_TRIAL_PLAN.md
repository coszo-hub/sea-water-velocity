# VEL3D one-week download trial — plan

Goal: download **7 days** of data for all five VEL3D stations, convert to miniSEED,
and run the temporal-anomaly investigation — as a calibration + validation run
*before* any full historical backfill. This de-risks the pipeline changes and
replaces the storage *estimates* with measured bytes.

Trial window (suggested): a recent, fully-ingested week, e.g.
**2025-01-13 → 2025-01-20** (all 5 stations were active; avoids the live edge).

---

## 1. Storage — a week is tiny (no disk resize required)

Sizes from real M2M sample counts (miniSEED, float32; NetCDF is transient,
processed one 24 h window at a time and not retained):

| Station | Stream(s) | Rate | miniSEED / week |
|---|---|---|---|
| CE04OSBP (OSBP) | vel3d_cd_velocity_data (+system_data temp) | 8 Hz | ~58 MB |
| CE02SHBP (SHBP) | vel3d_cd_velocity_data (+system_data temp) | 8 Hz | ~58 MB |
| HYSB1 / HYS14 / AXBA1 | vel3d_b_sample | 1 Hz | ~10 MB each |
| **All 5, one week** | | | **~145 MB** |

- Transient NetCDF peak: ~70 MB (one VEL3D-C day) — never more than a day or two
  on disk if we don't `--save-nc`.
- Current free space (13 GB) is **~90× the weekly footprint**. The disk grow
  (`resize2fs` → ~60 GB) is **not needed for the trial** — only for a full
  multi-year backfill (~70 GB of mseed).

---

## 2. Prerequisites to build first (the trial can't run until these exist)

The download pipeline still has only the PREST/BOTPT stream branches.

1. **VEL3D download branch** in `bin/OOI_data_request_and_convert_mseed.py`:
   - Route each channel to its stream via the param `c_stream` key:
     - VEL3D-B → single stream `vel3d_b_sample`.
     - VEL3D-C → two streams: `vel3d_cd_velocity_data` (MOU/MOV/MOW, 8 Hz) and
       `vel3d_cd_system_data` (UKO temp, ~18 s).
   - `stream_tag` (~L202) and `ncml_url` (~L319): build per-stream instead of the
     hardcoded `*_real_time`.
   - Temperature unit fix (~L854): `temperature_centidegree` × 0.01 → °C
     (velocity is already L1 m/s, r_value = 1.0).
   - Confirm the multi-component / per-deployment `channels`/`data_types` handling
     (~L698–765) works with 4 VEL3D channels (it is param-driven, expected OK).
2. **`param/run_vel3d.txt`** (gitignored run config) — clone `run_prest.txt`
   schema: `time_interval=86400`, `data_endtime`, `data_quality=D`, `gap_algo`,
   alert thresholds, servers, etc.
3. **`run/` state files** — `endtime_<ref-dash>_vel3d.txt` seeded to the trial
   start date for each of the 5 stations (the pipeline advances these per cycle).

---

## 3. Run procedure (same workflow as PREST)

For each station, 7 daily cycles over the trial window, via the existing wrapper:

```
bin/run_ooi_requests.sh <REF-DASH> vel3d miniseed2dmc
```

e.g. `bin/run_ooi_requests.sh CE04OSBP-LJ01C-07-VEL3DC107 vel3d miniseed2dmc`.
Each invocation downloads the next 24 h window (driven by the `run/` endtime
state) and writes miniSEED to `output/mseed2dmc/YYYY/`.

Then flatten for transfer: `bin/flatten_mseed2dmc.sh`.

---

## 4. Verification / what to measure

- Actual NetCDF MB/day and miniSEED MB/day per station (replace §1 estimates).
- `obspy.read` a few mseed: confirm sample rate (8 Hz velocity, ~0.0556 Hz temp),
  channel codes (MOU/MOV/MOW/UKO), units sane (velocity ~±m/s, temp ~°C — the
  ÷100 working).
- `output/metrics/<station>_vel3d_pipeline_stats.csv` — gaps, sp deviation, etc.
- Optionally run `bin/temporal_anomaly_investigator.py` over the week to produce
  the anomaly metrics/figures (the "new files" used downstream).

---

## 5. Scope notes

- This is a **trial**, not the backfill. Full history (~11.7 yr) ≈ 70 GB mseed and
  ~hundreds of GB of NetCDF if retained — that needs the disk grow and/or batched
  download→mseed2dmc→cleanup. See `CONVERSION_TODO.md` §3.
- VEL3D-C 8 Hz dominates everything; the 1 Hz VEL3D-B stations are negligible.
