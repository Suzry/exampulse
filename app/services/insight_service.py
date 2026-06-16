from __future__ import annotations

from sqlmodel import Session

from app.core.analysis import ExamReadiness, analyze_exam
from app.core.models import Exam
from app.storage import repositories
from app.utils.time import to_utc, utc_now

UPCOMING_EXAM_MESSAGE = (
    "Analysis will be available after WHOOP data exists for the night before this exam."
)


def _upcoming_result(exam: Exam) -> ExamReadiness:
    return ExamReadiness(
        exam=exam,
        sleep=None,
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
        readiness_score=None,
        readiness_label="UPCOMING",
        flags=[],
        summary=UPCOMING_EXAM_MESSAGE,
    )


class InsightService:
    def __init__(self, session: Session):
        self.session = session

    def generate(self, exam_name: str | None = None) -> list[ExamReadiness]:
        exams = repositories.list_exams(self.session)
        if exam_name:
            needle = exam_name.casefold()
            exams = [exam for exam in exams if needle in exam.course.casefold()]

        sleeps = repositories.list_sleeps(self.session)
        recoveries = repositories.list_recoveries(self.session)
        cycles = repositories.list_cycles(self.session)

        now = utc_now()
        results = []
        for exam in exams:
            if to_utc(exam.exam_at) > now:
                results.append(_upcoming_result(exam))
            else:
                results.append(
                    analyze_exam(
                        exam, sleeps=sleeps, recoveries=recoveries, cycles=cycles
                    )
                )
        for result in results:
            if result.exam.id is not None:
                repositories.save_exam_insight(
                    self.session,
                    exam_id=result.exam.id,
                    readiness_score=result.readiness_score,
                    readiness_label=result.readiness_label,
                    sleep_debt_minutes=result.sleep_debt_minutes,
                    hrv_delta_percent=result.hrv_delta_percent,
                    rhr_delta_bpm=result.rhr_delta_bpm,
                    recovery_delta=result.recovery_delta,
                    summary=result.summary,
                )
        return results
