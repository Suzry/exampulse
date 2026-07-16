from __future__ import annotations

from rich import box
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from app.cli.views.common import (
    TABLE_BOX,
    UNICODE,
    ascii_safe_table,
    bpm_delta_text,
    console,
    float_text,
    hr_text,
    percent_text,
    section,
    zone_bar,
)
from app.core.exam_hr import ExamActivityHR
from app.core.models import Exam
from app.services.sync_service import redact_sleep_id
from app.utils.terminal_ui import (
    format_compact_datetime,
    truncate,
    value_color,
)


def print_exams_table(exams: list[Exam]) -> None:
    table = Table(title="EXAMS", box=box.ASCII)
    table.add_column("Course")
    table.add_column("Exam time")
    table.add_column("Grade")
    table.add_column("Letter")
    table.add_column("Notes")
    for exam in exams:
        table.add_row(
            exam.course,
            exam.exam_at.isoformat(),
            "n/a" if exam.grade is None else f"{exam.grade:g}",
            exam.letter_grade or "n/a",
            exam.notes,
        )
    console.print(ascii_safe_table(table))


def print_stream_error_samples(summary, *, debug: bool = False) -> None:
    samples = summary.sleep_stream_error_samples or []
    if not samples:
        return
    console.print("[yellow]First stream errors:[/yellow]")
    for sample in samples:
        detail = sample.error or sample.message or sample.response_text or "unknown"
        line = (
            f"- {sample.status_code or 'n/a'} "
            f"sleep_id={sample.redacted_sleep_id} "
            f"error={detail}"
        )
        if debug and sample.path:
            line = f"{line} path={_redact_stream_path(sample.path, sample.sleep_id)}"
        console.print(line)


def _redact_stream_path(path: str, sleep_id: str) -> str:
    if not path or not sleep_id:
        return path
    return path.replace(sleep_id, redact_sleep_id(sleep_id))


def print_exam_window_hr(result) -> None:
    section("EXAM WINDOW HR", width=40)
    console.print(f"[dim]{'exam':<10}[/dim] {escape(result.exam.course)}")
    console.print(
        f"[dim]{'window':<10}[/dim] "
        f"{result.window_start.astimezone().strftime('%H:%M')} - "
        f"{result.window_end.astimezone().strftime('%H:%M')}"
    )
    console.print(f"[dim]{'source':<10}[/dim] real raw HR")
    console.print(f"[dim]{'points':<10}[/dim] {result.points}")
    console.print(f"[dim]{'baseline':<10}[/dim] {hr_text(result.avg_hr_baseline)}")
    console.print(f"[dim]{'exam avg':<10}[/dim] {hr_text(result.avg_hr_exam)}")
    console.print(f"[dim]{'dbpm':<10}[/dim] {bpm_delta_text(result.dbpm)}")
    console.print(f"[dim]{'z-ish':<10}[/dim] {float_text(result.z_like)}")
    console.print(f"[dim]{'elevated':<10}[/dim] {percent_text(result.elevated_percent)}")
    console.print(
        "\n[dim]note      [/dim] Real user-provided HR data compared against "
        "a 90-minute local baseline."
    )


def print_whoop_raw_check(result) -> None:
    section("WHOOP RAW ACCESS CHECK", width=40)
    console.print(f"[dim]{'summary data':<18}[/dim] {result.summary_status}")
    stream = result.sleep_stream_status
    if result.sleep_stream_status_code is not None:
        stream = f"{stream} {result.sleep_stream_status_code}"
    console.print(f"[dim]{'sleep stream':<18}[/dim] {stream}")
    console.print(f"[dim]{'all-day raw HR':<18}[/dim] not exposed by official WHOOP API")
    console.print(f"[dim]{'source':<18}[/dim] WHOOP band only")
    console.print()
    if result.sleep_stream_forbidden:
        console.print(
            "[dim]note              [/dim] Exampulse requested official WHOOP Sleep Stream.\n"
            "The API returned 403, so raw sleep HR is not available for this account/app right now."
        )
    elif result.sleep_stream_status == "available":
        console.print("[dim]note              [/dim] Official WHOOP Sleep Stream is available.")
    else:
        console.print(
            "[dim]note              [/dim] Summary data is checked through official WHOOP APIs only."
        )


