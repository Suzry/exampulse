from __future__ import annotations

from datetime import datetime

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from app.cli.views.common import console, remaining_text, section
from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.utils.formatters import format_float, format_percent, minutes_to_hm
from app.utils.terminal_ui import (
    colored_bar,
    compact_duration,
    format_compact_datetime,
)


def _recovery_text(recovery: WhoopRecovery | None) -> str:
    if recovery is None:
        return "n/a"
    parts = [
        format_percent(recovery.recovery_score),
        f"HRV {format_float(recovery.hrv_rmssd_milli)} ms",
        f"RHR {recovery.resting_heart_rate} bpm"
        if recovery.resting_heart_rate is not None
        else "RHR n/a",
    ]
    return " | ".join(parts)


def _recovery_compact_text(recovery: WhoopRecovery | None) -> str:
    if recovery is None:
        return "n/a"
    score = "n/a" if recovery.recovery_score is None else f"{recovery.recovery_score:.0f}%"
    bar = colored_bar(recovery.recovery_score)
    return f"{score:<4} {bar}"


def print_compact_today(
    *,
    exam: Exam,
    now: datetime,
    sleep: WhoopSleep | None,
    recovery: WhoopRecovery | None,
    cycle: WhoopCycle | None,
) -> None:
    section("TODAY", width=40)
    console.print(f"[dim]{'next exam':<13}[/dim] {escape(exam.course)}")
    console.print(f"[dim]{'time':<13}[/dim] {format_compact_datetime(exam.exam_at)}")
    console.print(f"[dim]{'remaining':<13}[/dim] {remaining_text(exam.exam_at, now)}")
    console.print(
        f"[dim]{'stress':<13}[/dim] "
        "[cyan]pending night-before data[/cyan]"
    )

    section("LATEST WHOOP", width=40)
    console.print(f"[dim]{'recovery':<13}[/dim] {_recovery_compact_text(recovery)}")
    console.print(
        f"[dim]{'sleep':<13}[/dim] "
        f"{compact_duration(sleep.total_sleep_minutes if sleep else None)}"
    )
    console.print(
        f"[dim]{'strain':<13}[/dim] "
        f"{format_float(cycle.strain if cycle else None)}"
    )
    console.print(
        "\n[dim]reminder[/dim] Full analysis unlocks after exam time + "
        "night-before data."
    )


def today_panel(
    *,
    exam: Exam,
    now: datetime,
    sleep: WhoopSleep | None,
    recovery: WhoopRecovery | None,
    cycle: WhoopCycle | None,
) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Next exam", exam.course)
    table.add_row("Time", exam.exam_at.isoformat())
    table.add_row("Remaining", remaining_text(exam.exam_at, now))
    table.add_row("Notes", exam.notes or "n/a")
    table.add_row("Latest recovery", _recovery_text(recovery))
    table.add_row(
        "Latest sleep",
        minutes_to_hm(sleep.total_sleep_minutes if sleep else None),
    )
    table.add_row("Latest strain", format_float(cycle.strain if cycle else None))
    table.add_row(
        "Reminder",
        "Full exam analysis is only available after the exam time and "
        "night-before WHOOP data exists.",
    )
    return Panel(table, title="TODAY", box=box.ASCII, border_style="cyan")
