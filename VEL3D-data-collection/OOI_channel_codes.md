# Non-Tier 1 OOI Instrument Data Channel List

Authoritative reference for SEED network/station/location/channel codes for the
OOI RCA seafloor instruments. Source: COSZO "Non-Tier 1 OOI Instrument Data
Channel" list (provided by user). **VEL3D rows are the scope of this repo** —
the three RSN VEL3D-**B** (Nobska MAVS-4, 1 Hz) plus the two Endurance cabled
VEL3D-**C** (Nortek, 8 Hz) at `CZSHF`/`CZOFF`.

SEED channel = `[band][instrument][orientation]`.

---

## Slope Base — Station `HYSB1` ("RSN Hydrate Slope Base")

| Instrument | Measurement | Reference Designator | Net | Sta | Loc | SEED Channel | Notes |
|---|---|---|---|---|---|---|---|
| BB Seismometer + Hydrophone *(existing)* | Velocity (m/s), Accel (m/s²) | — | OO | HYSB1 | — | BHE, BHN, BHZ | [IRIS MDA](https://ds.iris.edu/mda/OO/HYSB1/) |
| Pressure | Pressure (PSI) | `RS01SLBS-MJ01A-06-PRESTA101` | OO | HYSB1 | 10 | UDO, LDO | PSI→Pa. Sample period = 1 s. |
| Pressure | Internal Temp (°C) | `RS01SLBS-MJ01A-06-PRESTA101` | OO | HYSB1 | 10 | UK1, LK1 | `1` = cabinet/internal source |
| **3-D Single Point Velocity** | **Eastward Vel (m/s)** | `RS01SLBS-MJ01A-12-VEL3DB101` | OO | HYSB1 | **20** | **`[L?]O2` / `[L?]OE`** | ⚠ orientation — see below |
| **3-D Single Point Velocity** | **Northward Vel (m/s)** | `RS01SLBS-MJ01A-12-VEL3DB101` | OO | HYSB1 | **20** | **`[L?]O1` / `[L?]ON`** | ⚠ orientation — see below |
| **3-D Single Point Velocity** | **Upward Vel (m/s)** | `RS01SLBS-MJ01A-12-VEL3DB101` | OO | HYSB1 | **20** | **`[L?]OZ`** | Z if within 5° of true vertical |
| **3-D Single Point Velocity** | **Seawater Temp (°C)** | `RS01SLBS-MJ01A-12-VEL3DB101` | OO | HYSB1 | **20** | **`[L?]KO`** | `K`=Temp, `O`=Outside |

---

## Southern Hydrate Ridge — Station `HYS14` ("RSN Hydrate Summit 1-4")

> Note: HYS11/HYS12/HYS13 short-period seismometers and the HYS14 BB
> seismometer+hydrophone are existing (EHE/EHN/EHZ, BHE/BHN/BHZ); RCA did not use
> location codes for those. [IRIS MDA HYS14](https://ds.iris.edu/mda/OO/HYS14/)

| Instrument | Measurement | Reference Designator | Net | Sta | Loc | SEED Channel | Notes |
|---|---|---|---|---|---|---|---|
| Pressure | Pressure (PSI) | `RS01SUM1-LJ01B-09-PRESTB102` | OO | HYS14 | 10 | UDO, LDO | PSI→Pa. Sample period = 1 s. |
| Pressure | Internal Temp (°C) | `RS01SUM1-LJ01B-09-PRESTB102` | OO | HYS14 | 10 | UK1, LK1 | `1` = cabinet/internal source |
| **3-D Single Point Velocity** | **Eastward Vel (m/s)** | `RS01SUM1-LJ01B-12-VEL3DB104` | OO | HYS14 | **20** | **`[L?]O2` / `[L?]OE`** | ⚠ orientation — see below |
| **3-D Single Point Velocity** | **Northward Vel (m/s)** | `RS01SUM1-LJ01B-12-VEL3DB104` | OO | HYS14 | **20** | **`[L?]O1` / `[L?]ON`** | ⚠ orientation — see below |
| **3-D Single Point Velocity** | **Upward Vel (m/s)** | `RS01SUM1-LJ01B-12-VEL3DB104` | OO | HYS14 | **20** | **`[L?]OZ`** | Z if within 5° of true vertical |
| **3-D Single Point Velocity** | **Seawater Temp (°C)** | `RS01SUM1-LJ01B-12-VEL3DB104` | OO | HYS14 | **20** | **`[L?]KO`** | `K`=Temp, `O`=Outside |

---

## Axial Base Seafloor — Station `AXBA1` ("RSN Axial Base 1")

> Note: the PREST pressure instrument here samples at **15 s** (band `U` = ultra
> long period), unlike the 1 s PREST at the other two sites. The VEL3D-B is still
> 1 Hz (band `L`). [IRIS MDA AXBA1](https://ds.iris.edu/mda/OO/AXBA1/)

| Instrument | Measurement | Reference Designator | Net | Sta | Loc | SEED Channel | Notes |
|---|---|---|---|---|---|---|---|
| BB Seismometer + Hydrophone *(existing)* | Velocity, Accel | — | OO | AXBA1 | — | BHE, BHN, BHZ | |
| Pressure | Pressure (PSI) | `RS03AXBS-MJ03A-06-PRESTA301` | OO | AXBA1 | 10 | UDO | `U`=ultra long (15 s); PSI→Pa |
| Pressure | Internal Temp (°C) | `RS03AXBS-MJ03A-06-PRESTA301` | OO | AXBA1 | 10 | UK1 | `1` = cabinet/internal source |
| **3-D Single Point Velocity** | **Eastward Vel (m/s)** | `RS03AXBS-MJ03A-12-VEL3DB301` | OO | AXBA1 | **20** | **`[L?]O2` / `[L?]OE`** | ⚠ orientation — see below |
| **3-D Single Point Velocity** | **Northward Vel (m/s)** | `RS03AXBS-MJ03A-12-VEL3DB301` | OO | AXBA1 | **20** | **`[L?]O1` / `[L?]ON`** | ⚠ orientation — see below |
| **3-D Single Point Velocity** | **Upward Vel (m/s)** | `RS03AXBS-MJ03A-12-VEL3DB301` | OO | AXBA1 | **20** | **`[L?]OZ`** | Z if within 5° of true vertical |
| **3-D Single Point Velocity** | **Seawater Temp (°C)** | `RS03AXBS-MJ03A-12-VEL3DB301` | OO | AXBA1 | **20** | **`[L?]KO`** | `K`=Temp, `O`=Outside |

---

## Endurance OR Shelf — Station `CZSHF` ("Endurance OR Shelf Cabled Benthic")

> Endurance cabled benthic package, ~80 m. VEL3D **Series C** (Nortek), a
> different sensor from the RSN VEL3D-B. Velocity streams at **8 Hz** (band `M`,
> mid-period) in `vel3d_cd_velocity_data`; temperature is **not** in that stream —
> it comes from `vel3d_cd_system_data` (`temperature_centidegree`, 1 Hz → band
> `L`). 12 deployments, ongoing. [IRIS MDA CZSHF](https://ds.iris.edu/mda/OO/CZSHF/)

| Instrument | Measurement | Reference Designator | Net | Sta | Loc | SEED Channel | Notes |
|---|---|---|---|---|---|---|---|
| **3-D Single Point Velocity** | **Eastward Vel (m/s)** | `CE02SHBP-LJ01D-07-VEL3DC108` | OO | CZSHF | **20** | **`MOU`** | `vel3d_c_eastward_turbulent_velocity`, 8 Hz |
| **3-D Single Point Velocity** | **Northward Vel (m/s)** | `CE02SHBP-LJ01D-07-VEL3DC108` | OO | CZSHF | **20** | **`MOV`** | `vel3d_c_northward_turbulent_velocity`, 8 Hz |
| **3-D Single Point Velocity** | **Upward Vel (m/s)** | `CE02SHBP-LJ01D-07-VEL3DC108` | OO | CZSHF | **20** | **`MOW`** | `vel3d_c_upward_turbulent_velocity`, 8 Hz |
| **3-D Single Point Velocity** | **Seawater Temp (°C)** | `CE02SHBP-LJ01D-07-VEL3DC108` | OO | CZSHF | **20** | **`LKO`** | `temperature_centidegree` (×0.01 → °C), 1 Hz, from `vel3d_cd_system_data` |

---

## Endurance OR Offshore — Station `CZOFF` ("Endurance OR Offshore Cabled Benthic")

> Endurance cabled benthic package, ~581 m. Same VEL3D **Series C** instrument and
> stream layout as `CZSHF` above. 12 deployments, ongoing.
> [IRIS MDA CZOFF](https://ds.iris.edu/mda/OO/CZOFF/)

| Instrument | Measurement | Reference Designator | Net | Sta | Loc | SEED Channel | Notes |
|---|---|---|---|---|---|---|---|
| **3-D Single Point Velocity** | **Eastward Vel (m/s)** | `CE04OSBP-LJ01C-07-VEL3DC107` | OO | CZOFF | **20** | **`MOU`** | `vel3d_c_eastward_turbulent_velocity`, 8 Hz |
| **3-D Single Point Velocity** | **Northward Vel (m/s)** | `CE04OSBP-LJ01C-07-VEL3DC107` | OO | CZOFF | **20** | **`MOV`** | `vel3d_c_northward_turbulent_velocity`, 8 Hz |
| **3-D Single Point Velocity** | **Upward Vel (m/s)** | `CE04OSBP-LJ01C-07-VEL3DC107` | OO | CZOFF | **20** | **`MOW`** | `vel3d_c_upward_turbulent_velocity`, 8 Hz |
| **3-D Single Point Velocity** | **Seawater Temp (°C)** | `CE04OSBP-LJ01C-07-VEL3DC107` | OO | CZOFF | **20** | **`LKO`** | `temperature_centidegree` (×0.01 → °C), 1 Hz, from `vel3d_cd_system_data` |

---

## SEED band code by sample rate (why VEL3D-B is `L` but VEL3D-C is `M`/`U`)

The band letter follows the channel's sample rate (per the FDSN/SEED band-code table):

| Sample rate | Period | Band | Used by |
|---|---|---|---|
| 8 Hz | 0.125 s | `M` (mid period, >1–10 Hz) | VEL3D-C velocity → `MOU/MOV/MOW` |
| 1 Hz | 1 s | `L` (long period, ~1 Hz) | VEL3D-B velocity + temp → `LOE/LON/LOZ/LKO`; VEL3D-C temp → `LKO` |

---

## SEED code legend (VEL3D-B)

| Position | Code | Meaning |
|---|---|---|
| Band | `L` | Long period, ~1 Hz — **confirmed 1 Hz** from data (resolves the `[L?]` placeholder) |
| Instrument | `O` | Water current |
| Instrument | `K` | Temperature |
| Orientation | `2` / `E` | Most eastward component |
| Orientation | `1` / `N` | Most northward component |
| Orientation | `Z` | Vertical (up) |
| Orientation | `O` | Outside (used for seawater temp) |

## Resolved for this repo
- **Network** `OO`; **Location code** `20`; **Band** `L` (1 Hz confirmed).
- **Temperature channel** `LKO` (`K`=Temperature, `O`=Outside).
- **Units** m/s, `r_value = 1.0` (EarthScope/FDSN expectation for instrument code `O`).
- **Azimuth / Dip** — StationXML writes `Azimuth = 0.0` on every channel and omits
  `Dip` entirely (no authoritative as-installed orientation). Matches the PREST
  deliverable EarthScope already accepted. See the azimuth/dip decision below.

## VEL3D-C orientation codes — DECISION (2026-08-12): `MOU` / `MOV` / `MOW`

For the two Nortek VEL3D-C stations (`CZSHF`, `CZOFF`) we cannot verify the
"within 5° of true direction / true vertical" rule that the `E`/`N`/`Z` codes
assert, so the velocity channels use the SEED nonstandard-orientation codes
**`MOU`** (u, east-ish), **`MOV`** (v, north-ish), **`MOW`** (w, up-ish).
Temperature stays `LKO`. The RSN VEL3D-B stations keep `LOE`/`LON`/`LOZ` per the
declination investigation below (that investigation covered the Nobska product
functions only, not the Nortek).

## Velocity orientation codes — DECISION: `LOE` / `LON` / `LOZ`

Chosen scheme: **`LOE`** (east), **`LON`** (north), **`LOZ`** (up), plus **`LKO`**
seawater temp. Directed by user's supervisor (2026-06-09), superseding the earlier
interim choice of numeric `LO2`/`LO1`. This is consistent with the orientation
investigation below: the OOI product is declination-corrected to **true** north, so
the horizontals satisfy the "within 5° of true direction" rule for using `E`/`N`.

> The channel **codes** stay `LOE`/`LON`/`LOZ`. The decision above is only about
> those three-letter codes — not the numeric `Azimuth`/`Dip` metadata fields, which
> are governed by the separate decision immediately below.

### Azimuth / Dip metadata — DECISION (2026-06-17): `Azimuth = 0`, no `Dip`

Superseding the earlier interim metadata (`Azimuth = 90` on the East channel,
`Dip = −90` on the vertical), the StationXML now writes **`Azimuth = 0.0` on every
channel and no `<Dip>` element**, because we do **not** have authoritative
as-installed orientation (compass heading / tilt) for the instruments. Directed by
the user (2026-06-17).

- Exactly matches the **PREST deliverable** EarthScope already accepted — its scalar
  pressure/temp channels also carry `Azimuth = 0` and no `Dip`.
- Mechanics: `c_az = 0.0` for all channels; `c_dip` is **commented out** in the
  channel param files; `create_metadata.py` no longer writes `Dip`.
- **Known tension:** the `E`/`Z` orientation codes nominally imply azimuth 90° /
  vertical, so a strict FDSN code-vs-orientation check may warn. We accept this —
  orientation is labelled by the *code* only, not asserted by value. If a strict
  check ever rejects it, the fallback is numeric orientation codes
  (`LO2`/`LO1`/`LO3`, `MO2`/`MO1`/`MO3`).
- The declination / true-north investigation below still stands: it justifies the
  `E`/`N` **codes**; it does not require asserting a numeric azimuth.

### Orientation investigation (is the horizontal within 5° of true?)
The list's Notes say to use `E`/`N` only if the horizontal is within 5° of true
direction, else the numeric convention `2`/`1`. Investigated via the OOI data-product
algorithm:

- `eastward_turbulent_velocity` (PD878, `VELPTTU-VLE_L1`) is computed by
  **`nobska_mag_corr_east`** (`ion_functions.data.vel_functions`):
  *"L1 VELPTTU-VLE (Nobska) by correcting L0 for compass declination and converting
  from cm/s to m/s."* Inputs `{lat, lon, timestamp, raw_east, raw_north}` → it computes
  magnetic declination and **rotates the horizontal vector into true geographic
  coordinates**. `northward` (PD879) uses `nobska_mag_corr_north`; `upward` (PD880) is
  raw vertical, no rotation.
- Empirical check: raw `turbulent_velocity_east` = 0.30 cm/s (0.0030 m/s) vs delivered
  `eastward_turbulent_velocity` = 0.0039 m/s — confirms a real rotation is applied.

**Conclusion:** the OOI product is **declination-corrected to true north** (the ≈ +15°
Oregon declination is removed). It therefore *clears* the 5° rule by design, modulo the
MAVS-4 compass accuracy (~±2°, not independently verified). On that basis the supervisor
directed using the true-direction codes **`LOE`/`LON`/`LOZ`** (final decision above).
