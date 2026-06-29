from __future__ import annotations

from datetime import UTC, datetime, timedelta

import app.cli.main as cli_main
from app.core.analysis import ExamReadiness
from app.core.models import Exam, ResearchRawHRPoint, WhoopSleep
from app.core.night_hr import analyze_night_hr_from_raw


def _sleep(index: int, start: datetime, hours: int = 8) -> WhoopSleep:
    return WhoopSleep(
        id=f"sleep-{index}",
        cycle_id=index,
        start=start,
        end=start + timedelta(hours=hours),
        nap=False,
        score_state="SCORED",
        total_sleep_minutes=hours * 60,
    )


def _result(exam_at: datetime, sleep: WhoopSleep) -> ExamReadiness:
    return ExamReadiness(
        exam=Exam(course="Final", exam_at=exam_at, notes="End: 12:15"),
        sleep=sleep,
        recovery=None,
        previous_cycle=None,
        baseline_sleep_minutes=None,
        baseline_recovery_score=None,
        baseline_hrv=None,
        baseline_rhr=None,
        sleep_debt_minutes=None,
        recovery_delta=None,
        hrv_delta_percent=None,
        rhr_delta_bpm=None,
        readiness_score=40.0,
        readiness_label="LOW",
        flags=[],
        summary="",
    )


def _points(start: datetime, count: int, hr: int) -> list[ResearchRawHRPoint]:
    return [
        ResearchRawHRPoint(timestamp=start + timedelta(minutes=i), hr=hr, source="x")
        for i in range(count)
    ]


def test_night_hr_from_raw_matches_sleep_window() -> None:
    exam_at = datetime(2026, 6, 15, 10, tzinfo=UTC)
    night = _sleep(99, exam_at - timedelta(hours=12))  # ends well before exam
    result = _result(exam_at, night)
    # 60 points inside the sleep window, plus some outside it that must be ignored.
    points = _points(night.start, 60, 55)
    points += _points(night.start - timedelta(hours=5), 30, 90)

    signal = analyze_night_hr_from_raw(result, sleeps=[night], points=points)

    assert signal.status == "ok"
    assert signal.source == "imported_raw"
    assert signal.points == 60
    assert signal.avg_hr == 55
    assert signal.max_hr == 55


def test_night_hr_from_raw_no_points_in_window() -> None:
    exam_at = datetime(2026, 6, 15, 10, tzinfo=UTC)
    night = _sleep(99, exam_at - timedelta(hours=12))
    result = _result(exam_at, night)
    far = _points(night.start - timedelta(days=2), 20, 70)

    signal = analyze_night_hr_from_raw(result, sleeps=[night], points=far)
    assert signal.status == "missing_stream"


def test_night_hr_from_raw_computes_baseline_delta() -> None:
    exam_at = datetime(2026, 6, 15, 10, tzinfo=UTC)
    night = _sleep(1, exam_at - timedelta(hours=12))
    baseline_night = _sleep(2, exam_at - timedelta(days=3))
    result = _result(exam_at, night)
    points = _points(night.start, 50, 60) + _points(baseline_night.start, 50, 52)

    signal = analyze_night_hr_from_raw(
        result, sleeps=[night, baseline_night], points=points
    )
    assert signal.status == "ok"
    assert signal.baseline_hr == 52
    assert signal.delta_bpm == 8


def test_night_arousal_verdict_flags_suppressed_hrv() -> None:
    result = _result(datetime(2026, 6, 15, 10, tzinfo=UTC), _sleep(1, datetime(2026, 6, 14, tzinfo=UTC)))
    result.hrv_delta_percent = -19.0
    result.rhr_delta_bpm = 3.0
    verdict, style = cli_main._night_arousal(result)
    assert "elevated" in verdict
    assert style == "red"


def test_night_arousal_verdict_calm() -> None:
    result = _result(datetime(2026, 6, 15, 10, tzinfo=UTC), _sleep(1, datetime(2026, 6, 14, tzinfo=UTC)))
    result.hrv_delta_percent = 1.0
    result.rhr_delta_bpm = 0.0
    verdict, style = cli_main._night_arousal(result)
    assert verdict == "calm"
    assert style == "green"


def test_awake_verdict_thresholds() -> None:
    assert cli_main._awake_verdict(None) == ("unknown", "dim")
    assert cli_main._awake_verdict(8)[1] == "green"
    assert cli_main._awake_verdict(15)[1] == "yellow"
    assert cli_main._awake_verdict(20)[1] == "red"
    assert "24h+" in cli_main._awake_verdict(30)[0]
    assert "all-nighter" in cli_main._awake_verdict(20)[0]
