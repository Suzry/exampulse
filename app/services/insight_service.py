from __future__ import annotations

from sqlmodel import Session

from app.core.analysis import ExamReadiness, analyze_exam
from app.storage import repositories


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

        results = [
            analyze_exam(exam, sleeps=sleeps, recoveries=recoveries, cycles=cycles)
            for exam in exams
        ]
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
