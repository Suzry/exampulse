from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median

from app.core.analysis import (
    _baseline_sleeps,
    _hrv_score,
    _rhr_score,
    _sleep_minutes,
    _weighted_score,
    classify_readiness,
)
from app.core.models import Exam, WhoopRecovery, WhoopSleep
from app.core.scoring import ScoringConfig, get_scoring_config
from app.utils.time import to_utc

# Bedtimes cluster around midnight, so a naive median of clock times would mix
# 23:30 with 00:30 badly. Anchoring the day at 18:00 keeps one night together.
_BEDTIME_ANCHOR_MINUTES = 18 * 60

# How long before the exam the plan reserves for waking up, food, and travel.
DEFAULT_PREP_BUFFER_MINUTES = 120

# When two exams are closer than this, the night between them is worth planning.
_BACK_TO_BACK_MAX_GAP_HOURS = 36

# Assumed exam duration plus post-exam wind-down before sleep is realistic.
_EXAM_DURATION_MINUTES = 120
_WIND_DOWN_MINUTES = 120

_FALLBACK_TARGET_SLEEP_MINUTES = 480.0
_SHORT_BASELINE_SLEEP_MINUTES = 420


@dataclass(slots=True)
class ExamPlan:
    exam: Exam
    baseline_nights: int
    target_sleep_minutes: float
    bedtime_target: datetime
    wake_target: datetime
    typical_bedtime: datetime | None
    bedtime_shift_minutes: float | None
    projected_readiness: float | None
    projected_label: str
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RecoveryWindow:
    earlier: Exam
    later: Exam
    gap_hours: float
    sleep_opportunity_minutes: float
    short: bool


def _median_time_of_day_minutes(
    moments: list[datetime], anchor_minutes: int
) -> float | None:
    """Median clock time, expressed as minutes since midnight."""
    if not moments:
        return None
    day = 24 * 60
    shifted = [
        ((moment.hour * 60 + moment.minute) - anchor_minutes) % day
        for moment in moments
    ]
    return (median(shifted) + anchor_minutes) % day


def _at_time_of_day(day: datetime, minutes_since_midnight: float) -> datetime:
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=minutes_since_midnight)


def _recent_recoveries(
    sleeps: list[WhoopSleep],
    recoveries: list[WhoopRecovery],
    now: datetime,
    count: int = 3,
) -> list[WhoopRecovery]:
    recovery_by_sleep_id = {
        recovery.sleep_id: recovery
        for recovery in recoveries
        if recovery.score_state == "SCORED"
    }
    nights = sorted(
        (
            sleep
            for sleep in sleeps
            if sleep.score_state == "SCORED"
            and not sleep.nap
            and to_utc(sleep.end) <= now
        ),
        key=lambda sleep: to_utc(sleep.end),
    )
    linked = [
        recovery_by_sleep_id[night.id]
        for night in nights
        if night.id in recovery_by_sleep_id
    ]
    return linked[-count:]


