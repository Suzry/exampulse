from __future__ import annotations

from dataclasses import dataclass

from app.core.analysis import ExamReadiness


@dataclass(frozen=True, slots=True)
class StressComponent:
    name: str
    points: int
    max_points: int
    label: str


@dataclass(frozen=True, slots=True)
class StressResult:
    score: int
    label: str
    components: list[StressComponent]


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _sleep_pressure(sleep_debt_minutes: float | None) -> StressComponent:
    if sleep_debt_minutes is None:
        points, label = 0, "neutral"
    elif sleep_debt_minutes <= -180:
        points, label = 25, "high pressure"
    elif sleep_debt_minutes <= -120:
        points, label = 18, "elevated pressure"
    elif sleep_debt_minutes <= -60:
        points, label = 10, "mild pressure"
    else:
        points, label = 0, "neutral"
    return StressComponent("sleep", points, 25, label)


def _recovery_pressure(recovery_score: float | int | None) -> StressComponent:
    if recovery_score is None:
        points, label = 0, "neutral"
    elif recovery_score < 30:
        points, label = 25, "high pressure"
    elif recovery_score < 50:
        points, label = 17, "elevated pressure"
    elif recovery_score < 70:
        points, label = 8, "mild pressure"
    else:
        points, label = 0, "low pressure"
    return StressComponent("recovery", points, 25, label)


def _hrv_pressure(hrv_delta_percent: float | int | None) -> StressComponent:
    if hrv_delta_percent is None:
        points, label = 0, "neutral"
    elif hrv_delta_percent <= -25:
        points, label = 20, "high pressure"
    elif hrv_delta_percent <= -15:
        points, label = 14, "elevated pressure"
    elif hrv_delta_percent <= -5:
        points, label = 7, "mild pressure"
    else:
        points, label = 0, "neutral"
    return StressComponent("hrv", points, 20, label)


def _rhr_pressure(rhr_delta_bpm: float | int | None) -> StressComponent:
    if rhr_delta_bpm is None:
        points, label = 0, "neutral"
    elif rhr_delta_bpm >= 10:
        points, label = 20, "high pressure"
    elif rhr_delta_bpm >= 7:
        points, label = 14, "elevated pressure"
    elif rhr_delta_bpm >= 4:
        points, label = 8, "mild pressure"
    else:
        points, label = 0, "neutral"
    return StressComponent("rhr", points, 20, label)


def _strain_pressure(previous_strain: float | int | None) -> StressComponent:
    if previous_strain is None:
        points, label = 0, "neutral"
    elif previous_strain >= 16:
        points, label = 10, "high"
    elif previous_strain >= 12:
        points, label = 6, "moderate"
    elif previous_strain >= 8:
        points, label = 3, "light"
    else:
        points, label = 0, "light"
    return StressComponent("strain", points, 10, label)


def classify_stress(score: int | float) -> str:
    clamped = _clamp_score(int(round(score)))
    if clamped <= 24:
        return "calm"
    if clamped <= 49:
        return "mild load"
    if clamped <= 74:
        return "elevated"
    return "high load"


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
