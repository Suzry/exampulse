from __future__ import annotations

from dataclasses import dataclass

from app.core.analysis import ExamReadiness
from app.core.scoring import Band, ScoringConfig, get_scoring_config


@dataclass(frozen=True, slots=True)
class StressComponent:
    name: str
    points: int
    max_points: int
    label: str
    note: str


@dataclass(frozen=True, slots=True)
class StressResult:
    score: int
    label: str
    components: list[StressComponent]


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _match_band(
    value: float, bands: tuple[Band, ...], *, at_most: bool
) -> Band | None:
    """First band whose threshold the value crosses (<= for at_most, else >=)."""
    for band in bands:
        threshold = band[0]
        if (value <= threshold) if at_most else (value >= threshold):
            return band
    return None


def _sleep_pressure(
    sleep_debt_minutes: float | None, config: ScoringConfig
) -> StressComponent:
    max_points = config.sleep_debt_max_points
    if sleep_debt_minutes is None:
        return StressComponent(
            "sleep_debt", 0, max_points, "neutral", "sleep debt unavailable"
        )
    rounded_debt = int(round(sleep_debt_minutes))
    band = _match_band(rounded_debt, config.sleep_debt_bands, at_most=True)
    if band is None:
        return StressComponent("sleep_debt", 0, max_points, "neutral", "near baseline")
    _, points, label = band
    return StressComponent(
        "sleep_debt",
        points,
        max_points,
        label,
        f"{abs(rounded_debt) // 60}h below baseline",
    )


def _recovery_pressure(
    recovery_score: float | int | None, config: ScoringConfig
) -> StressComponent:
    max_points = config.recovery_max_points
    if recovery_score is None:
        return StressComponent(
            "recovery_drop", 0, max_points, "neutral", "recovery unavailable"
        )
    for threshold, points, label in config.recovery_bands:
        if recovery_score < threshold:
            return StressComponent(
                "recovery_drop",
                points,
                max_points,
                label,
                f"recovery below {threshold:.0f}%",
            )
    return StressComponent("recovery_drop", 0, max_points, "calm", "recovery acceptable")


def _hrv_pressure(
    hrv_delta_percent: float | int | None, config: ScoringConfig
) -> StressComponent:
    max_points = config.hrv_max_points
    if hrv_delta_percent is None:
        return StressComponent(
            "hrv_pressure", 0, max_points, "neutral", "HRV unavailable"
        )
    band = _match_band(float(hrv_delta_percent), config.hrv_bands, at_most=True)
    if band is None:
        return StressComponent(
            "hrv_pressure", 0, max_points, "neutral", "no meaningful drop"
        )
    threshold, points, label = band
    return StressComponent(
        "hrv_pressure", points, max_points, label, f"HRV down {abs(threshold):.0f}%+"
    )


def _rhr_pressure(
    rhr_delta_bpm: float | int | None, config: ScoringConfig
) -> StressComponent:
    max_points = config.rhr_max_points
    if rhr_delta_bpm is None:
        return StressComponent(
            "rhr_elevation", 0, max_points, "neutral", "RHR unavailable"
        )
    band = _match_band(float(rhr_delta_bpm), config.rhr_bands, at_most=False)
    if band is None:
        return StressComponent(
            "rhr_elevation", 0, max_points, "neutral", f"{rhr_delta_bpm:+.0f} bpm"
        )
    _, points, label = band
    return StressComponent(
        "rhr_elevation", points, max_points, label, f"+{rhr_delta_bpm:.0f} bpm"
    )


def _strain_pressure(
    previous_strain: float | int | None, config: ScoringConfig
) -> StressComponent:
    max_points = config.strain_max_points
    if previous_strain is None:
        return StressComponent(
            "strain_load", 0, max_points, "neutral", "strain unavailable"
        )
    note = f"previous strain {previous_strain:.1f}"
    band = _match_band(float(previous_strain), config.strain_bands, at_most=False)
    if band is None:
        return StressComponent("strain_load", 0, max_points, "light", note)
    _, points, label = band
    return StressComponent("strain_load", points, max_points, label, note)


def classify_stress(score: int | float, config: ScoringConfig | None = None) -> str:
    config = config or get_scoring_config()
    clamped = _clamp_score(int(round(score)))
    if clamped <= config.stress_low_max:
        return "low stress"
    if clamped <= config.stress_mild_max:
        return "mild stress"
    if clamped <= config.stress_elevated_max:
        return "elevated stress"
    return "high stress"


def top_stress_drivers(
    components: list[StressComponent], limit: int = 3
) -> list[StressComponent]:
    contributing = [component for component in components if component.points > 0]
    return sorted(contributing, key=lambda component: component.points, reverse=True)[
        :limit
    ]


def compute_exam_stress_index(
    result: ExamReadiness, config: ScoringConfig | None = None
) -> StressResult | None:
    if result.readiness_label == "UPCOMING":
        return None
    config = config or get_scoring_config()

    recovery_score = result.recovery.recovery_score if result.recovery else None
    previous_strain = result.previous_cycle.strain if result.previous_cycle else None
    components = [
        _sleep_pressure(result.sleep_debt_minutes, config),
        _recovery_pressure(recovery_score, config),
        _hrv_pressure(result.hrv_delta_percent, config),
        _rhr_pressure(result.rhr_delta_bpm, config),
        _strain_pressure(previous_strain, config),
    ]
    score = _clamp_score(sum(component.points for component in components))
    return StressResult(
        score=score,
        label=classify_stress(score, config),
        components=components,
    )


def stress_bar(score: int | float | None, width: int = 20) -> str:
    from app.utils.terminal_ui import make_bar

    return make_bar(score, max_value=100, width=width)
