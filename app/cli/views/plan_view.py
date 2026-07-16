from __future__ import annotations

from datetime import datetime, timedelta

from rich.markup import escape
from rich.text import Text

from app.cli.views.common import (
    DOT,
    console,
    remaining_text,
    section_sub,
)
from app.core.planning import ExamPlan, RecoveryWindow
from app.core.scoring import get_scoring_config
from app.utils.terminal_ui import (
    compact_duration,
    format_compact_datetime,
    status_color,
    truncate,
)
from app.utils.time import to_utc


def _clock(moment: datetime) -> str:
    return f"{moment:%a %H:%M}"


def print_plan(
    plans: list[ExamPlan],
    windows: list[RecoveryWindow],
    now: datetime,
) -> None:
    config = get_scoring_config()
    section_sub(
        "PLAN",
        f"bedtime targets for upcoming exams vs your "
        f"{config.baseline_window_days}-day baseline",
    )
    if not plans:
        console.print("[yellow]No upcoming exams to plan for.[/yellow]")
        return

    for index, plan in enumerate(plans):
        if index:
            console.print()
        exam = plan.exam
        tonight = to_utc(plan.bedtime_target) - now <= timedelta(hours=18)
        headline = Text.assemble(
            (truncate(exam.course, 40), "bold"),
            (
                f"   {format_compact_datetime(exam.exam_at)}"
                f"  {DOT}  in {remaining_text(exam.exam_at, now)}",
                "dim",
            ),
        )
        if tonight:
            headline.append("   TONIGHT", style="bold magenta")
        console.print(headline)

        console.print(
            f"[dim]{'target sleep':<14}[/dim] "
            f"{compact_duration(plan.target_sleep_minutes)} "
            f"[dim](baseline median, n={plan.baseline_nights})[/dim]"
        )
        arrow = "→"
        console.print(
            f"[dim]{'bedtime':<14}[/dim] "
            f"[bold cyan]{_clock(plan.bedtime_target)}[/bold cyan]  {arrow}  "
            f"wake {_clock(plan.wake_target)} "
            f"[dim](prep buffer included)[/dim]"
        )
        if plan.typical_bedtime is not None and plan.bedtime_shift_minutes is not None:
            shift = plan.bedtime_shift_minutes
            if shift <= -15:
                shift_text = f"{abs(int(round(shift)))}m earlier than usual"
                shift_style = "yellow" if shift > -60 else "red"
            elif shift >= 15:
                shift_text = f"{int(round(shift))}m later than usual"
                shift_style = "green"
            else:
                shift_text = "matches your usual bedtime"
                shift_style = "green"
            console.print(
                f"[dim]{'usual bedtime':<14}[/dim] "
                f"{_clock(plan.typical_bedtime)}  "
                f"[{shift_style}]{shift_text}[/{shift_style}]"
            )
        if plan.projected_readiness is not None:
            console.print(
                f"[dim]{'projection':<14}[/dim] "
                f"[{status_color(plan.projected_label)}]"
                f"{plan.projected_label} {plan.projected_readiness:.0f}[/] "
                "[dim](if tonight matches baseline; recent 3-night trend)[/dim]"
            )
        if plan.flags:
            console.print(
                f"[dim]{'flags':<14}[/dim] {escape(', '.join(plan.flags))}"
            )

    print_recovery_windows(windows)
    console.print(
        "\n[dim]note[/dim] Bedtime targets are derived from your own baseline "
        "sleep, not a universal rule. Projections are estimates, not promises."
    )


def print_recovery_windows(windows: list[RecoveryWindow]) -> None:
    short = [window for window in windows if window.short]
    if not short:
        return
    section_sub("SHORT RECOVERY WINDOWS", "back-to-back exams squeeze the night between")
    arrow = "→"
    for window in short:
        console.print(
            f"[yellow]{truncate(window.earlier.course, 26)} {arrow} "
            f"{truncate(window.later.course, 26)}[/yellow]   "
            f"[dim]gap {window.gap_hours:.0f}h {DOT} realistic sleep window "
            f"{compact_duration(window.sleep_opportunity_minutes)}[/dim]"
        )
    console.print(
        "[dim]Plan the earlier night: sleep banked before the first exam is the "
        "only lever for the squeezed night.[/dim]"
    )
