"""
gap_algorithms.py — shared gap-detection interface for the COSZO PREST pipeline.

Two pure functions return the same GapResult shape so the cron pipeline,
testk smoke-test, and offline plotting tools can swap algorithms via a single
config flag (`gap_algo` in run_prest.txt).

    detect_gaps_legacy  : robust median Δt + adaptive multiplier × sp threshold
                          (mirrors OOI_data_request_and_convert_mseed.py
                          ~lines 460–700 prior to the migration)
    detect_gaps_anomaly : OLS Δt_true + integer-step reconstruction +
                          true_missing = n_ideal − n_points (Option A:
                          split only when true_missing > 0)

Lightweight — pure numpy. No matplotlib import, safe for the resource-
constrained VM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class GapResult:
    sr: float                       # 1 / sp — lands in MiniSEED header
    sp: float                       # day-global sample interval (s)
    gap_idx: np.ndarray             # indices into dt_all where a gap starts
    segment_splits: list[int]       # split points for np.split on the timestamp array
    is_full: bool                   # wall-clock completeness flag
    n_gaps: int                     # post-correction gap count (anomaly: 0 if true_missing==0)
    n_segments: int                 # number of MiniSEED segments produced
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════════════
# Legacy — robust median + adaptive threshold
# ════════════════════════════════════════════════════════════════════════════
def detect_gaps_legacy(
    t_sec: np.ndarray,
    sp_nominal: float,
    req_duration: float | None = None,
) -> GapResult:
    """
    Pipeline-faithful legacy detector.

    Sample period:  median(Δt) after stripping the upper 10 % gap tail.
    Threshold:      multiplier × sp, where multiplier ∈ {2.0…4.0} depending on
                    sample-period regime and wall-clock completeness.

    `req_duration` is the requested window length in seconds (e.g. 86400 for a
    24 h pipeline run). If omitted, falls back to the actual t_sec span — only
    appropriate for offline tools where the request window equals the data span.
    """
    n = int(len(t_sec))
    if n < 2:
        raise ValueError(f"Need ≥2 samples, got {n}")

    dt_all = np.diff(t_sec)
    dt_pos = dt_all[np.isfinite(dt_all) & (dt_all > 0.0)]

    if dt_pos.size == 0:
        sp = float("nan")
    else:
        gap_cut = np.percentile(dt_pos, 90.0)
        dt_clean = dt_pos[dt_pos <= gap_cut]
        if dt_clean.size == 0:
            dt_clean = dt_pos
        sp = float(np.median(dt_clean))

    if not np.isfinite(sp) or sp <= 0:
        raise ValueError(f"Non-positive sample period: {sp}")

    sr = 1.0 / sp

    # Wall-clock completeness — pipeline uses requested window duration
    if req_duration is None:
        req_duration = float(t_sec[-1] - t_sec[0])
    expected_npts = int(round(req_duration / sp)) + 1
    tol = max(5, int(0.001 * expected_npts))
    is_full = abs(n - expected_npts) <= tol

    # Adaptive multiplier — same breakpoints as the pre-migration pipeline
    if sp >= 10.0:
        multiplier = 4.0 if is_full else 3.0
    elif sp >= 0.5:
        multiplier = 3.5 if is_full else 2.5
    else:
        multiplier = 2.5 if is_full else 2.0
    gap_threshold = multiplier * sp

    gap_idx = np.where(dt_all > gap_threshold)[0]
    segment_splits = (gap_idx + 1).tolist()

    if sp > 0 and len(gap_idx) > 0:
        gap_total_missing_est = int(sum(
            round(float(dt_all[gi]) / sp) - 1 for gi in gap_idx
        ))
    else:
        gap_total_missing_est = 0

    return GapResult(
        sr=sr,
        sp=sp,
        gap_idx=gap_idx,
        segment_splits=segment_splits,
        is_full=is_full,
        n_gaps=int(len(gap_idx)),
        n_segments=int(len(segment_splits) + 1),
        diagnostics={
            "algorithm": "legacy",
            "multiplier": multiplier,
            "gap_threshold": gap_threshold,
            "expected_npts": expected_npts,
            "gap_total_missing_est": gap_total_missing_est,
            "dt_all": dt_all,
        },
    )


# ════════════════════════════════════════════════════════════════════════════
# Anomaly — OLS Δt_true + integer-step + true_missing (Option A)
# ════════════════════════════════════════════════════════════════════════════
def detect_gaps_anomaly(
    t_sec: np.ndarray,
    sp_nominal: float,
    req_duration: float | None = None,    # accepted for interface parity; unused
) -> GapResult:
    """
    Integer-step reconstruction with OLS rate fit.

    Steps:
        Δt'        = diff(t_sec)
        Δt_FG      = median(Δt')
        Δi_int     = round(Δt' / Δt_FG)
        i_j        = cumsum(Δi_int)  (with i_0 = 0)
        n_ideal    = i_j[-1] + 1
        Δt_true    = polyfit(i_j, t_sec, 1).slope
        true_missing = max(0, n_ideal − n_points)

    Option A splitting: split MiniSEED only when true_missing > 0. Jitter-only
    days produce a single segment spanning the full window.
    """
    n = int(len(t_sec))
    if n < 2:
        raise ValueError(f"Need ≥2 samples, got {n}")

    dt_prime = np.diff(t_sec)
    dt_FG = float(np.median(dt_prime))
    if dt_FG <= 0:
        raise ValueError(f"Non-positive first-guess interval: {dt_FG}")

    delta_i_float = dt_prime / dt_FG
    delta_i_int   = np.round(delta_i_float).astype(np.int64)

    gap_positions_raw = np.where(delta_i_int > 1)[0]
    n_gaps_raw = int(gap_positions_raw.size)

    i_j = np.empty(n, dtype=np.int64)
    i_j[0] = 0
    np.cumsum(delta_i_int, out=i_j[1:])
    n_ideal = int(i_j[-1]) + 1

    true_missing = max(0, n_ideal - n)

    # OLS slope = Δt_true; intercept ignored (segment starttime stays as raw t_sec[0])
    slope, _ = np.polyfit(i_j.astype(float), t_sec, 1)
    dt_true = float(slope)
    if dt_true <= 0:
        raise ValueError(f"Non-positive OLS Δt_true: {dt_true}")

    sp = dt_true
    sr = 1.0 / dt_true

    # Jitter / instability diagnostics
    t_fit = i_j.astype(float) * dt_true   # intercept ≈ 0 in the t_sec frame
    e     = t_sec - (t_sec[0] + (t_fit - t_fit[0]))   # residuals re. observed start
    f     = e / dt_true
    fmax  = float(np.max(np.abs(f)))
    sigma_ms = float(np.std(1000.0 * e, ddof=0))
    jitter_unstable = bool(fmax > 0.4)

    # Wall-clock completeness — under the new algorithm "full" means n_ideal==n
    is_full = (true_missing == 0)

    # Option A — split only when true_missing > 0
    if true_missing == 0:
        gap_idx = np.array([], dtype=np.int64)
        segment_splits: list[int] = []
        n_gaps = 0
    else:
        gap_idx = gap_positions_raw
        segment_splits = (gap_idx + 1).tolist()
        n_gaps = n_gaps_raw

    return GapResult(
        sr=sr,
        sp=sp,
        gap_idx=gap_idx,
        segment_splits=segment_splits,
        is_full=is_full,
        n_gaps=n_gaps,
        n_segments=int(len(segment_splits) + 1),
        diagnostics={
            "algorithm": "anomaly",
            "dt_true": dt_true,
            "dt_FG": dt_FG,
            "n_ideal": n_ideal,
            "true_missing": true_missing,
            "n_gaps_raw": n_gaps_raw,
            "frac_maxabs": fmax,
            "jitter_unstable": jitter_unstable,
            "sigma_ms": sigma_ms,
            "dt_all": dt_prime,
        },
    )


# ════════════════════════════════════════════════════════════════════════════
# Selector
# ════════════════════════════════════════════════════════════════════════════
ALGORITHMS = {
    "legacy":  detect_gaps_legacy,
    "anomaly": detect_gaps_anomaly,
}

# Below this sample count the OLS fit and integer-step reconstruction become
# numerically suspect; anomaly mode falls back to legacy automatically. 100 is
# 25 minutes of 15 s data and 100 seconds of 1 Hz data — both safely larger
# than any backfill chunk we'd ever care to run through anomaly.
MIN_N_FOR_ANOMALY = 100


def detect_gaps(algo: str, t_sec: np.ndarray, sp_nominal: float, **kwargs) -> GapResult:
    """Dispatch helper. `algo` ∈ {"legacy", "anomaly"}.

    Short-window guardrail: if `algo == "anomaly"` and len(t_sec) < MIN_N_FOR_ANOMALY,
    silently fall back to legacy with a warning. The actual algorithm used is
    reflected in `result.diagnostics["algorithm"]` so callers (e.g. the CSV
    writer) can record what really ran.

    Extra kwargs (e.g. `req_duration`) are forwarded to the algorithm
    function. Both detectors accept `req_duration`; anomaly ignores it.
    """
    if algo == "anomaly" and len(t_sec) < MIN_N_FOR_ANOMALY:
        print(
            f"WARNING: n={len(t_sec)} below MIN_N_FOR_ANOMALY={MIN_N_FOR_ANOMALY}; "
            f"falling back to legacy for this window"
        )
        algo = "legacy"

    fn = ALGORITHMS.get(algo)
    if fn is None:
        raise ValueError(
            f"Unknown gap_algo {algo!r}; expected one of {list(ALGORITHMS)}"
        )
    return fn(t_sec, sp_nominal, **kwargs)