def print_exam_hr(results: list[ExamActivityHR], *, has_workouts: bool) -> None:
    section("EXAM HEART RATE", width=74)
    console.print("[dim]from logged WHOOP activities overlapping each exam window[/dim]")
    if not has_workouts:
        console.print(
            "\n[yellow]No WHOOP activities imported yet.[/yellow] Import your export:"
        )
        console.print(
            "  exampulse whoop import-export my_whoop_data.zip"
        )
        return

    table = Table(box=TABLE_BOX, header_style="bold", pad_edge=False, expand=False)
    table.add_column("Exam", no_wrap=True, overflow="ellipsis" if UNICODE else "crop")
    table.add_column("Window", no_wrap=True)
    table.add_column("Avg HR", justify="right", no_wrap=True)
    table.add_column("Max HR", justify="right", no_wrap=True)
    table.add_column("Covers", justify="right", no_wrap=True)
    table.add_column("HR zones (Z1-Z5)", no_wrap=True)
    for result in results:
        window = (
            f"{result.window_start.astimezone():%H:%M}"
            f"-{result.window_end.astimezone():%H:%M}"
        )
        if result.status != "ok":
            table.add_row(
                truncate(result.exam.course, 30),
                window,
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]no activity logged[/dim]",
            )
            continue
        coverage = (
            f"{result.coverage_percent:.0f}%"
            if result.coverage_percent is not None
            else "--"
        )
        table.add_row(
            truncate(result.exam.course, 30),
            window,
            Text(
                f"{result.avg_hr:.0f}" if result.avg_hr is not None else "--",
                style="cyan",
            ),
            f"{result.max_hr}" if result.max_hr is not None else "--",
            Text(coverage, style=value_color(result.coverage_percent, good_high=True)),
            zone_bar(result.zone_percent, width=18),
        )
    console.print(ascii_safe_table(table))
    console.print(
        "[dim]note[/dim] WHOOP gives a per-activity HR summary (avg/max/zones), "
        "not a minute-by-minute trace. 'Covers' is how much of the exam window the "
        "activity spanned."
    )


def print_not_enough_raw_hr_data() -> None:
    console.print("[yellow]Not enough raw HR data for this exam.[/yellow]")
    console.print("Needed:")
    console.print("- 90-min baseline before exam")
    console.print("- HR points during exam window")
    console.print()
    console.print("Run:")
    console.print("python -m app.cli.main research raw-hr audit")
    console.print("to inspect imported data.")


def print_raw_hr_audit(audit) -> None:
    section("RAW HR DATA AUDIT", width=40)
    if audit.total_points == 0:
        console.print("No raw HR points found.")
        console.print("Import real HR data first:")
        console.print()
        console.print(
            "python -m app.cli.main research raw-hr import-csv "
            "whoop_hr.csv --source whoop_export"
        )
        return

    console.print(f"[dim]{'total points':<14}[/dim] {audit.total_points:,}")
    console.print(f"[dim]{'sources':<14}[/dim] {len(audit.sources)}")
    console.print(
        f"[dim]{'date range':<14}[/dim] "
        f"{format_compact_datetime(audit.first)} -> {format_compact_datetime(audit.last)}"
    )
    console.print()
    console.print(
        f"[dim]{'source':<28} {'points':>8}  {'first':<16} {'last':<16}[/dim]"
    )
    for source in audit.sources:
        console.print(
            f"{truncate(source.source, 28):<28} "
            f"{source.points:>8,}  "
            f"{format_compact_datetime(source.first):<16} "
            f"{format_compact_datetime(source.last):<16}"
        )
