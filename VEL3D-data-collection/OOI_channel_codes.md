# Non-Tier 1 OOI Instrument Data Channel List

Authoritative reference for SEED network/station/location/channel codes for the
OOI RCA seafloor instruments. Source: COSZO "Non-Tier 1 OOI Instrument Data
Channel" list (provided by user). **VEL3D-B rows are the scope of this repo.**

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

## Velocity orientation codes — DECISION: `LOE` / `LON` / `LOZ`

Chosen scheme: **`LOE`** (east), **`LON`** (north), **`LOZ`** (up), plus **`LKO`**
seawater temp. Directed by user's supervisor (2026-06-09), superseding the earlier
interim choice of numeric `LO2`/`LO1`. This is consistent with the orientation
investigation below: the OOI product is declination-corrected to **true** north, so
the horizontals satisfy the "within 5° of true direction" rule for using `E`/`N`.

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
