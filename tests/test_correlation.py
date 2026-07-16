from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.analysis import ExamReadiness
from app.core.correlation import (
    _ranks,
    correlate,
    describe_strength,
    exam_outcomes,
    pearson,
    spearman,
)
from app.core.models import Exam


def _result(
    course: str,
    grade: float | None,
    readiness: float | None = 60,
    label: str = "MODERATE",
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
        baseline_sleep_minutes=None,
        baseline_recovery_score=None,
        baseline_hrv=None,
        baseline_rhr=None,
        sleep_debt_minutes=-30,
        recovery_delta=None,
        hrv_delta_percent=None,
        rhr_delta_bpm=None,
        readiness_score=readiness,
        readiness_label=label,
        flags=[],
        summary="",
    )


def test_pearson_perfect_positive_correlation() -> None:
    assert pearson([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_pearson_perfect_negative_correlation() -> None:
    assert pearson([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_pearson_undefined_for_constant_series() -> None:
    assert pearson([1, 1, 1], [10, 20, 30]) is None


def test_pearson_needs_three_pairs() -> None:
    assert pearson([1, 2], [10, 20]) is None


def test_ranks_average_ties() -> None:
    assert _ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_monotonic_nonlinear_is_perfect() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 8.0, 27.0, 64.0, 125.0]
    assert spearman(xs, ys) == pytest.approx(1.0)


def test_correlate_reports_sample_size() -> None:
    result = correlate([1, 2, 3], [3, 2, 1])
    assert result.n == 3
    assert result.small_sample


def test_exam_outcomes_needs_grade_and_analysis() -> None:
    results = [
        _result("Graded", grade=85),
        _result("No grade", grade=None),
        _result("Upcoming", grade=90, label="UPCOMING"),
    ]
    outcomes = exam_outcomes(results)
    assert [outcome.course for outcome in outcomes] == ["Graded"]
    assert outcomes[0].grade == 85
    assert outcomes[0].stress is not None


def test_describe_strength_bands() -> None:
    assert describe_strength(None) == "not computable"
    assert describe_strength(0.05) == "negligible"
    assert describe_strength(-0.4) == "moderate"
    assert describe_strength(0.9) == "very strong"
