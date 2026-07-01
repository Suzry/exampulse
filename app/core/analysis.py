from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from statistics import mean, pstdev

from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.utils.time import to_utc


@dataclass(slots=True)
class ExamReadiness:
    exam: Exam
    sleep: WhoopSleep | None
    recovery: WhoopRecovery | None
    previous_cycle: WhoopCycle | None
    baseline_sleep_minutes: float | None
    baseline_recovery_score: float | None
    baseline_hrv: float | None
    baseline_rhr: float | None
    sleep_debt_minutes: float | None
    recovery_delta: float | None
    hrv_delta_percent: float | None
    rhr_delta_bpm: float | None
    readiness_score: float | None
    readiness_label: str
    flags: list[str]
    summary: str
    # Statistical context (defaults keep older constructors working).
    baseline_nights: int = 0
    baseline_sleep_std: float | None = None
    baseline_recovery_std: float | None = None
    baseline_hrv_std: float | None = None
    baseline_rhr_std: float | None = None
    sleep_z: float | None = None
    recovery_z: float | None = None
    hrv_z: float | None = None
    rhr_z: float | None = None
    recovery_percentile: float | None = None
    awake_hours_before: float | None = None
    sleep_series: list[float] = field(default_factory=list)
    recovery_series: list[float] = field(default_factory=list)
    hrv_series: list[float] = field(default_factory=list)
    rhr_series: list[float] = field(default_factory=list)


def clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _avg(values: list[float | int | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return mean(usable) if usable else None


def _std(values: list[float | int | None]) -> float | None:
    """Population standard deviation; needs at least two samples."""
    usable = [float(value) for value in values if value is not None]
    return pstdev(usable) if len(usable) >= 2 else None


def _zscore(
    value: float | None,
    baseline_mean: float | None,
    baseline_std: float | None,
) -> float | None:
    if value is None or baseline_mean is None or not baseline_std:
        return None
    return (value - baseline_mean) / baseline_std


def _percentile_rank(value: float | None, values: list[float | int | None]) -> float | None:
    usable = [float(item) for item in values if item is not None]
    if value is None or not usable:
        return None
    at_or_below = sum(1 for item in usable if item <= value)
    return (at_or_below / len(usable)) * 100


def _sleep_minutes(sleep: WhoopSleep | None) -> float | None:
    if sleep is None:
        return None
    if sleep.total_sleep_minutes is not None:
        return float(sleep.total_sleep_minutes)
    parts = [sleep.light_sleep_minutes, sleep.slow_wave_minutes, sleep.rem_minutes]
    if all(part is not None for part in parts):
        return float(sum(part for part in parts if part is not None))
    return None


def _last_sleep_before(exam_at, sleeps: list[WhoopSleep]) -> WhoopSleep | None:
    exam_at_utc = to_utc(exam_at)
    scored = [
        sleep
        for sleep in sleeps
        if sleep.score_state == "SCORED" and to_utc(sleep.end) <= exam_at_utc
    ]
    main_sleeps = [sleep for sleep in scored if not sleep.nap]
    candidates = main_sleeps or scored
    if not candidates:
        return None
    return max(candidates, key=lambda sleep: to_utc(sleep.end))


def _last_cycle_before(exam_at, cycles: list[WhoopCycle]) -> WhoopCycle | None:
    exam_at_utc = to_utc(exam_at)
    candidates = [
        cycle
        for cycle in cycles
        if cycle.score_state == "SCORED"
        and cycle.end is not None
        and to_utc(cycle.end) <= exam_at_utc
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda cycle: to_utc(cycle.end))


def _baseline_sleeps(exam_at, sleeps: list[WhoopSleep]) -> list[WhoopSleep]:
    exam_at_utc = to_utc(exam_at)
    start = exam_at_utc - timedelta(days=14)
    return [
        sleep
        for sleep in sleeps
        if sleep.score_state == "SCORED"
        and not sleep.nap
        and start <= to_utc(sleep.end) < exam_at_utc
    ]


def _recovery_for_sleep(
    sleep: WhoopSleep | None, recoveries: list[WhoopRecovery]
) -> WhoopRecovery | None:
    if sleep is None:
        return None
    for recovery in recoveries:
        if recovery.sleep_id == sleep.id and recovery.score_state == "SCORED":
            return recovery
    return None


def _baseline_recoveries(
    baseline_sleeps: list[WhoopSleep], recoveries: list[WhoopRecovery]
) -> list[WhoopRecovery]:
    sleep_ids = {sleep.id for sleep in baseline_sleeps}
    return [
        recovery
        for recovery in recoveries
        if recovery.score_state == "SCORED" and recovery.sleep_id in sleep_ids
    ]


def _sleep_score(
    sleep: WhoopSleep | None,
    sleep_minutes: float | None,
    baseline_sleep: float | None,
) -> float | None:
    if sleep_minutes is not None and baseline_sleep and baseline_sleep > 0:
        sleep_debt = sleep_minutes - baseline_sleep
        return clamp(75 + (sleep_debt / 3))
    if sleep and sleep.sleep_performance_percentage is not None:
        return clamp(float(sleep.sleep_performance_percentage))
    if sleep_minutes is not None:
        return clamp((sleep_minutes / 480) * 100)
    return None


def _hrv_score(hrv_delta_percent: float | None) -> float | None:
    if hrv_delta_percent is None:
        return None
    return clamp(70 + (hrv_delta_percent * 1.5))


def _rhr_score(rhr_delta_bpm: float | None) -> float | None:
    if rhr_delta_bpm is None:
        return None
    return clamp(75 - (rhr_delta_bpm * 5))


def _weighted_score(
    recovery_score: float | None,
    sleep_score: float | None,
    hrv_score: float | None,
    rhr_score: float | None,
) -> float | None:
    components = [
        (0.40, recovery_score),
        (0.25, sleep_score),
        (0.20, hrv_score),
        (0.15, rhr_score),
    ]
    usable = [(weight, score) for weight, score in components if score is not None]
    if not usable:
        return None
    total_weight = sum(weight for weight, _ in usable)
    return sum(weight * float(score) for weight, score in usable) / total_weight


def classify_readiness(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score < 40:
        return "LOW"
    if score < 70:
        return "MODERATE"
    return "GOOD"


def _flags_and_summary(
    *,
    sleep: WhoopSleep | None,
    recovery: WhoopRecovery | None,
    sleep_debt: float | None,
    hrv_delta: float | None,
    rhr_delta: float | None,
    previous_cycle: WhoopCycle | None,
) -> tuple[list[str], str]:
    if sleep is None:
        return (
            ["no matching sleep"],
            "No matching sleep before this exam yet. Sync more WHOOP history or check the exam date.",
        )

    flags: list[str] = []
    if sleep_debt is not None and sleep_debt <= -90:
        flags.append("low sleep")
    if recovery and recovery.recovery_score is not None and recovery.recovery_score < 40:
        flags.append("low recovery")
    if hrv_delta is not None and hrv_delta <= -15:
        flags.append("HRV below baseline")
    if rhr_delta is not None and rhr_delta >= 5:
        flags.append("elevated resting HR")
    if previous_cycle and previous_cycle.strain is not None and previous_cycle.strain >= 14:
        flags.append("high previous strain")

    if flags:
        factors = ", ".join(flags)
        return (
            flags,
            "Your physiological readiness before this exam was lower than usual. "
            f"Main factors: {factors}. These are context signals, not proof of causation.",
        )

    return (
        ["no major flags"],
        "No major physiological stress indicators stood out before this exam.",
    )


def analyze_exam(
    exam: Exam,
    sleeps: list[WhoopSleep],
    recoveries: list[WhoopRecovery],
    cycles: list[WhoopCycle],
) -> ExamReadiness:
    sleep = _last_sleep_before(exam.exam_at, sleeps)
    recovery = _recovery_for_sleep(sleep, recoveries)
    previous_cycle = _last_cycle_before(exam.exam_at, cycles)

    baseline_sleeps = _baseline_sleeps(exam.exam_at, sleeps)
    # The night-before sleep falls inside the 14-day window; exclude it so the
    # baseline (and z-scores/percentiles) compare it against the *other* nights.
    if sleep is not None:
        baseline_sleeps = [night for night in baseline_sleeps if night.id != sleep.id]
    baseline_recoveries = _baseline_recoveries(baseline_sleeps, recoveries)

    sleep_minutes = _sleep_minutes(sleep)
    awake_hours_before = (
        (to_utc(exam.exam_at) - to_utc(sleep.end)).total_seconds() / 3600
        if sleep is not None
        else None
    )

    # Chronological baseline series (oldest -> newest) for trend sparklines.
    ordered_sleeps = sorted(baseline_sleeps, key=lambda item: to_utc(item.end))
    recovery_by_sleep = {item.sleep_id: item for item in baseline_recoveries}
    sleep_series: list[float] = []
    recovery_series: list[float] = []
    hrv_series: list[float] = []
    rhr_series: list[float] = []
    for night in ordered_sleeps:
        night_minutes = _sleep_minutes(night)
        if night_minutes is not None:
            sleep_series.append(night_minutes)
        linked = recovery_by_sleep.get(night.id)
        if linked is None:
            continue
        if linked.recovery_score is not None:
            recovery_series.append(float(linked.recovery_score))
        if linked.hrv_rmssd_milli is not None:
            hrv_series.append(float(linked.hrv_rmssd_milli))
        if linked.resting_heart_rate is not None:
            rhr_series.append(float(linked.resting_heart_rate))

    baseline_sleep = _avg([_sleep_minutes(sleep) for sleep in baseline_sleeps])
    baseline_recovery = _avg(
        [recovery.recovery_score for recovery in baseline_recoveries]
    )
    baseline_hrv = _avg([recovery.hrv_rmssd_milli for recovery in baseline_recoveries])
    baseline_rhr = _avg(
        [recovery.resting_heart_rate for recovery in baseline_recoveries]
    )
    baseline_sleep_std = _std([_sleep_minutes(sleep) for sleep in baseline_sleeps])
    baseline_recovery_std = _std(
        [recovery.recovery_score for recovery in baseline_recoveries]
    )
    baseline_hrv_std = _std(
        [recovery.hrv_rmssd_milli for recovery in baseline_recoveries]
    )
    baseline_rhr_std = _std(
        [recovery.resting_heart_rate for recovery in baseline_recoveries]
    )

    recovery_score = (
        float(recovery.recovery_score)
        if recovery and recovery.recovery_score is not None
        else None
    )
    hrv = (
        float(recovery.hrv_rmssd_milli)
        if recovery and recovery.hrv_rmssd_milli is not None
        else None
    )
    rhr = (
        float(recovery.resting_heart_rate)
        if recovery and recovery.resting_heart_rate is not None
        else None
    )

    sleep_debt = (
        sleep_minutes - baseline_sleep
        if sleep_minutes is not None and baseline_sleep is not None
        else None
    )
    recovery_delta = (
        recovery_score - baseline_recovery
        if recovery_score is not None and baseline_recovery is not None
        else None
    )
    hrv_delta = (
        ((hrv - baseline_hrv) / baseline_hrv) * 100
        if hrv is not None and baseline_hrv
        else None
    )
    rhr_delta = rhr - baseline_rhr if rhr is not None and baseline_rhr is not None else None

    sleep_z = _zscore(sleep_minutes, baseline_sleep, baseline_sleep_std)
    recovery_z = _zscore(recovery_score, baseline_recovery, baseline_recovery_std)
    hrv_z = _zscore(hrv, baseline_hrv, baseline_hrv_std)
    rhr_z = _zscore(rhr, baseline_rhr, baseline_rhr_std)
    recovery_percentile = _percentile_rank(
        recovery_score, [item.recovery_score for item in baseline_recoveries]
    )

    # Append the night-before value as the final point so trends show the dip.
    if sleep_minutes is not None:
        sleep_series.append(sleep_minutes)
    if recovery_score is not None:
        recovery_series.append(recovery_score)
    if hrv is not None:
        hrv_series.append(hrv)
    if rhr is not None:
        rhr_series.append(rhr)

    readiness = _weighted_score(
        recovery_score=recovery_score,
        sleep_score=_sleep_score(sleep, sleep_minutes, baseline_sleep),
        hrv_score=_hrv_score(hrv_delta),
        rhr_score=_rhr_score(rhr_delta),
    )
    label = classify_readiness(readiness)
    flags, summary = _flags_and_summary(
        sleep=sleep,
        recovery=recovery,
        sleep_debt=sleep_debt,
        hrv_delta=hrv_delta,
        rhr_delta=rhr_delta,
        previous_cycle=previous_cycle,
    )

    return ExamReadiness(
        exam=exam,
        sleep=sleep,
        recovery=recovery,
        previous_cycle=previous_cycle,
        baseline_sleep_minutes=baseline_sleep,
        baseline_recovery_score=baseline_recovery,
        baseline_hrv=baseline_hrv,
        baseline_rhr=baseline_rhr,
        sleep_debt_minutes=sleep_debt,
        recovery_delta=recovery_delta,
        hrv_delta_percent=hrv_delta,
        rhr_delta_bpm=rhr_delta,
        readiness_score=readiness,
        readiness_label=label,
        flags=flags,
        summary=summary,
        baseline_nights=len(baseline_sleeps),
        baseline_sleep_std=baseline_sleep_std,
        baseline_recovery_std=baseline_recovery_std,
        baseline_hrv_std=baseline_hrv_std,
        baseline_rhr_std=baseline_rhr_std,
        sleep_z=sleep_z,
        recovery_z=recovery_z,
        hrv_z=hrv_z,
        rhr_z=rhr_z,
        recovery_percentile=recovery_percentile,
        awake_hours_before=awake_hours_before,
        sleep_series=sleep_series,
        recovery_series=recovery_series,
        hrv_series=hrv_series,
        rhr_series=rhr_series,
    )