def _center(values: list[float | int | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return median(usable) if usable else None


def project_readiness(
    exam: Exam,
    sleeps: list[WhoopSleep],
    recoveries: list[WhoopRecovery],
    now: datetime,
    config: ScoringConfig | None = None,
) -> float | None:
    """Projected readiness if tonight matches the baseline and the recent
    recovery/HRV/RHR trend holds. A projection, not a measurement."""
    config = config or get_scoring_config()
    baseline = _baseline_sleeps(exam.exam_at, sleeps, config.baseline_window_days)
    recovery_by_sleep_id = {
        recovery.sleep_id: recovery
        for recovery in recoveries
        if recovery.score_state == "SCORED"
    }
    baseline_recoveries = [
        recovery_by_sleep_id[night.id]
        for night in baseline
        if night.id in recovery_by_sleep_id
    ]
    baseline_hrv = _center([item.hrv_rmssd_milli for item in baseline_recoveries])
    baseline_rhr = _center([item.resting_heart_rate for item in baseline_recoveries])

    recent = _recent_recoveries(sleeps, recoveries, now)
    if not recent:
        return None
    recent_recovery = _center([item.recovery_score for item in recent])
    recent_hrv = _center([item.hrv_rmssd_milli for item in recent])
    recent_rhr = _center([item.resting_heart_rate for item in recent])

    hrv_delta = (
        ((recent_hrv - baseline_hrv) / baseline_hrv) * 100
        if recent_hrv is not None and baseline_hrv
        else None
    )
    rhr_delta = (
        recent_rhr - baseline_rhr
        if recent_rhr is not None and baseline_rhr is not None
        else None
    )
    # Sleep component assumes tonight lands on the baseline (zero debt).
    return _weighted_score(
        recovery_score=recent_recovery,
        sleep_score=75.0,
        hrv_score=_hrv_score(hrv_delta),
        rhr_score=_rhr_score(rhr_delta),
        config=config,
    )


def plan_exam(
    exam: Exam,
    sleeps: list[WhoopSleep],
    recoveries: list[WhoopRecovery],
    now: datetime,
    config: ScoringConfig | None = None,
    prep_buffer_minutes: int = DEFAULT_PREP_BUFFER_MINUTES,
) -> ExamPlan:
    config = config or get_scoring_config()
    tz = exam.exam_at.tzinfo or UTC
    exam_local = to_utc(exam.exam_at).astimezone(tz)

    baseline = _baseline_sleeps(exam.exam_at, sleeps, config.baseline_window_days)
    nights = len(baseline)
    baseline_sleep = _center([_sleep_minutes(night) for night in baseline])
    target_sleep = baseline_sleep or _FALLBACK_TARGET_SLEEP_MINUTES

    local_starts = [to_utc(night.start).astimezone(tz) for night in baseline]
    local_ends = [to_utc(night.end).astimezone(tz) for night in baseline]
    bed_tod = _median_time_of_day_minutes(local_starts, _BEDTIME_ANCHOR_MINUTES)
    wake_tod = _median_time_of_day_minutes(local_ends, 0)

    latest_wake = exam_local - timedelta(minutes=prep_buffer_minutes)
    wake_target = latest_wake
    if wake_tod is not None:
        typical_wake = _at_time_of_day(exam_local, wake_tod)
        # A later exam doesn't require sleeping into the afternoon: wake at the
        # usual time and keep the morning instead.
        wake_target = min(latest_wake, typical_wake)
    bedtime_target = wake_target - timedelta(minutes=target_sleep)

    typical_bedtime: datetime | None = None
    if bed_tod is not None:
        # Evening bedtimes belong to the previous calendar day.
        bed_day = exam_local if bed_tod < 12 * 60 else exam_local - timedelta(days=1)
        typical_bedtime = _at_time_of_day(bed_day, bed_tod)

    shift: float | None = None
    if typical_bedtime is not None:
        shift = (bedtime_target - typical_bedtime).total_seconds() / 60

    flags: list[str] = []
    if nights < config.min_baseline_nights:
        flags.append(f"thin baseline (n={nights})")
    if exam_local.hour < 10:
        flags.append("morning exam")
    if shift is not None and shift <= -30:
        flags.append(f"go to bed {abs(int(round(shift)))}m earlier than usual")
    if baseline_sleep is not None and baseline_sleep < _SHORT_BASELINE_SLEEP_MINUTES:
        flags.append("baseline sleep itself is short")

    projected = project_readiness(exam, sleeps, recoveries, now, config)
    projected_label = (
        classify_readiness(projected, config) if projected is not None else "UNKNOWN"
    )

    return ExamPlan(
        exam=exam,
        baseline_nights=nights,
        target_sleep_minutes=target_sleep,
        bedtime_target=bedtime_target,
        wake_target=wake_target,
        typical_bedtime=typical_bedtime,
        bedtime_shift_minutes=shift,
        projected_readiness=projected,
        projected_label=projected_label,
        flags=flags,
    )


def short_recovery_windows(
    exams: list[Exam],
    *,
    target_sleep_minutes: float = _FALLBACK_TARGET_SLEEP_MINUTES,
    exam_duration_minutes: int = _EXAM_DURATION_MINUTES,
    prep_buffer_minutes: int = DEFAULT_PREP_BUFFER_MINUTES,
    wind_down_minutes: int = _WIND_DOWN_MINUTES,
) -> list[RecoveryWindow]:
    """Nights squeezed between back-to-back exams, flagged when the realistic
    sleep opportunity falls below the target."""
    ordered = sorted(exams, key=lambda exam: to_utc(exam.exam_at))
    windows: list[RecoveryWindow] = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        earlier_end = to_utc(earlier.exam_at) + timedelta(minutes=exam_duration_minutes)
        gap_minutes = (to_utc(later.exam_at) - earlier_end).total_seconds() / 60
        if gap_minutes <= 0 or gap_minutes > _BACK_TO_BACK_MAX_GAP_HOURS * 60:
            continue
        opportunity = gap_minutes - wind_down_minutes - prep_buffer_minutes
        windows.append(
            RecoveryWindow(
                earlier=earlier,
                later=later,
                gap_hours=gap_minutes / 60,
                sleep_opportunity_minutes=max(0.0, opportunity),
                short=opportunity < target_sleep_minutes,
            )
        )
    return windows
