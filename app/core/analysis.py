from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.core.scoring import ScoringConfig, get_scoring_config
from app.utils.time import to_utc

# Scale factor that makes the median absolute deviation a consistent
# estimator of the standard deviation for normal data.
_MAD_TO_SIGMA = 1.4826


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


def _center(values: list[float | int | None]) -> float | None:
    """Baseline center: the median, so one all-nighter can't drag it."""
    usable = [float(value) for value in values if value is not None]
    return median(usable) if usable else None


def _spread(values: list[float | int | None]) -> float | None:
    """Robust sigma estimate from the MAD; needs at least two samples."""
    usable = [float(value) for value in values if value is not None]
    if len(usable) < 2:
        return None
    center = median(usable)
    mad = median([abs(value - center) for value in usable])
    return mad * _MAD_TO_SIGMA


def _zscore(
    value: float | None,
    baseline_center: float | None,
    baseline_spread: float | None,
) -> float | None:
    if value is None or baseline_center is None or not baseline_spread:
        return None
    return (value - baseline_center) / baseline_spread


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
    return max(candidates, key=lambda cycle: to_utc(cycle.end or cycle.start))


def _baseline_sleeps(
    exam_at, sleeps: list[WhoopSleep], window_days: int
) -> list[WhoopSleep]:
    exam_at_utc = to_utc(exam_at)
    start = exam_at_utc - timedelta(days=window_days)
    return [
        sleep
        for sleep in sleeps
        if sleep.score_state == "SCORED"
        and not sleep.nap
        and start <= to_utc(sleep.end) < exam_at_utc
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
    *,
    recovery_score: float | None,
    sleep_score: float | None,
    hrv_score: float | None,
    rhr_score: float | None,
    config: ScoringConfig,
) -> float | None:
    components = [
        (config.recovery_weight, recovery_score),
        (config.sleep_weight, sleep_score),
        (config.hrv_weight, hrv_score),
        (config.rhr_weight, rhr_score),
    ]
    usable = [(weight, score) for weight, score in components if score is not None]
    if not usable:
        return None
    total_weight = sum(weight for weight, _ in usable)
    return sum(weight * float(score) for weight, score in usable) / total_weight


def classify_readiness(score: float | None, config: ScoringConfig | None = None) -> str:
    config = config or get_scoring_config()
    if score is None:
        return "UNKNOWN"
    if score < config.readiness_low_below:
        return "LOW"
    if score < config.readiness_moderate_below:
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
    config: ScoringConfig,
) -> tuple[list[str], str]:
    if sleep is None:
        return (
            ["no matching sleep"],
            "No matching sleep before this exam yet. Sync more WHOOP history or check the exam date.",
        )

    flags: list[str] = []
    if sleep_debt is not None and sleep_debt <= config.flag_sleep_debt_minutes:
        flags.append("low sleep")
    if (
        recovery
        and recovery.recovery_score is not None
        and recovery.recovery_score < config.flag_recovery_below
    ):
        flags.append("low recovery")
    if hrv_delta is not None and hrv_delta <= config.flag_hrv_delta_percent:
        flags.append("HRV below baseline")
    if rhr_delta is not None and rhr_delta >= config.flag_rhr_delta_bpm:
        flags.append("elevated resting HR")
    if (
        previous_cycle
        and previous_cycle.strain is not None
        and previous_cycle.strain >= config.flag_previous_strain
    ):
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
    config: ScoringConfig | None = None,
) -> ExamReadiness:
    config = config or get_scoring_config()
    recovery_by_sleep_id = {
        recovery.sleep_id: recovery
        for recovery in recoveries
        if recovery.score_state == "SCORED"
    }

    sleep = _last_sleep_before(exam.exam_at, sleeps)
    recovery = recovery_by_sleep_id.get(sleep.id) if sleep is not None else None
    previous_cycle = _last_cycle_before(exam.exam_at, cycles)

    baseline_sleeps = _baseline_sleeps(
        exam.exam_at, sleeps, config.baseline_window_days
    )
    # The night-before sleep falls inside the baseline window; exclude it so the
    # baseline (and z-scores/percentiles) compare it against the *other* nights.
    if sleep is not None:
        baseline_sleeps = [night for night in baseline_sleeps if night.id != sleep.id]
    baseline_recoveries = [
        recovery_by_sleep_id[night.id]
        for night in baseline_sleeps
        if night.id in recovery_by_sleep_id
    ]

    sleep_minutes = _sleep_minutes(sleep)
    awake_hours_before = (
        (to_utc(exam.exam_at) - to_utc(sleep.end)).total_seconds() / 3600
        if sleep is not None
        else None
    )

    # Chronological baseline series (oldest -> newest) for trend sparklines.
    ordered_sleeps = sorted(baseline_sleeps, key=lambda item: to_utc(item.end))
    sleep_series: list[float] = []
    recovery_series: list[float] = []
    hrv_series: list[float] = []
    rhr_series: list[float] = []
    for night in ordered_sleeps:
        night_minutes = _sleep_minutes(night)
        if night_minutes is not None:
            sleep_series.append(night_minutes)
        linked = recovery_by_sleep_id.get(night.id)
        if linked is None:
            continue
        if linked.recovery_score is not None:
            recovery_series.append(float(linked.recovery_score))
        if linked.hrv_rmssd_milli is not None:
            hrv_series.append(float(linked.hrv_rmssd_milli))
        if linked.resting_heart_rate is not None:
            rhr_series.append(float(linked.resting_heart_rate))

    baseline_sleep = _center([_sleep_minutes(sleep) for sleep in baseline_sleeps])
    baseline_recovery = _center(
        [recovery.recovery_score for recovery in baseline_recoveries]
    )
    baseline_hrv = _center([recovery.hrv_rmssd_milli for recovery in baseline_recoveries])
    baseline_rhr = _center(
        [recovery.resting_heart_rate for recovery in baseline_recoveries]
    )
    baseline_sleep_std = _spread([_sleep_minutes(sleep) for sleep in baseline_sleeps])
    baseline_recovery_std = _spread(
        [recovery.recovery_score for recovery in baseline_recoveries]
    )
    baseline_hrv_std = _spread(
        [recovery.hrv_rmssd_milli for recovery in baseline_recoveries]
    )
    baseline_rhr_std = _spread(
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

    # A thin baseline can't support statistical claims: keep the raw deltas
    # but withhold z-scores and percentiles below the minimum night count.
    if len(baseline_sleeps) >= config.min_baseline_nights:
        sleep_z = _zscore(sleep_minutes, baseline_sleep, baseline_sleep_std)
        recovery_z = _zscore(recovery_score, baseline_recovery, baseline_recovery_std)
        hrv_z = _zscore(hrv, baseline_hrv, baseline_hrv_std)
        rhr_z = _zscore(rhr, baseline_rhr, baseline_rhr_std)
        recovery_percentile = _percentile_rank(
            recovery_score, [item.recovery_score for item in baseline_recoveries]
        )
    else:
        sleep_z = recovery_z = hrv_z = rhr_z = None
        recovery_percentile = None

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
        config=config,
    )
    label = classify_readiness(readiness, config)
    flags, summary = _flags_and_summary(
        sleep=sleep,
        recovery=recovery,
        sleep_debt=sleep_debt,
        hrv_delta=hrv_delta,
        rhr_delta=rhr_delta,
        previous_cycle=previous_cycle,
        config=config,
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
