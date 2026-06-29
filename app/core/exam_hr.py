from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, pstdev

from app.core.models import Exam, WhoopWorkout
from app.utils.time import to_utc

# Default exam length used when the notes do not carry an explicit end time.
DEFAULT_EXAM_HOURS = 2

# Minutes of local baseline used to normalize per-minute exam heart rate.
RAW_HR_BASELINE_MINUTES = 90

ZONE_LABELS = ("Z1", "Z2", "Z3", "Z4", "Z5")


def exam_window(exam: Exam) -> tuple[datetime, datetime]:
    """Return the exam window ``(start, end)`` in UTC.

    The end is read from an ``End: HH:MM`` hint in the notes when present,
    otherwise it defaults to ``DEFAULT_EXAM_HOURS`` after the start.
    """
    start = to_utc(exam.exam_at)
    match = re.search(r"\bend\s*:\s*(\d{1,2}):(\d{2})", exam.notes or "", re.IGNORECASE)
    if not match:
        return start, start + timedelta(hours=DEFAULT_EXAM_HOURS)

    local_start = exam.exam_at
    end_local = local_start.replace(
        hour=int(match.group(1)),
        minute=int(match.group(2)),
        second=0,
        microsecond=0,
    )
    if end_local <= local_start:
        end_local += timedelta(days=1)
    return start, to_utc(end_local)


@dataclass(slots=True)
class ExamActivityHR:
    exam: Exam
    window_start: datetime
    window_end: datetime
    status: str  # "ok" | "no_activity"
    matched: int = 0
    avg_hr: float | None = None
    max_hr: int | None = None
    overlap_minutes: float = 0.0
    coverage_percent: float | None = None
    zone_percent: list[float] = field(default_factory=list)

    @property
    def window_minutes(self) -> float:
        return (self.window_end - self.window_start).total_seconds() / 60


@dataclass(slots=True)
class ExamRawHR:
    """Per-minute heart-rate stress for one exam window vs a local baseline."""

    exam: Exam
    window_start: datetime
    window_end: datetime
    status: str  # "ok" | "no_data"
    exam_points: int = 0
    baseline_points: int = 0
    avg_exam: float | None = None
    avg_baseline: float | None = None
    dbpm: float | None = None
    elevated_percent: float | None = None
    z: float | None = None
    minute_series: list[float] = field(default_factory=list)


