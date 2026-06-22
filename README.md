# Sea Water Velocity

OOI RCA Tier-3 3-D single-point current-meter (VEL3D) data collection: retrieval,
validation, conversion to MiniSEED / StationXML, and staging for SeedLink
(near-real-time) or miniseed2dmc (historical backfill). Sibling of
[`absolute-seafloor-pressure`](https://github.com/coszo-hub/absolute-seafloor-pressure) (PREST);
same internal layout and pipeline.

The fleet spans **two instrument series**, which differ in vendor, sample rate, and
channel codes:

- **VEL3D Series C** — Nortek, **8 Hz**, on the Endurance array (`CE*`). Channels `MO?_20`. OOI streams `vel3d_cd_velocity_data` (velocity) and `vel3d_cd_system_data` (temperature).
- **VEL3D Series B** — Nobska MAVS-4, **1 Hz**, on the RSN cabled array (`RS*`). Channels `LO?_20`. OOI stream `vel3d_b_sample`.

## Stations

`?OE`/`?ON`/`?OZ` are eastward / northward / upward turbulent velocity (M/S); `LKO`
is the instrument temperature reading. `_20` is the SEED location code.

| Reference | Site | OO Net.Sta | Series (rate) | Velocity / temp channels |
|---|---|---|---|---|
| `CE02SHBP-LJ01D-07-VEL3DC108` | Endurance OR Shelf Cabled Benthic | `OO.SHBP` | C — Nortek (8 Hz) | `MOE_20`, `MON_20`, `MOZ_20`, `LKO_20` |
| `CE04OSBP-LJ01C-07-VEL3DC107` | Endurance OR Offshore Cabled Benthic | `OO.OSBP` | C — Nortek (8 Hz) | `MOE_20`, `MON_20`, `MOZ_20`, `LKO_20` |
| `RS01SLBS-MJ01A-12-VEL3DB101` | RSN Hydrate Slope Base | `OO.HYSB1` | B — Nobska MAVS-4 (1 Hz) | `LOE_20`, `LON_20`, `LOZ_20`, `LKO_20` |
| `RS01SUM1-LJ01B-12-VEL3DB104` | RSN Hydrate Summit 1-4 | `OO.HYS14` | B — Nobska MAVS-4 (1 Hz) | `LOE_20`, `LON_20`, `LOZ_20`, `LKO_20` |
| `RS03AXBS-MJ03A-12-VEL3DB301` | RSN Axial Base | `OO.AXBA1` | B — Nobska MAVS-4 (1 Hz) | `LOE_20`, `LON_20`, `LOZ_20`, `LKO_20` |

## Deployments

Per-station deployment count and span, with sample rate. Full per-deployment epochs
(`c_start` / `c_end`) live in each channel's `VEL3D-data-collection/param/*_?O?_20.txt`.

| Station | Series | Deployments | First start | Status | Sample rate |
|---|---|---|---|---|---|
| `OO.SHBP` | C | 12 | 2014-09-10 | ongoing | 8 Hz |
| `OO.OSBP` | C | 12 | 2014-08-15 | ongoing | 8 Hz |
| `OO.HYSB1` | B | 3 | 2014-09-13 | ongoing | 1 Hz |
| `OO.HYS14` | B | 6 | 2014-09-09 | ongoing | 1 Hz |
| `OO.AXBA1` | B | 3 | 2014-08-08 | ongoing | 1 Hz |

## Layout

```
sea-water-velocity/
├── README.md
├── .gitignore
└── VEL3D-data-collection/        ← pipeline code (see its README.md for full detail)
    ├── bin/                       ← *.py + *.sh: cron pipeline, metadata builder,
    │                                 backfill, gap_algorithms, diagnose_timing,
    │                                 temporal_anomaly_investigator, sync_metrics, etc.
    ├── param/                     ← run_vel3d.txt, run_metadata.txt, station +
    │                                 per-channel params (c_start/c_end, rates, streams)
    ├── run/                       ← endtime_*.txt pipeline state
    ├── crons_prest_seedlink_and_mseed2dmc.txt   ← inherited from PREST (VEL3D
    │                                 conversion pending — see CONVERSION_TODO.md)
    ├── testk/                     ← smoke-test scripts
    └── output/                    ← runtime working tree
        ├── mseed/                  ← seedlink MiniSEEDs (contents NOT tracked)
        ├── mseed2dmc/<YEAR>/       ← backfill MiniSEEDs (contents NOT tracked)
        ├── xml/                    ← StationXML (only OO_*.xml TRACKED)
        ├── netcdf/                 ← optional raw .nc audit copies (NOT tracked)
        ├── metrics/                ← per-run pipeline_stats CSVs (TRACKED)
        ├── diagnostics/            ← diagnose_timing figures / CSVs (TRACKED)
        └── temporal_anomaly/       ← temporal_anomaly_investigator output
            ├── metrics/             ← <STATION>_variability.csv (TRACKED)
            ├── figures/             ← per_day/ + summary/ PNGs (TRACKED)
            └── netcdf/              ← raw .nc when --save-nc (NOT tracked; *.nc ignored)
```

## Workflows

### Live data — SeedLink path

All operations run through wrapper scripts in `bin/` (`run_ooi_requests.sh` →
`run_data_collection.sh`) that activate the conda env, load OOI credentials from
`.ooi_env`, resolve cron-safe paths, and prevent concurrent runs. **Python scripts
are never called directly from cron.** The VM clones this repo and runs the staggered
SeedLink + metadata + latency + metrics-sync window, mirroring the PREST sibling.

> The current `crons_*.txt` is still the inherited PREST crontab (PREST stations /
> `Tidal-Seafloor-Pressure` paths). Converting it to the five VEL3D references is a
> pending task — see `VEL3D-data-collection/CONVERSION_TODO.md`.

### Historical — local backfill

`bin/backfill_mseed_from_nc.py` walks saved NetCDFs and produces MiniSEEDs in
`output/mseed2dmc/<YEAR>/`, byte-compatible with the cron pipeline. miniseed2dmc cron
entries are commented out — historical backfill is local-only.

### Daily metrics sync

`bin/sync_metrics.sh` runs once per day on the VM:

```
git pull --rebase --autostash
git add VEL3D-data-collection/output/metrics/ VEL3D-data-collection/output/diagnostics/
git commit -m "metrics: sync <date>"
git push origin main
```

`output/metrics/<reference>_vel3d_pipeline_stats.csv` and
`output/diagnostics/*_vel3d.txt` are tracked directly — no copy step.

## Algorithm

`gap_algo` in `param/run_vel3d.txt` selects between `legacy` (median Δt + adaptive
multiplier × sample-period threshold) and `anomaly` (OLS Δt_true + integer-step +
`true_missing > 0` splitting). **Default: `anomaly`** as of 2026-04-29.

Both algorithms live in `bin/gap_algorithms.py` behind a single `detect_gaps()`
dispatcher shared by the cron pipeline, the local backfill, the testk smoke-tests, and
the plotting tools. The pipeline uses an absolute Δt threshold for clean file
splitting; the offline `temporal_anomaly_investigator.py` applies a stricter
integer-step + wall-clock criterion for data-quality characterisation (it separates
real gaps from timestamp jitter and records both `n_gaps_raw` and corrected `n_gaps`).

See `VEL3D-data-collection/README.md` for the full pipeline reference, credential
setup, the offline diagnostic tools, and the `*_variability.csv` schema.

## Other instrument repos

Sibling instrument repos in the `coszo-hub` organization share this internal layout:

- `coszo-hub/absolute-seafloor-pressure/PREST-data-collection/` — seafloor pressure (PREST)
- `coszo-hub/sea-water-velocity/VEL3D-data-collection/` — current meters (VEL3D, this repo)
