from __future__ import annotations

from datetime import datetime, timedelta

from rich.markup import escape

from app.cli.views.common import DOT, console, section_sub
from app.cli.views.plan_view import print_recovery_windows
from app.core.models import Exam
from app.core.planning import RecoveryWindow
from app.utils.terminal_ui import truncate
from app.utils.time import to_utc


def print_week(
    exams: list[Exam],
    windows: list[RecoveryWindow],
    now: datetime,
    days: int = 7,
) -> None:
    start = now.astimezone()
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = start_day + timedelta(days=days)
    section_sub(
        "WEEK",
        f"{start_day:%a %d %b} - {(end_day - timedelta(days=1)):%a %d %b}",
    )

    in_range = [
        exam
        for exam in exams
        if start_day <= to_utc(exam.exam_at).astimezone() < end_day
    ]
    if not in_range:
        console.print(f"[yellow]No exams in the next {days} day(s).[/yellow]")
        return

    short_after = {id(window.earlier) for window in windows if window.short}
    short_before = {id(window.later) for window in windows if window.short}

    by_day: dict[str, list[Exam]] = {}
    for exam in in_range:
        key = f"{to_utc(exam.exam_at).astimezone():%Y-%m-%d}"
        by_day.setdefault(key, []).append(exam)

    for offset in range(days):
        day = start_day + timedelta(days=offset)
        key = f"{day:%Y-%m-%d}"
        day_label = f"{day:%a %d}"
        day_exams = sorted(
            by_day.get(key, []), key=lambda exam: to_utc(exam.exam_at)
        )
        if not day_exams:
            console.print(f"[dim]{day_label:<8} {DOT}[/dim]")
            continue
        for index, exam in enumerate(day_exams):
            label = day_label if index == 0 else ""
            local = to_utc(exam.exam_at).astimezone()
            marks = []
            if id(exam) in short_before:
                marks.append("[yellow]short night before[/yellow]")
            if id(exam) in short_after:
                marks.append("[yellow]short night after[/yellow]")
            mark_text = f"   {' '.join(marks)}" if marks else ""
            console.print(
                f"[bold]{label:<8}[/bold] {local:%H:%M} "
                f"{escape(truncate(exam.course, 40))}{mark_text}"
            )

    print_recovery_windows(windows)
