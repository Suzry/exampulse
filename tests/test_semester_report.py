from __future__ import annotations

from datetime import UTC, datetime

from app.core.analysis import ExamReadiness
from app.core.models import Exam
from app.services.semester_service import build_semester_report


def _result(
    course: str,
    readiness: float,
    grade: float | None = None,
    flags: list[str] | None = None,
) -> ExamReadiness:
    return ExamReadiness(
        exam=Exam(
            course=course,
            exam_at=datetime(2026, 6, 20, 10, tzinfo=UTC),
            grade=grade,
        ),
        sleep=None,
        recovery=None,
        previous_cycle=None,
        baseline_sleep_minutes=420,
        baseline_recovery_score=None,
        baseline_hrv=None,
        baseline_rhr=None,
        sleep_debt_minutes=-60,
        recovery_delta=None,
        hrv_delta_percent=None,
        rhr_delta_bpm=None,
        readiness_score=readiness,
        readiness_label="MODERATE",
        flags=flags or ["no major flags"],
        summary="",
    )


def _upcoming(course: str) -> ExamReadiness:
    return ExamReadiness(
        exam=Exam(course=course, exam_at=datetime(2026, 7, 20, 10, tzinfo=UTC)),
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
        summary="",
    )


def test_report_summarizes_analyzed_exams_only() -> None:
    report = build_semester_report(
        [
            _result("Best", 80, grade=95),
            _result("Worst", 30, grade=60, flags=["low sleep"]),
            _upcoming("Future"),
        ]
    )
    assert "Exams analyzed: **2**" in report
    assert "Best night: **Best**" in report
    assert "Worst night: **Worst**" in report
    assert "Future" not in report.split("## Per-exam detail")[1].split("##")[0]
    assert "**low sleep** — 1 exam(s)" in report


def test_report_handles_empty_input() -> None:
    report = build_semester_report([])
    assert "No analyzed exams yet" in report


def test_report_correlates_with_enough_grades() -> None:
    results = [
        _result(f"Exam {index}", readiness=40 + index * 5, grade=60 + index * 4)
        for index in range(6)
    ]
    report = build_semester_report(results)
    assert "Readiness vs grade" in report
    assert "+1.00" in report
    # Six pairs is still a small sample; the caveat must be present.
    assert "anecdotes" in report


def test_report_without_grades_says_so() -> None:
    report = build_semester_report([_result("A", 50), _result("B", 60)])
    assert "not enough to correlate" in report
