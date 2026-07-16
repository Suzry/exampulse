from __future__ import annotations

from rich.table import Table

from app.cli.views.common import SIMPLE_BOX, UNICODE, ascii_safe_table, console, section_sub
from app.core.correlation import (
    SMALL_SAMPLE_PAIRS,
    Correlation,
    ExamOutcome,
    describe_strength,
)
from app.utils.terminal_ui import truncate


def _r_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def print_correlation_report(
    outcomes: list[ExamOutcome],
    readiness_grade: Correlation | None,
    stress_grade: Correlation | None,
    sleep_grade: Correlation | None,
) -> None:
    section_sub("CORRELATE", "does night-before physiology track your grades?")
    if not outcomes:
        console.print(
            "[yellow]No graded, analyzed exams yet.[/yellow] Add grades to "
            "exams.json and re-import; correlation needs both a grade and "
            "night-before data."
        )
        return

    table = Table(box=SIMPLE_BOX, pad_edge=False, header_style="dim", expand=False)
    table.add_column("exam", style="bold")
    table.add_column("grade", justify="right")
    table.add_column("readiness", justify="right")
    table.add_column("stress", justify="right")
    table.add_column("sleep debt", justify="right")
    for outcome in outcomes:
        table.add_row(
            truncate(outcome.course, 30),
            f"{outcome.grade:g}",
            "--" if outcome.readiness is None else f"{outcome.readiness:.0f}",
            "--" if outcome.stress is None else f"{outcome.stress:.0f}",
            "--"
            if outcome.sleep_debt_minutes is None
            else f"{outcome.sleep_debt_minutes:+.0f}m",
        )
    console.print(ascii_safe_table(table))

    stats = Table(box=SIMPLE_BOX, pad_edge=False, header_style="dim", expand=False)
    stats.add_column("relationship", style="dim")
    stats.add_column("n", justify="right")
    stats.add_column("pearson r", justify="right")
    stats.add_column("spearman", justify="right")
    stats.add_column("read as")
    for name, corr in (
        ("readiness vs grade", readiness_grade),
        ("stress vs grade", stress_grade),
        ("sleep debt vs grade", sleep_grade),
    ):
        if corr is None:
            stats.add_row(name, "0", "n/a", "n/a", "not computable")
            continue
        stats.add_row(
            name,
            str(corr.n),
            _r_text(corr.pearson_r),
            _r_text(corr.spearman_rho),
            describe_strength(corr.pearson_r),
        )
    console.print(ascii_safe_table(stats))

    if readiness_grade is not None and readiness_grade.n:
        _print_scatter(outcomes)

    n = readiness_grade.n if readiness_grade else 0
    if n < SMALL_SAMPLE_PAIRS:
        console.print(
            f"\n[yellow]caveat[/yellow] Only {n} paired exam(s): treat these "
            "numbers as anecdotes, not evidence. Keep importing grades — the "
            f"picture firms up around n={SMALL_SAMPLE_PAIRS}+."
        )
    console.print(
        "[dim]note[/dim] Correlation is not causation; grades also depend on "
        "preparation, difficulty, and luck."
    )


def _print_scatter(outcomes: list[ExamOutcome], width: int = 44, height: int = 12) -> None:
    points = [
        (outcome.readiness, outcome.grade)
        for outcome in outcomes
        if outcome.readiness is not None
    ]
    if len(points) < 3:
        return
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x == min_x or max_y == min_y:
        return

    grid = [[" "] * width for _ in range(height)]
    dot = "●" if UNICODE else "o"
    for x, y in points:
        column = int(round((x - min_x) / (max_x - min_x) * (width - 1)))
        row_position = int(round((y - min_y) / (max_y - min_y) * (height - 1)))
        grid[height - 1 - row_position][column] = dot

    console.print()
    console.print("[dim]grade[/dim]")
    axis = "│" if UNICODE else "|"
    for row_index, row in enumerate(grid):
        left = f"{max_y:>5.0f}" if row_index == 0 else (
            f"{min_y:>5.0f}" if row_index == height - 1 else " " * 5
        )
        console.print(f"[dim]{left}[/dim] [dim]{axis}[/dim][cyan]{''.join(row)}[/cyan]")
    rule = "└" + "─" * width if UNICODE else "+" + "-" * width
    console.print(f"{' ' * 6}[dim]{rule}[/dim]")
    console.print(
        f"{' ' * 7}[dim]{min_x:<8.0f}{'readiness':^{width - 16}}{max_x:>8.0f}[/dim]"
    )
