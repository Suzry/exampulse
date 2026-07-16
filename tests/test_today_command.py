from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.cli.views.common import remaining_text as _remaining_text
from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.core.selectors import (
    latest_cycle as _latest_cycle,
)
from app.core.selectors import (
    latest_recovery as _latest_recovery,
)
from app.core.selectors import (
    latest_sleep as _latest_sleep,
)
from app.core.selectors import (
    next_upcoming_exam as _next_upcoming_exam,
)


def _sleep(index: int, end: datetime, minutes: int, nap: bool = False) -> WhoopSleep:
    return WhoopSleep(
        id=f"sleep-{index}",
        cycle_id=index,
        start=end - timedelta(hours=8),
        end=end,
        nap=nap,
        score_state="SCORED",
        total_sleep_minutes=minutes,
    )


def _recovery(index: int, score: int = 80) -> WhoopRecovery:
    return WhoopRecovery(
        sleep_id=f"sleep-{index}",
        cycle_id=index,
        score_state="SCORED",
        recovery_score=score,
    )


def test_next_upcoming_exam_uses_future_exam_only() -> None:
    now = datetime(2026, 6, 16, 9, tzinfo=UTC)
    past = Exam(course="Past", exam_at=now - timedelta(hours=1))
    later = Exam(course="Later", exam_at=now + timedelta(days=2))
    next_exam = Exam(course="Next", exam_at=now + timedelta(hours=3))

    assert _next_upcoming_exam([past, later, next_exam], now) == next_exam


def test_remaining_text_rounds_up_to_days_and_hours() -> None:
    now = datetime(2026, 6, 16, 9, tzinfo=UTC)
    exam_at = now + timedelta(days=1, hours=2, minutes=1)

    assert _remaining_text(exam_at, now) == "1d 3h"


def test_latest_whoop_context_ignores_future_data() -> None:
    now = datetime(2026, 6, 16, 9, tzinfo=UTC)
    older_sleep = _sleep(1, now - timedelta(days=2), 390)
    latest_sleep = _sleep(2, now - timedelta(hours=3), 420)
    future_sleep = _sleep(3, now + timedelta(hours=1), 480)
    latest_cycle = WhoopCycle(
        id=2,
        start=now - timedelta(days=1),
        end=now - timedelta(hours=2),
        score_state="SCORED",
        strain=10.5,
    )
    future_cycle = WhoopCycle(
        id=3,
        start=now,
        end=now + timedelta(hours=2),
        score_state="SCORED",
        strain=18.0,
    )

    assert _latest_sleep([older_sleep, future_sleep, latest_sleep], now) == latest_sleep
    assert _latest_cycle([future_cycle, latest_cycle], now) == latest_cycle
    assert _latest_recovery(
        [_recovery(1, 70), _recovery(2, 82), _recovery(3, 95)],
        [older_sleep, latest_sleep, future_sleep],
        now,
    ).recovery_score == 82
