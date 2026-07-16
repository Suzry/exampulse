from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.core.models import Exam, WhoopRecovery, WhoopSleep
from app.core.planning import plan_exam, project_readiness, short_recovery_windows

RIYADH = timezone(timedelta(hours=3))


def _night(index: int, end: datetime, minutes: int = 435) -> WhoopSleep:
    return WhoopSleep(
        id=f"sleep-{index}",
        cycle_id=index,
        start=end - timedelta(minutes=minutes + 25),
        end=end,
        nap=False,
        score_state="SCORED",
        total_sleep_minutes=minutes,
    )


def _recovery(index: int, score: int = 70, hrv: float = 45, rhr: int = 58) -> WhoopRecovery:
    return WhoopRecovery(
        sleep_id=f"sleep-{index}",
        cycle_id=index,
        score_state="SCORED",
        recovery_score=score,
        hrv_rmssd_milli=hrv,
        resting_heart_rate=rhr,
    )


def _week_of_nights(exam_at: datetime, wake_hour: int = 7) -> list[WhoopSleep]:
    nights = []
    for index in range(1, 8):
        end = (exam_at - timedelta(days=index)).replace(hour=wake_hour, minute=0)
        nights.append(_night(index, end))
    return nights


def test_plan_exam_targets_baseline_median_sleep() -> None:
    exam_at = datetime(2026, 6, 22, 10, 15, tzinfo=RIYADH)
    now = exam_at.astimezone(UTC) - timedelta(days=1)
    sleeps = _week_of_nights(exam_at)
    recoveries = [_recovery(index) for index in range(1, 8)]

    plan = plan_exam(Exam(course="OS", exam_at=exam_at), sleeps, recoveries, now)

    assert plan.baseline_nights == 7
    assert plan.target_sleep_minutes == 435
    # Wake target never lands after (exam - prep buffer).
    assert plan.wake_target <= exam_at - timedelta(minutes=120)
    # Bedtime target leaves exactly the target sleep before the wake target.
    slept = (plan.wake_target - plan.bedtime_target).total_seconds() / 60
    assert slept == plan.target_sleep_minutes
    assert plan.projected_readiness is not None


def test_plan_exam_early_exam_requires_earlier_bedtime_flag() -> None:
    # 07:30 exam with a 2h prep buffer forces a 05:30 wake -> early bedtime.
    exam_at = datetime(2026, 6, 22, 7, 30, tzinfo=RIYADH)
    now = exam_at.astimezone(UTC) - timedelta(days=1)
    sleeps = _week_of_nights(exam_at, wake_hour=8)

    plan = plan_exam(Exam(course="Calculus", exam_at=exam_at), sleeps, [], now)

    assert "morning exam" in plan.flags
    assert plan.bedtime_shift_minutes is not None
    assert plan.bedtime_shift_minutes < -30
    assert any("earlier than usual" in flag for flag in plan.flags)


def test_plan_exam_thin_baseline_is_flagged() -> None:
    exam_at = datetime(2026, 6, 22, 10, 0, tzinfo=RIYADH)
    now = exam_at.astimezone(UTC) - timedelta(days=1)
    end = (exam_at - timedelta(days=1)).replace(hour=7)

    plan = plan_exam(
        Exam(course="OS", exam_at=exam_at), [_night(1, end)], [], now
    )

    assert plan.baseline_nights == 1
    assert any("thin baseline" in flag for flag in plan.flags)


def test_project_readiness_needs_recent_recoveries() -> None:
    exam_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    now = exam_at - timedelta(days=1)
    assert project_readiness(Exam(course="OS", exam_at=exam_at), [], [], now) is None


def test_short_recovery_windows_flags_back_to_back_exams() -> None:
    first = Exam(course="OS", exam_at=datetime(2026, 6, 22, 16, 0, tzinfo=UTC))
    second = Exam(course="DB", exam_at=datetime(2026, 6, 23, 8, 0, tzinfo=UTC))
    far = Exam(course="AI", exam_at=datetime(2026, 6, 28, 8, 0, tzinfo=UTC))

    windows = short_recovery_windows(
        [far, second, first], target_sleep_minutes=435
    )

    assert len(windows) == 1
    window = windows[0]
    assert window.earlier.course == "OS"
    assert window.later.course == "DB"
    # 16:00 + 2h exam -> 18:00; gap to 08:00 is 14h; minus 4h overhead = 10h.
    assert window.gap_hours == 14
    assert window.sleep_opportunity_minutes == 600
    assert not window.short


def test_short_recovery_windows_marks_truly_short_nights() -> None:
    first = Exam(course="OS", exam_at=datetime(2026, 6, 22, 20, 0, tzinfo=UTC))
    second = Exam(course="DB", exam_at=datetime(2026, 6, 23, 8, 0, tzinfo=UTC))

    windows = short_recovery_windows([first, second], target_sleep_minutes=435)

    assert len(windows) == 1
    # 20:00 + 2h -> 22:00; gap 10h; minus 4h overhead = 6h < 7h15 target.
    assert windows[0].short
