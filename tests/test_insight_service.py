from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from app.core.models import Exam, ExamInsight, WhoopCycle, WhoopRecovery, WhoopSleep
from app.services.insight_service import UPCOMING_EXAM_MESSAGE, InsightService
from app.utils.time import utc_now


def _sleep(index: int, exam_at, minutes: int = 420) -> WhoopSleep:
    return WhoopSleep(
        id=f"sleep-{index}",
        cycle_id=index,
        start=exam_at - timedelta(hours=12),
        end=exam_at - timedelta(hours=4),
        nap=False,
        score_state="SCORED",
        total_sleep_minutes=minutes,
        sleep_performance_percentage=85,
    )


def _recovery(index: int, score: int = 80) -> WhoopRecovery:
    return WhoopRecovery(
        sleep_id=f"sleep-{index}",
        cycle_id=index,
        score_state="SCORED",
        recovery_score=score,
        hrv_rmssd_milli=45,
        resting_heart_rate=58,
    )


def _cycle(index: int, exam_at) -> WhoopCycle:
    return WhoopCycle(
        id=index,
        start=exam_at - timedelta(days=1),
        end=exam_at - timedelta(hours=8),
        score_state="SCORED",
        strain=8.2,
    )


def test_future_exam_returns_upcoming_without_readiness_score() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    future_exam_at = utc_now() + timedelta(days=1)

    with Session(engine) as session:
        exam = Exam(
            course="Software Construction",
            exam_at=future_exam_at,
            notes="Room: 12-1.017",
        )
        session.add(exam)
        session.add(_sleep(1, future_exam_at))
        session.add(_recovery(1))
        session.add(_cycle(1, future_exam_at))
        session.commit()

        result = InsightService(session).generate()[0]
        insight = session.exec(
            select(ExamInsight).where(ExamInsight.exam_id == exam.id)
        ).one()

        assert result.readiness_label == "UPCOMING"
        assert result.readiness_score is None
        assert result.sleep is None
        assert result.recovery is None
        assert result.previous_cycle is None
        assert result.summary == UPCOMING_EXAM_MESSAGE
        assert insight.readiness_label == "UPCOMING"
        assert insight.readiness_score is None


def test_past_exam_still_generates_readiness_normally() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    past_exam_at = utc_now() - timedelta(days=1)

    with Session(engine) as session:
        exam = Exam(course="Operating Systems", exam_at=past_exam_at)
        session.add(exam)
        session.add(_sleep(1, past_exam_at))
        session.add(_recovery(1))
        session.add(_cycle(1, past_exam_at))
        session.commit()

        result = InsightService(session).generate()[0]
        insight = session.exec(
            select(ExamInsight).where(ExamInsight.exam_id == exam.id)
        ).one()

        assert result.readiness_label != "UPCOMING"
        assert result.readiness_score is not None
        assert result.sleep is not None
        assert insight.readiness_label == result.readiness_label
        assert insight.readiness_score == result.readiness_score
