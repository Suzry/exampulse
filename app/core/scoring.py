from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# A pressure band: (threshold, points, label). Bands are checked in order and
# the first matching threshold wins; no match scores zero points.
Band = tuple[float, int, str]


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Single source of truth for every tunable in readiness/stress scoring.

    Values were previously hardcoded across analysis.py and stress.py.
    """

    # Baseline statistics
    baseline_window_days: int = 14
    # Below this many baseline nights, z-scores and percentiles are withheld
    # (deltas still show) so a thin baseline never masquerades as confidence.
    min_baseline_nights: int = 5

    # Readiness component weights (renormalized over available components)
    recovery_weight: float = 0.40
    sleep_weight: float = 0.25
    hrv_weight: float = 0.20
    rhr_weight: float = 0.15

    # Readiness label cutoffs
    readiness_low_below: float = 40
    readiness_moderate_below: float = 70

    # Stress component bands. Sleep debt and HRV match when the value is at or
    # BELOW the threshold; recovery matches when strictly below; RHR and strain
    # match when at or ABOVE it.
    sleep_debt_bands: tuple[Band, ...] = (
        (-180, 25, "high pressure"),
        (-120, 18, "elevated pressure"),
        (-60, 10, "mild pressure"),
    )
    sleep_debt_max_points: int = 25
    recovery_bands: tuple[Band, ...] = (
        (30, 25, "high pressure"),
        (50, 17, "elevated pressure"),
        (70, 8, "mild pressure"),
    )
    recovery_max_points: int = 25
    hrv_bands: tuple[Band, ...] = (
        (-25, 20, "high pressure"),
        (-15, 14, "elevated pressure"),
        (-5, 7, "mild pressure"),
    )
    hrv_max_points: int = 20
    rhr_bands: tuple[Band, ...] = (
        (10, 20, "high pressure"),
        (7, 14, "elevated pressure"),
        (4, 8, "mild pressure"),
    )
    rhr_max_points: int = 20
    strain_bands: tuple[Band, ...] = (
        (16, 10, "high"),
        (12, 6, "moderate"),
        (8, 3, "light"),
    )
    strain_max_points: int = 10

    # Stress label cutoffs (score <= cutoff)
    stress_low_max: int = 24
    stress_mild_max: int = 49
    stress_elevated_max: int = 74

    # Flag thresholds
    flag_sleep_debt_minutes: float = -90
    flag_recovery_below: float = 40
    flag_hrv_delta_percent: float = -15
    flag_rhr_delta_bpm: float = 5
    flag_previous_strain: float = 14


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@lru_cache(maxsize=1)
def get_scoring_config() -> ScoringConfig:
    """Config with env overrides for the knobs users actually retune."""
    return ScoringConfig(
        baseline_window_days=_env_int("EXAMPULSE_BASELINE_DAYS", 14),
        min_baseline_nights=_env_int("EXAMPULSE_MIN_BASELINE_NIGHTS", 5),
    )
