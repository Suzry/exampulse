from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console
from sqlmodel import Session, SQLModel, create_engine

import app.cli.main as cli_main
from app.core.analysis import ExamReadiness
from app.core.models import Exam, WhoopSleep, WhoopSleepStreamPoint
from app.core.night_hr import analyze_night_hr_signal
from app.services.sync_service import _extract_hr_stream_points
from app.storage.repositories import list_sleep_stream_points, upsert_sleep_stream_points


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _sleep(identifier: str, start: datetime, end: datetime) -> WhoopSleep:
    return WhoopSleep(
        id=identifier,
        cycle_id=1,
        start=start,
        end=end,
        score_state="SCORED",
        total_sleep_minutes=480,
    )


def _result(exam_at: datetime, sleep: WhoopSleep, label: str = "MODERATE") -> ExamReadiness:
    return ExamReadiness(
        exam=Exam(course="Operating Systems", exam_at=exam_at),
        sleep=sleep,
        recovery=None,
        previous_cycle=None,
        baseline_sleep_minutes=480,
        baseline_recovery_score=None,
        baseline_hrv=None,
        baseline_rhr=None,
        sleep_debt_minutes=0,
        recovery_delta=None,
        hrv_delta_percent=None,
        rhr_delta_bpm=None,
        readiness_score=66 if label != "UPCOMING" else None,
        readiness_label=label,
        flags=[],
        summary="ok",
    )


def test_sleep_stream_upsert_uniqueness() -> None:
    with _session() as session:
        upsert_sleep_stream_points(
            session,
            sleep_id="sleep-1",
            points=[{"timestamp": "2026-06-18T01:00:00+00:00", "hr": 61}],
        )
        upsert_sleep_stream_points(
            session,
            sleep_id="sleep-1",
            points=[{"timestamp": "2026-06-18T01:00:00+00:00", "hr": 64}],
        )

        points = list_sleep_stream_points(session, sleep_id="sleep-1")

    assert len(points) == 1
    assert points[0].hr == 64


def test_sleep_stream_analysis_with_baseline() -> None:
    exam_at = datetime(2026, 6, 18, 10, tzinfo=UTC)
    baseline_sleep = _sleep(
        "baseline",
        exam_at - timedelta(days=2, hours=8),
        exam_at - timedelta(days=2),
    )
    night_sleep = _sleep(
        "night",
        exam_at - timedelta(hours=9),
        exam_at - timedelta(hours=1),
    )
    points = [
        WhoopSleepStreamPoint(
            sleep_id="baseline",
            timestamp=baseline_sleep.start + timedelta(minutes=index),
            hr=60,
            is_sleeping=True,
        )
        for index in range(180)
    ] + [
        WhoopSleepStreamPoint(
            sleep_id="night",
            timestamp=night_sleep.start + timedelta(minutes=index),
            hr=hr,
            is_sleeping=True,
        )
        for index, hr in enumerate([66, 70, 81, 82])
    ]

    signal = analyze_night_hr_signal(
        _result(exam_at, night_sleep),
        sleeps=[baseline_sleep, night_sleep],
        stream_points=points,
    )

    assert signal.points == 4
    assert signal.avg_hr == 74.75
    assert signal.baseline_hr == 60
    assert signal.elevated_percent == 75
    assert signal.spike_count == 2
    assert signal.confidence == "high"


def test_missing_sleep_stream_data_does_not_crash_compact_report(monkeypatch) -> None:
    exam_at = datetime(2026, 6, 18, 10, tzinfo=UTC)
    sleep = _sleep("night", exam_at - timedelta(hours=9), exam_at - timedelta(hours=1))
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report([_result(exam_at, sleep)], sync_run=None, sleeps=[sleep])
    output = test_console.export_text()

    assert "NIGHT HR SIGNAL" in output
    assert "no sleep stream data" in output


def test_extract_hr_stream_points_reads_whoop_stream_payload() -> None:
    points = _extract_hr_stream_points(
        {
            "stream": [
                {
                    "timestamp": "2026-06-23T05:17:54.100Z",
                    "hr": None,
                    "is_sleeping": None,
                },
                {
                    "timestamp": "2026-06-23T05:18:54.100Z",
                    "hr": 62.4,
                    "is_sleeping": True,
                },
            ],
            "algorithm_version": "example",
        }
    )

    assert points == [
        {
            "timestamp": "2026-06-23T05:18:54.100Z",
            "hr": 62,
            "is_sleeping": True,
        }
    ]
