from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.analysis import analyze_exam
from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep


def _sleep(index: int, end: datetime, minutes: int, nap: bool = False) -> WhoopSleep:
    return WhoopSleep(
        id=f"sleep-{index}",
        cycle_id=index,
        start=end - timedelta(hours=8),
        end=end,
        nap=nap,
        score_state="SCORED",
        total_sleep_minutes=minutes,
        sleep_performance_percentage=80,
    )


def _recovery(index: int, score: int, hrv: float, rhr: int) -> WhoopRecovery:
    return WhoopRecovery(
        sleep_id=f"sleep-{index}",
        cycle_id=index,
        score_state="SCORED",
        recovery_score=score,
        hrv_rmssd_milli=hrv,
        resting_heart_rate=rhr,
    )


def test_analyze_exam_flags_low_readiness() -> None:
    exam_at = datetime(2026, 6, 16, 10, tzinfo=UTC)
    baseline_end = exam_at - timedelta(days=3)
    bad_sleep = _sleep(99, exam_at - timedelta(hours=3), 270)
    sleeps = [
        _sleep(1, baseline_end, 420),
        _sleep(2, baseline_end + timedelta(days=1), 410),
        bad_sleep,
    ]
    recoveries = [
        _recovery(1, 55, 42, 60),
        _recovery(2, 58, 40, 61),
        WhoopRecovery(
            sleep_id="sleep-99",
            cycle_id=99,
            score_state="SCORED",
            recovery_score=34,
            hrv_rmssd_milli=30,
            resting_heart_rate=68,
        ),
    ]
    cycles = [
        WhoopCycle(
            id=1,
            start=exam_at - timedelta(days=1),
            end=exam_at - timedelta(hours=8),
            score_state="SCORED",
            strain=14.8,
        )
    ]

    result = analyze_exam(
        Exam(course="Linear Algebra", exam_at=exam_at),
        sleeps=sleeps,
        recoveries=recoveries,
        cycles=cycles,
    )

    assert result.sleep == bad_sleep
    assert result.readiness_label == "LOW"
    assert result.sleep_debt_minutes is not None
    assert result.sleep_debt_minutes <= -90
    assert "low sleep" in result.flags
    assert "elevated resting HR" in result.flags
    assert "stress" not in result.summary.casefold()


def test_analyze_exam_ignores_nap_when_main_sleep_exists() -> None:
    exam_at = datetime(2026, 6, 16, 10, tzinfo=UTC)
    main_sleep = _sleep(1, exam_at - timedelta(hours=7), 400)
    nap = _sleep(2, exam_at - timedelta(hours=2), 50, nap=True)

    result = analyze_exam(
        Exam(course="Operating Systems", exam_at=exam_at),
        sleeps=[main_sleep, nap],
        recoveries=[_recovery(1, 70, 45, 58), _recovery(2, 72, 47, 57)],
        cycles=[],
    )

    assert result.sleep == main_sleep
