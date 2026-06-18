from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import mean

from app.core.analysis import ExamReadiness
from app.core.models import WhoopSleep, WhoopSleepStreamPoint
from app.utils.time import to_utc


@dataclass(frozen=True, slots=True)
class NightHRSignal:
    status: str
    points: int = 0
    avg_hr: float | None = None
    max_hr: int | None = None
    baseline_hr: float | None = None
    delta_bpm: float | None = None
    elevated_percent: float | None = None
    spike_count: int | None = None
    confidence: str = "low"


def analyze_night_hr_signal(
    result: ExamReadiness,
    *,
    sleeps: list[WhoopSleep],
    stream_points: list[WhoopSleepStreamPoint],
) -> NightHRSignal:
    if result.readiness_label == "UPCOMING":
        return NightHRSignal(status="pending")
    if result.sleep is None:
        return NightHRSignal(status="missing_sleep")

    current_points = _sleeping_points_for_sleep(stream_points, result.sleep.id)
    if not current_points:
        return NightHRSignal(status="missing_stream")

    baseline_points = _baseline_sleeping_points(
        result=result,
        sleeps=sleeps,
        stream_points=stream_points,
    )
    if not baseline_points:
        return _signal_from_points(
            current_points=current_points,
            baseline_hr=None,
            confidence="low",
        )

    baseline_hr = mean(point.hr for point in baseline_points)
    confidence = "high" if len(baseline_points) >= 180 else "low"
    return _signal_from_points(
        current_points=current_points,
        baseline_hr=baseline_hr,
        confidence=confidence,
    )


def _sleeping_points_for_sleep(
    stream_points: list[WhoopSleepStreamPoint], sleep_id: str
) -> list[WhoopSleepStreamPoint]:
    return [
        point
        for point in stream_points
        if point.sleep_id == sleep_id and point.is_sleeping
    ]


def _baseline_sleeping_points(
    *,
    result: ExamReadiness,
    sleeps: list[WhoopSleep],
    stream_points: list[WhoopSleepStreamPoint],
) -> list[WhoopSleepStreamPoint]:
    if result.sleep is None:
        return []
    exam_at = to_utc(result.exam.exam_at)
    current_start = to_utc(result.sleep.start)
    window_start = exam_at - timedelta(days=14)

    baseline_sleep_ids = {
        sleep.id
        for sleep in sleeps
        if sleep.id != result.sleep.id
        and sleep.score_state == "SCORED"
        and not sleep.nap
        and window_start <= to_utc(sleep.end) < current_start
    }
    return [
        point
        for point in stream_points
        if point.sleep_id in baseline_sleep_ids and point.is_sleeping
    ]


def _signal_from_points(
    *,
    current_points: list[WhoopSleepStreamPoint],
    baseline_hr: float | None,
    confidence: str,
) -> NightHRSignal:
    avg_hr = mean(point.hr for point in current_points)
    max_hr = max(point.hr for point in current_points)
    delta_bpm = avg_hr - baseline_hr if baseline_hr is not None else None
    elevated_percent = None
    spike_count = None
    if baseline_hr is not None:
        elevated = [point for point in current_points if point.hr >= baseline_hr + 10]
        spikes = [point for point in current_points if point.hr >= baseline_hr + 20]
        elevated_percent = (len(elevated) / len(current_points)) * 100
        spike_count = len(spikes)

    return NightHRSignal(
        status="ok",
        points=len(current_points),
        avg_hr=avg_hr,
        max_hr=max_hr,
        baseline_hr=baseline_hr,
        delta_bpm=delta_bpm,
        elevated_percent=elevated_percent,
        spike_count=spike_count,
        confidence=confidence,
    )
