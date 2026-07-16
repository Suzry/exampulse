from __future__ import annotations

from collections import Counter
from statistics import mean

from app.core.analysis import ExamReadiness
from app.core.correlation import (
    SMALL_SAMPLE_PAIRS,
    correlate,
    describe_strength,
    exam_outcomes,
)
from app.core.stress import compute_exam_stress_index
from app.utils.terminal_ui import compact_duration
from app.utils.time import utc_now

_NO_SIGNAL_FLAGS = {"no major flags", "no matching sleep"}


def _fmt(value: float | None, pattern: str = "{:.0f}") -> str:
    return "n/a" if value is None else pattern.format(value)


def build_semester_report(results: list[ExamReadiness]) -> str:
    """Markdown end-of-term summary of readiness, stress, and grades."""
    analyzed = [
        result for result in results if result.readiness_label not in {"UPCOMING"}
    ]
    lines: list[str] = []
    lines.append("# Exampulse semester report")
    lines.append("")
    lines.append(f"Generated {utc_now():%Y-%m-%d %H:%M} UTC.")
    lines.append("")

    if not analyzed:
        lines.append("No analyzed exams yet — import exams and WHOOP data first.")
        return "\n".join(lines) + "\n"

    readiness_values = [
        result.readiness_score
        for result in analyzed
        if result.readiness_score is not None
    ]
    stress_results = [
        (result, compute_exam_stress_index(result)) for result in analyzed
    ]
    stress_values = [stress.score for _, stress in stress_results if stress is not None]

    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Exams analyzed: **{len(analyzed)}**")
    if readiness_values:
        lines.append(f"- Average readiness: **{mean(readiness_values):.0f}/100**")
        best = max(
            (result for result in analyzed if result.readiness_score is not None),
            key=lambda result: result.readiness_score or 0,
        )
        worst = min(
            (result for result in analyzed if result.readiness_score is not None),
            key=lambda result: result.readiness_score or 0,
        )
        lines.append(
            f"- Best night: **{best.exam.course}** "
            f"({best.readiness_score:.0f} readiness)"
        )
        lines.append(
            f"- Worst night: **{worst.exam.course}** "
            f"({worst.readiness_score:.0f} readiness)"
        )
    if stress_values:
        lines.append(
            f"- Average physiological stress: **{mean(stress_values):.0f}/100**"
        )
    lines.append("")

    lines.append("## Per-exam detail")
    lines.append("")
    lines.append("| Exam | Date | Readiness | Stress | Sleep debt | Grade |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for result, stress in stress_results:
        exam = result.exam
        grade = f"{exam.grade:g}" if exam.grade is not None else "—"
        if exam.letter_grade:
            grade = f"{grade} ({exam.letter_grade})"
        lines.append(
            f"| {exam.course} "
            f"| {exam.exam_at:%Y-%m-%d %H:%M} "
            f"| {_fmt(result.readiness_score)} "
            f"| {stress.score if stress is not None else 'n/a'} "
            f"| {compact_duration(result.sleep_debt_minutes, signed=True)} "
            f"| {grade} |"
        )
    lines.append("")

    flag_counts = Counter(
        flag
        for result in analyzed
        for flag in result.flags
        if flag not in _NO_SIGNAL_FLAGS
    )
    if flag_counts:
        lines.append("## Recurring pressure points")
        lines.append("")
        for flag, count in flag_counts.most_common():
            lines.append(f"- **{flag}** — {count} exam(s)")
        lines.append("")

    outcomes = exam_outcomes(analyzed)
    lines.append("## Physiology vs grades")
    lines.append("")
    if len(outcomes) < 3:
        lines.append(
            f"Only {len(outcomes)} exam(s) have both a grade and night-before "
            "data — not enough to correlate. Keep recording grades."
        )
    else:
        pairs = [
            (
                "Readiness vs grade",
                [o.readiness for o in outcomes],
            ),
            ("Stress vs grade", [o.stress for o in outcomes]),
            ("Sleep debt vs grade", [o.sleep_debt_minutes for o in outcomes]),
        ]
        lines.append("| Relationship | n | Pearson r | Read as |")
        lines.append("| --- | ---: | ---: | --- |")
        for name, xs in pairs:
            paired = [
                (x, o.grade) for x, o in zip(xs, outcomes, strict=True) if x is not None
            ]
            corr = correlate([p[0] for p in paired], [p[1] for p in paired])
            r_text = "n/a" if corr.pearson_r is None else f"{corr.pearson_r:+.2f}"
            lines.append(
                f"| {name} | {corr.n} | {r_text} | "
                f"{describe_strength(corr.pearson_r)} |"
            )
        if len(outcomes) < SMALL_SAMPLE_PAIRS:
            lines.append("")
            lines.append(
                f"> Caveat: with n={len(outcomes)} these are anecdotes, not "
                "evidence. Correlation is not causation either way."
            )
    lines.append("")

    lines.append("## Method notes")
    lines.append("")
    lines.append(
        "- Each exam is compared against the median of the nights in the "
        "personal baseline window before it (robust to one-off bad nights)."
    )
    lines.append(
        "- Readiness blends recovery, sleep vs baseline, HRV delta, and RHR "
        "delta. Stress is a 0-100 pressure index over the same signals."
    )
    lines.append(
        "- These are physiological context signals, not judgments of "
        "preparation or ability."
    )
    return "\n".join(lines) + "\n"