def exam_window_hr(
    exam: Exam,
    points,
    *,
    baseline_minutes: int = RAW_HR_BASELINE_MINUTES,
) -> ExamRawHR:
    """Summarize real per-minute HR during the exam vs the local pre-exam baseline.

    ``points`` is any iterable of objects with ``timestamp`` and ``hr``. The
    baseline is the ``baseline_minutes`` immediately before the exam start.
    """
    window_start, window_end = exam_window(exam)
    baseline_start = window_start - timedelta(minutes=baseline_minutes)
    result = ExamRawHR(
        exam=exam,
        window_start=window_start,
        window_end=window_end,
        status="no_data",
    )

    exam_points = []
    baseline_points = []
    for point in points:
        moment = to_utc(point.timestamp)
        if window_start <= moment < window_end:
            exam_points.append(point)
        elif baseline_start <= moment < window_start:
            baseline_points.append(point)

    if not exam_points or not baseline_points:
        return result

    avg_exam = mean(point.hr for point in exam_points)
    avg_baseline = mean(point.hr for point in baseline_points)
    dbpm = avg_exam - avg_baseline
    elevated = sum(1 for point in exam_points if point.hr > avg_baseline + 10)

    z = None
    if len(baseline_points) >= 2:
        spread = pstdev(point.hr for point in baseline_points)
        if spread > 0:
            z = dbpm / spread

    # Per-minute averaged series (oldest -> newest) for a sparkline.
    buckets: dict[int, list[int]] = {}
    for point in sorted(exam_points, key=lambda item: to_utc(item.timestamp)):
        minute = int((to_utc(point.timestamp) - window_start).total_seconds() // 60)
        buckets.setdefault(minute, []).append(point.hr)
    minute_series = [mean(buckets[minute]) for minute in sorted(buckets)]

    result.status = "ok"
    result.exam_points = len(exam_points)
    result.baseline_points = len(baseline_points)
    result.avg_exam = avg_exam
    result.avg_baseline = avg_baseline
    result.dbpm = dbpm
    result.elevated_percent = (elevated / len(exam_points)) * 100
    result.z = z
    result.minute_series = minute_series
    return result


@dataclass(slots=True)
class PreExamHR:
    """Heart rate over a fixed clock window ending at the exam start.

    Built for all-nighter exams where there is no real night-before sleep, so a
    "sleep window" is meaningless. Instead it covers the awake hours leading up
    to the exam.
    """

    exam: Exam
    window_start: datetime
    window_end: datetime
    hours_before: float
    status: str  # "ok" | "no_data"
    points: int = 0
    avg_hr: float | None = None
    max_hr: int | None = None
    min_hr: int | None = None
    minute_series: list[float] = field(default_factory=list)


def pre_exam_window_hr(
    exam: Exam,
    points,
    *,
    hours_before: float = 10,
) -> PreExamHR:
    """Summarize HR over the ``hours_before`` clock window before the exam start."""
    window_end, _ = exam_window(exam)
    window_start = window_end - timedelta(hours=hours_before)
    result = PreExamHR(
        exam=exam,
        window_start=window_start,
        window_end=window_end,
        hours_before=hours_before,
        status="no_data",
    )

    selected = [
        point
        for point in points
        if window_start <= to_utc(point.timestamp) < window_end
    ]
    if not selected:
        return result

    selected.sort(key=lambda item: to_utc(item.timestamp))
    buckets: dict[int, list[int]] = {}
    for point in selected:
        minute = int((to_utc(point.timestamp) - window_start).total_seconds() // 60)
        buckets.setdefault(minute, []).append(point.hr)

    result.status = "ok"
    result.points = len(selected)
    result.avg_hr = mean(point.hr for point in selected)
    result.max_hr = max(point.hr for point in selected)
    result.min_hr = min(point.hr for point in selected)
    result.minute_series = [mean(buckets[minute]) for minute in sorted(buckets)]
    return result


def _overlap_minutes(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> float:
    latest_start = max(to_utc(start), window_start)
    earliest_end = min(to_utc(end), window_end)
    seconds = (earliest_end - latest_start).total_seconds()
    return max(0.0, seconds / 60)


def match_exam_activity(
    exam: Exam, workouts: list[WhoopWorkout]
) -> ExamActivityHR:
    """Match logged WHOOP activities to an exam window and summarize HR.

    Multiple overlapping activities are combined with an overlap-duration
    weighting, so a long activity counts more than a brief one. Heart-rate
    values are taken from WHOOP's per-activity summary; this is not a
    minute-by-minute signal.
    """
    window_start, window_end = exam_window(exam)
    result = ExamActivityHR(
        exam=exam,
        window_start=window_start,
        window_end=window_end,
        status="no_activity",
    )

    weighted: list[tuple[WhoopWorkout, float]] = []
    for workout in workouts:
        minutes = _overlap_minutes(
            workout.start, workout.end, window_start, window_end
        )
        if minutes > 0:
            weighted.append((workout, minutes))

    if not weighted:
        return result

    total_overlap = sum(minutes for _, minutes in weighted)
    avg_numerator = sum(
        workout.avg_hr * minutes
        for workout, minutes in weighted
        if workout.avg_hr is not None
    )
    avg_weight = sum(
        minutes for workout, minutes in weighted if workout.avg_hr is not None
    )
    max_values = [workout.max_hr for workout, _ in weighted if workout.max_hr is not None]

    zones = [0.0] * 5
    zone_weight = 0.0
    for workout, minutes in weighted:
        raw = [
            workout.hr_zone1_percent,
            workout.hr_zone2_percent,
            workout.hr_zone3_percent,
            workout.hr_zone4_percent,
            workout.hr_zone5_percent,
        ]
        if any(value is not None for value in raw):
            zone_weight += minutes
            for index, value in enumerate(raw):
                zones[index] += (value or 0.0) * minutes

    result.status = "ok"
    result.matched = len(weighted)
    result.avg_hr = avg_numerator / avg_weight if avg_weight else None
    result.max_hr = max(max_values) if max_values else None
    result.overlap_minutes = total_overlap
    window_minutes = result.window_minutes
    result.coverage_percent = (
        (total_overlap / window_minutes) * 100 if window_minutes > 0 else None
    )
    result.zone_percent = (
        [value / zone_weight for value in zones] if zone_weight else []
    )
    return result
