from __future__ import annotations

from dataclasses import dataclass

from app.core.analysis import ExamReadiness


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


def _sleep_pressure(sleep_debt_minutes: float | None) -> StressComponent:
    rounded_debt = int(round(sleep_debt_minutes)) if sleep_debt_minutes is not None else None
    if sleep_debt_minutes is None:
        points, label, note = 0, "neutral", "sleep debt unavailable"
    elif rounded_debt <= -180:
        points, label = 25, "high pressure"
        note = f"{abs(rounded_debt) // 60}h below baseline"
    elif rounded_debt <= -120:
        points, label = 18, "elevated pressure"
        note = f"{abs(rounded_debt) // 60}h below baseline"
    elif rounded_debt <= -60:
        points, label = 10, "mild pressure"
        note = "1h below baseline"
    else:
        points, label, note = 0, "neutral", "near baseline"
    return StressComponent("sleep_debt", points, 25, label, note)


def _recovery_pressure(recovery_score: float | int | None) -> StressComponent:
    if recovery_score is None:
        points, label, note = 0, "neutral", "recovery unavailable"
    elif recovery_score < 30:
        points, label = 25, "high pressure"
        note = "recovery below 30%"
    elif recovery_score < 50:
        points, label = 17, "elevated pressure"
        note = "recovery below 50%"
    elif recovery_score < 70:
        points, label = 8, "mild pressure"
        note = "recovery below 70%"
    else:
        points, label, note = 0, "calm", "recovery acceptable"
    return StressComponent("recovery_drop", points, 25, label, note)


def _hrv_pressure(hrv_delta_percent: float | int | None) -> StressComponent:
    if hrv_delta_percent is None:
        points, label, note = 0, "neutral", "HRV unavailable"
    elif hrv_delta_percent <= -25:
        points, label = 20, "high pressure"
        note = "HRV down 25%+"
    elif hrv_delta_percent <= -15:
        points, label = 14, "elevated pressure"
        note = "HRV down 15%+"
    elif hrv_delta_percent <= -5:
        points, label = 7, "mild pressure"
        note = "HRV down 5%+"
    else:
        points, label, note = 0, "neutral", "no meaningful drop"
    return StressComponent("hrv_pressure", points, 20, label, note)


def _rhr_pressure(rhr_delta_bpm: float | int | None) -> StressComponent:
    if rhr_delta_bpm is None:
        points, label, note = 0, "neutral", "RHR unavailable"
    elif rhr_delta_bpm >= 10:
        points, label = 20, "high pressure"
        note = f"+{rhr_delta_bpm:.0f} bpm"
    elif rhr_delta_bpm >= 7:
        points, label = 14, "elevated pressure"
        note = f"+{rhr_delta_bpm:.0f} bpm"
    elif rhr_delta_bpm >= 4:
        points, label = 8, "mild pressure"
        note = f"+{rhr_delta_bpm:.0f} bpm"
    else:
        points, label, note = 0, "neutral", f"{rhr_delta_bpm:+.0f} bpm"
    return StressComponent("rhr_elevation", points, 20, label, note)


def _strain_pressure(previous_strain: float | int | None) -> StressComponent:
    if previous_strain is None:
        points, label, note = 0, "neutral", "strain unavailable"
    elif previous_strain >= 16:
        points, label = 10, "high"
        note = f"previous strain {previous_strain:.1f}"
    elif previous_strain >= 12:
        points, label = 6, "moderate"
        note = f"previous strain {previous_strain:.1f}"
    elif previous_strain >= 8:
        points, label = 3, "light"
        note = f"previous strain {previous_strain:.1f}"
    else:
        points, label, note = 0, "light", f"previous strain {previous_strain:.1f}"
    return StressComponent("strain_load", points, 10, label, note)


def classify_stress(score: int | float) -> str:
    clamped = _clamp_score(int(round(score)))
    if clamped <= 24:
        return "low stress"
    if clamped <= 49:
        return "mild stress"
    if clamped <= 74:
        return "elevated stress"
    return "high stress"


def top_stress_drivers(
    components: list[StressComponent], limit: int = 3
) -> list[StressComponent]:
    contributing = [component for component in components if component.points > 0]
    return sorted(contributing, key=lambda component: component.points, reverse=True)[
        :limit
    ]


def compute_exam_stress_index(result: ExamReadiness) -> StressResult | None:
    if result.readiness_label == "UPCOMING":
        return None

    recovery_score = result.recovery.recovery_score if result.recovery else None
    previous_strain = result.previous_cycle.strain if result.previous_cycle else None
    components = [
        _sleep_pressure(result.sleep_debt_minutes),
        _recovery_pressure(recovery_score),
        _hrv_pressure(result.hrv_delta_percent),
        _rhr_pressure(result.rhr_delta_bpm),
        _strain_pressure(previous_strain),
    ]
    score = _clamp_score(sum(component.points for component in components))
    return StressResult(
        score=score,
        label=classify_stress(score),
        components=components,
    )


def stress_bar(score: int | float | None, width: int = 20) -> str:
    from app.utils.terminal_ui import make_bar

    return make_bar(score, max_value=100, width=width)
