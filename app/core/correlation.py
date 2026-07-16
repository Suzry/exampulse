from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.analysis import ExamReadiness
from app.core.stress import compute_exam_stress_index

# Below this many paired points a correlation is basically an anecdote.
MIN_PAIRS = 3
SMALL_SAMPLE_PAIRS = 8


@dataclass(frozen=True, slots=True)
class ExamOutcome:
    course: str
    grade: float
    readiness: float | None
    stress: float | None
    sleep_debt_minutes: float | None


@dataclass(frozen=True, slots=True)
class Correlation:
    n: int
    pearson_r: float | None
    spearman_rho: float | None

    @property
    def small_sample(self) -> bool:
        return self.n < SMALL_SAMPLE_PAIRS


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < MIN_PAIRS or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    sxx = sum(d * d for d in dx)
    syy = sum(d * d for d in dy)
    if sxx == 0 or syy == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / math.sqrt(sxx * syy)


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (ties share the mean of their positions)."""
    indexed = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed):
        tie_end = position
        while (
            tie_end + 1 < len(indexed)
            and values[indexed[tie_end + 1]] == values[indexed[position]]
        ):
            tie_end += 1
        average_rank = (position + tie_end) / 2 + 1
        for offset in range(position, tie_end + 1):
            ranks[indexed[offset]] = average_rank
        position = tie_end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < MIN_PAIRS or len(xs) != len(ys):
        return None
    return pearson(_ranks(xs), _ranks(ys))


def correlate(xs: list[float], ys: list[float]) -> Correlation:
    return Correlation(
        n=len(xs),
        pearson_r=pearson(xs, ys),
        spearman_rho=spearman(xs, ys),
    )


def exam_outcomes(results: list[ExamReadiness]) -> list[ExamOutcome]:
    """Exams that have both a recorded grade and an analyzed night before."""
    outcomes: list[ExamOutcome] = []
    for result in results:
        if result.exam.grade is None or result.readiness_label == "UPCOMING":
            continue
        stress = compute_exam_stress_index(result)
        outcomes.append(
            ExamOutcome(
                course=result.exam.course,
                grade=float(result.exam.grade),
                readiness=result.readiness_score,
                stress=float(stress.score) if stress is not None else None,
                sleep_debt_minutes=result.sleep_debt_minutes,
            )
        )
    return outcomes


def describe_strength(r: float | None) -> str:
    if r is None:
        return "not computable"
    magnitude = abs(r)
    if magnitude < 0.1:
        return "negligible"
    if magnitude < 0.3:
        return "weak"
    if magnitude < 0.5:
        return "moderate"
    if magnitude < 0.7:
        return "strong"
    return "very strong"
