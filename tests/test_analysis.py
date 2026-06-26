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


def test_analyze_exam_computes_statistical_context() -> None:
    exam_at = datetime(2026, 6, 20, 10, tzinfo=UTC)
    # Ten stable baseline nights, then a clearly worse night before the exam.
    sleeps = [
        _sleep(index, exam_at - timedelta(days=11 - index), 420)
        for index in range(1, 11)
    ]
    recoveries = [_recovery(index, 70, 45, 60) for index in range(1, 11)]
    bad_night = _sleep(99, exam_at - timedelta(hours=4), 300)
    sleeps.append(bad_night)
    recoveries.append(
        WhoopRecovery(
            sleep_id="sleep-99",
            cycle_id=99,
            score_state="SCORED",
            recovery_score=35,
            hrv_rmssd_milli=30,
            resting_heart_rate=70,
        )
    )

    result = analyze_exam(
        Exam(course="Stats", exam_at=exam_at),
        sleeps=sleeps,
        recoveries=recoveries,
        cycles=[],
    )

    assert result.baseline_nights == 10
    # Perfectly stable baseline -> zero std -> z-score is undefined (None).
    assert result.baseline_recovery_std == 0
    assert result.recovery_z is None
    # Percentile rank still works: the bad night sits below the whole baseline.
    assert result.recovery_percentile == 0


def test_analyze_exam_z_scores_and_series() -> None:
    exam_at = datetime(2026, 6, 20, 10, tzinfo=UTC)
    sleeps = [
        _sleep(index, exam_at - timedelta(days=11 - index), 420 + index)
        for index in range(1, 11)
    ]
    recoveries = [
        _recovery(index, 70 + index % 3, 45 + index % 4, 60 + index % 3)
        for index in range(1, 11)
    ]
    sleeps.append(_sleep(99, exam_at - timedelta(hours=4), 300))
    recoveries.append(
        WhoopRecovery(
            sleep_id="sleep-99",
            cycle_id=99,
            score_state="SCORED",
            recovery_score=35,
            hrv_rmssd_milli=30,
            resting_heart_rate=72,
        )
    )

    result = analyze_exam(
        Exam(course="Stats", exam_at=exam_at),
        sleeps=sleeps,
        recoveries=recoveries,
        cycles=[],
    )

    assert result.baseline_nights == 10
    assert result.baseline_recovery_std is not None
    assert result.recovery_z is not None and result.recovery_z < 0
    assert result.rhr_z is not None and result.rhr_z > 0
    assert result.recovery_percentile is not None
    # Series carry the 10 baseline nights plus the night-before value.
    assert len(result.recovery_series) == 11
    assert len(result.sleep_series) == 11


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
