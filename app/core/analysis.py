from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import mean

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


def clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _avg(values: list[float | int | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return mean(usable) if usable else None


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
        "No major physiological load indicators stood out before this exam.",
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
    baseline_recoveries = _baseline_recoveries(baseline_sleeps, recoveries)

    sleep_minutes = _sleep_minutes(sleep)
    baseline_sleep = _avg([_sleep_minutes(sleep) for sleep in baseline_sleeps])
    baseline_recovery = _avg(
        [recovery.recovery_score for recovery in baseline_recoveries]
    )
    baseline_hrv = _avg([recovery.hrv_rmssd_milli for recovery in baseline_recoveries])
    baseline_rhr = _avg(
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
    )
