from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.core.analysis import ExamReadiness
from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.core.night_hr import NightHRSignal, analyze_night_hr_signal
from app.core.stress import (
    StressResult,
    compute_exam_stress_index,
    stress_bar,
    top_stress_drivers,
)
from app.integrations.whoop_client import WhoopAPIError
from app.integrations.whoop_oauth import OAuthError, run_local_oauth_flow
from app.services.demo_seed_service import DemoSeedService
from app.services.exam_service import ExamImportError, ExamService
from app.services.export_service import ExportService
from app.services.insight_service import InsightService
from app.services.sync_service import SyncService
from app.services.sync_service import redact_sleep_id
from app.services.whoop_raw_check_service import (
    SLEEP_STREAM_FORBIDDEN,
    WhoopRawCheckService,
)
from app.research.raw_hr.service import RawHRDataError, RawHRService
from app.storage.db import get_session, init_db
from app.storage.repositories import (
    has_demo_data,
    latest_whoop_raw_check,
    latest_sync_run,
    list_cycles,
    list_recoveries,
    list_sleeps,
    list_sleep_stream_points,
)
from app.utils.time import to_utc, utc_now
from app.utils.formatters import (
    format_bpm_delta,
    format_float,
    format_percent,
    format_percent_delta,
    minutes_to_hm,
)
from app.utils.terminal_ui import (
    colored_bar,
    compact_duration,
    format_compact_datetime,
    format_zscore,
    horizontal_rule,
    make_bar,
    sleep_debt_marker,
    sparkline,
    status_color,
    supports_unicode,
    truncate,
    value_color,
    zscore_color,
)

console = Console()

# Decorative glyphs and box styles degrade gracefully on non-UTF terminals.
_UNICODE = supports_unicode()
_DOT = "·" if _UNICODE else "-"
_DELTA = "Δ" if _UNICODE else "d"
_PLUSMINUS = "±" if _UNICODE else "+/-"
_BANNER_BOX = box.DOUBLE if _UNICODE else box.ASCII_DOUBLE_HEAD
_TABLE_BOX = box.SIMPLE_HEAVY if _UNICODE else box.ASCII
_SIMPLE_BOX = box.SIMPLE if _UNICODE else box.ASCII
app = typer.Typer(no_args_is_help=True)
exams_app = typer.Typer(help="Import and list exams.")
research_app = typer.Typer(help="Research tools for user-owned datasets.")
raw_hr_app = typer.Typer(help="User-provided raw heart-rate CSV tools.")
whoop_app = typer.Typer(help="WHOOP official API checks.")
app.add_typer(exams_app, name="exams")
research_app.add_typer(raw_hr_app, name="raw-hr")
app.add_typer(research_app, name="research")
app.add_typer(whoop_app, name="whoop")


def _ensure_db() -> None:
    init_db()


@app.command()
def auth() -> None:
    """Connect WHOOP and save OAuth tokens locally."""
    _ensure_db()
    try:
        with get_session() as session:
            run_local_oauth_flow(session, console=console)
        console.print("[green]WHOOP connected. Tokens saved locally.[/green]")
    except (OAuthError, OSError) as exc:
        console.print(f"[red]Auth failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def demo_seed(
    days: int = typer.Option(30, "--days", min=1, help="Days of demo history."),
    seed: int = typer.Option(42, "--seed", help="Random seed for repeatable data."),
    include_exams: bool = typer.Option(
        True,
        "--include-exams/--no-exams",
        help="Also seed a small demo exam schedule.",
    ),
) -> None:
    """Seed realistic offline WHOOP-like data without authentication."""
    _ensure_db()
    with get_session() as session:
        summary = DemoSeedService(session).seed(
            days=days,
            seed=seed,
            include_exams=include_exams,
        )
    console.print(
        "[magenta]DEMO DATA seeded:[/magenta] "
        f"{summary.sleeps_saved} sleeps, "
        f"{summary.recoveries_saved} recoveries, "
        f"{summary.cycles_saved} cycles, "
        f"{summary.exams_saved} exams."
    )


@app.command()
def sync(
    days: int = typer.Option(30, "--days", min=1, help="Days of WHOOP history."),
    streams: bool = typer.Option(
        False,
        "--streams",
        help="Also sync official WHOOP sleep HR stream points.",
    ),
    debug_streams: bool = typer.Option(
        False,
        "--debug-streams",
        help="Show a larger safe sample of sleep stream errors.",
    ),
) -> None:
    """Sync official WHOOP sleep, recovery, and cycle data."""
    _ensure_db()
    try:
        with get_session() as session:
            summary = SyncService(session).sync(
                days=days,
                streams=streams,
                stream_error_sample_limit=5 if debug_streams else 2,
            )
        console.print(
            "[green]Sync complete:[/green] "
            f"{summary.sleeps_saved} sleeps, "
            f"{summary.recoveries_saved} recoveries, "
            f"{summary.cycles_saved} cycles, "
            f"{summary.skipped_records} pending/skipped."
        )
        if streams:
            console.print(
                "[green]Sleep streams saved:[/green] "
                f"{summary.sleep_stream_points_saved} points across "
                f"{summary.sleep_stream_sleeps_synced} sleeps."
            )
            if summary.sleep_stream_errors:
                console.print(
                    "[yellow]Sleep stream fetch errors:[/yellow] "
                    f"{summary.sleep_stream_errors} sleep(s)."
                )
                _print_stream_error_samples(summary, debug=debug_streams)
    except (OAuthError, WhoopAPIError) as exc:
        console.print(f"[red]Sync failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@exams_app.command("import")
def import_exams(
    path: Path = typer.Argument(..., exists=True, readable=True),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Replace existing exams with the JSON file contents.",
    ),
) -> None:
    """Import exams from a JSON file."""
    _ensure_db()
    try:
        with get_session() as session:
            imported = ExamService(session).import_file(path, replace=replace)
        console.print(f"[green]Imported {len(imported)} exam(s).[/green]")
    except (ExamImportError, OSError, ValueError) as exc:
        console.print(f"[red]Exam import failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@exams_app.command("list")
def list_exam_command() -> None:
    """List imported exams."""
    _ensure_db()
    with get_session() as session:
        exams = ExamService(session).list()
    if not exams:
        console.print("No exams imported yet. Try: exampulse exams import exams.json")
        return
    _print_exams_table(exams)


@raw_hr_app.command("import-csv")
def import_raw_hr_csv(
    path: Path = typer.Argument(..., exists=True, readable=True),
    source: str = typer.Option(
        "whoop_export",
        "--source",
        help="Source label for this WHOOP-owned HR export.",
    ),
) -> None:
    """Import real WHOOP-owned raw HR points from CSV."""
    _ensure_db()
    try:
        with get_session() as session:
            summary = RawHRService(session).import_csv(path, source=source)
        console.print(
            "[green]Research raw HR imported:[/green] "
            f"{summary.rows_imported} point(s) from {summary.source}."
        )
    except (OSError, ValueError) as exc:
        console.print(f"[red]Raw HR import failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@raw_hr_app.command("audit")
def audit_raw_hr() -> None:
    """Summarize imported real raw HR data."""
    _ensure_db()
    with get_session() as session:
        audit = RawHRService(session).audit()
    _print_raw_hr_audit(audit)


@raw_hr_app.command("exam-window")
def research_exam_window(
    exam: str = typer.Option(..., "--exam", help="Course name or substring."),
    source: Optional[str] = typer.Option(None, "--source", help="Optional source filter."),
) -> None:
    """Compare exam-window HR against the 90-minute local baseline."""
    _ensure_db()
    try:
        with get_session() as session:
            result = RawHRService(session).exam_window(exam, source=source)
    except RawHRDataError as exc:
        _print_not_enough_raw_hr_data()
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]Exam-window HR failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_exam_window_hr(result)


@whoop_app.command("raw-check")
def whoop_raw_check() -> None:
    """Check official WHOOP raw HR availability safely."""
    _ensure_db()
    with get_session() as session:
        result = WhoopRawCheckService(session).check()
    _print_whoop_raw_check(result)


@app.command()
def export(
    directory: Path = typer.Option(Path("exports"), "--directory", help="Export directory."),
) -> None:
    """Write Exampulse CSV exports."""
    _ensure_db()
    with get_session() as session:
        summary = ExportService(session).export(directory)
    console.print(f"[green]Exports written:[/green] {summary.directory}")
    for path in summary.files:
        console.print(f"- {path}")


@app.command()
def report(
    exam: Optional[str] = typer.Option(None, "--exam", help="Filter by course name."),
    compact: bool = typer.Option(
        True,
        "--compact/--classic",
        help="Use the compact dashboard layout or the classic boxed report.",
    ),
) -> None:
    """Generate the terminal readiness report."""
    _ensure_db()
    with get_session() as session:
        results = InsightService(session).generate(exam_name=exam)
        sync_run = latest_sync_run(session)
        raw_check_run = latest_whoop_raw_check(session)
        demo_data = has_demo_data(session) or bool(sync_run and sync_run.source == "demo")
        sleeps = list_sleeps(session)
        sleep_stream_points = list_sleep_stream_points(session)
        sleep_stream_forbidden = _sleep_stream_forbidden(sync_run, raw_check_run)

    if not results:
        console.print("[yellow]No imported exams found. Showing demo output.[/yellow]")
        results = [_demo_result()]
    if compact:
        _print_compact_report(
            results,
            sync_run=sync_run,
            demo_data=demo_data,
            sleeps=sleeps,
            sleep_stream_points=sleep_stream_points,
            sleep_stream_forbidden=sleep_stream_forbidden,
        )
    else:
        _print_report(
            results,
            sync_run_message=_sync_message(sync_run),
            demo_data=demo_data,
        )


@app.command()
def today(
    compact: bool = typer.Option(
        True,
        "--compact/--classic",
        help="Use the compact dashboard layout or the classic boxed status.",
    ),
) -> None:
    """Show the next exam and the latest available WHOOP context."""
    _ensure_db()
    now = utc_now()
    with get_session() as session:
        exams = ExamService(session).list()
        sleeps = list_sleeps(session)
        recoveries = list_recoveries(session)
        cycles = list_cycles(session)

    next_exam = _next_upcoming_exam(exams, now)
    if next_exam is None:
        console.print("[yellow]No upcoming exams found.[/yellow]")
        return

    sleep = _latest_sleep(sleeps, now)
    recovery = _latest_recovery(recoveries, sleeps, now)
    cycle = _latest_cycle(cycles, now)
    if compact:
        _print_compact_today(
            exam=next_exam,
            now=now,
            sleep=sleep,
            recovery=recovery,
            cycle=cycle,
        )
    else:
        console.print(
            _today_panel(
                exam=next_exam,
                now=now,
                sleep=sleep,
                recovery=recovery,
                cycle=cycle,
            )
        )


@app.command()
def watch(
    every: int = typer.Option(30, "--every", min=1, help="Sync interval in minutes."),
) -> None:
    """Keep WHOOP data fresh with a simple polling loop."""
    _ensure_db()
    console.print(f"Watching WHOOP every {every} minute(s). Press Ctrl+C to stop.")
    running = False
    try:
        while True:
            if not running:
                running = True
                started = datetime.now(UTC)
                try:
                    with get_session() as session:
                        summary = SyncService(session).sync(days=7)
                        InsightService(session).generate()
                    console.print(
                        f"[green]{started.isoformat()}[/green] "
                        f"synced {summary.sleeps_saved}/"
                        f"{summary.recoveries_saved}/"
                        f"{summary.cycles_saved}; skipped {summary.skipped_records}"
                    )
                except (OAuthError, WhoopAPIError) as exc:
                    console.print(f"[red]Watch sync failed:[/red] {exc}")
                finally:
                    running = False
            time.sleep(every * 60)
    except KeyboardInterrupt:
        console.print("Stopped.")


def _risk_order(result: ExamReadiness) -> tuple[int, float]:
    order = {"LOW": 0, "MODERATE": 1, "GOOD": 2, "UNKNOWN": 3, "UPCOMING": 4}
    score = result.readiness_score if result.readiness_score is not None else 999
    return order.get(result.readiness_label, 3), float(score)


def _next_upcoming_exam(exams: list[Exam], now: datetime) -> Exam | None:
    upcoming = [exam for exam in exams if to_utc(exam.exam_at) > now]
    if not upcoming:
        return None
    return min(upcoming, key=lambda exam: to_utc(exam.exam_at))


def _remaining_text(exam_at: datetime, now: datetime) -> str:
    seconds = max(0, int((to_utc(exam_at) - now).total_seconds()))
    hours_total = (seconds + 3599) // 3600
    days, hours = divmod(hours_total, 24)
    return f"{days}d {hours}h"


def _section(title: str, width: int = 60) -> None:
    console.print(f"\n[bold cyan]{escape(title)}[/bold cyan]")
    console.print(f"[dim]{horizontal_rule(width)}[/dim]")


def _latest_sleep(sleeps: list[WhoopSleep], now: datetime) -> WhoopSleep | None:
    available = [
        sleep
        for sleep in sleeps
        if sleep.score_state == "SCORED" and to_utc(sleep.end) <= now
    ]
    if not available:
        return None
    main_sleeps = [sleep for sleep in available if not sleep.nap]
    return max(main_sleeps or available, key=lambda sleep: to_utc(sleep.end))


def _latest_recovery(
    recoveries: list[WhoopRecovery],
    sleeps: list[WhoopSleep],
    now: datetime,
) -> WhoopRecovery | None:
    sleeps_by_id = {sleep.id: sleep for sleep in sleeps}
    linked = [
        recovery
        for recovery in recoveries
        if recovery.score_state == "SCORED"
        and recovery.sleep_id in sleeps_by_id
        and to_utc(sleeps_by_id[recovery.sleep_id].end) <= now
    ]
    if linked:
        return max(
            linked,
            key=lambda recovery: to_utc(sleeps_by_id[recovery.sleep_id].end),
        )

    scored = [recovery for recovery in recoveries if recovery.score_state == "SCORED"]
    if not scored:
        return None
    return max(scored, key=lambda recovery: recovery.cycle_id)


def _latest_cycle(cycles: list[WhoopCycle], now: datetime) -> WhoopCycle | None:
    available = [
        cycle
        for cycle in cycles
        if cycle.score_state == "SCORED"
        and cycle.end is not None
        and to_utc(cycle.end) <= now
    ]
    if not available:
        return None
    return max(available, key=lambda cycle: to_utc(cycle.end))


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


def _sleep_debt_marker(value: float | int | None) -> str:
    if value is None:
        return ""
    marker = sleep_debt_marker(value)
    if value < -90:
        return f"[red]{marker}[/red]"
    if value < 0:
        return f"[yellow]{marker}[/yellow]"
    return f"[green]{marker}[/green]"


def _compact_sync_line(sync_run) -> str:
    if sync_run is None:
        return "no sync yet"
    when = (
        format_compact_datetime(sync_run.completed_at)
        if sync_run.completed_at
        else "unknown"
    )
    return (
        f"{sync_run.sleeps_saved} sleeps, "
        f"{sync_run.recoveries_saved} recoveries, "
        f"{sync_run.cycles_saved} cycles | last sync {when}"
    )


def _compact_flags(result: ExamReadiness) -> str:
    if result.readiness_label == "UPCOMING":
        return "pending night data"
    stress = _stress_for_result(result)
    if stress:
        drivers = top_stress_drivers(stress.components, limit=2)
        if drivers:
            return ", ".join(_driver_display_name(driver.name) for driver in drivers)
    if result.flags:
        return ", ".join(result.flags)
    return "no major flags"


def _stress_for_result(result: ExamReadiness) -> StressResult | None:
    return compute_exam_stress_index(result)


def _stress_level_text(stress: StressResult | None) -> str:
    if stress is None:
        return "upcoming"
    return stress.label


def _stress_score_text(stress: StressResult | None) -> str:
    if stress is None:
        return "--"
    return f"{stress.score}"


def _driver_display_name(name: str) -> str:
    return {
        "sleep_debt": "sleep debt",
        "recovery_drop": "recovery drop",
        "hrv_pressure": "HRV pressure",
        "rhr_elevation": "RHR elevation",
        "strain_load": "strain load",
    }.get(name, name.replace("_", " "))


def _print_compact_today(
    *,
    exam: Exam,
    now: datetime,
    sleep: WhoopSleep | None,
    recovery: WhoopRecovery | None,
    cycle: WhoopCycle | None,
) -> None:
    _section("TODAY", width=40)
    console.print(f"[dim]{'next exam':<13}[/dim] {escape(exam.course)}")
    console.print(f"[dim]{'time':<13}[/dim] {format_compact_datetime(exam.exam_at)}")
    console.print(f"[dim]{'remaining':<13}[/dim] {_remaining_text(exam.exam_at, now)}")
    console.print(
        f"[dim]{'stress':<13}[/dim] "
        "[cyan]pending night-before data[/cyan]"
    )

    _section("LATEST WHOOP", width=40)
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


def _today_panel(
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
    table.add_row("Remaining", _remaining_text(exam.exam_at, now))
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


def _sync_message(sync_run) -> str:
    if sync_run is None:
        return "whoop: no sync yet"
    if sync_run.source == "demo":
        return (
            f"DEMO DATA: last seeded {sync_run.completed_at.isoformat() if sync_run.completed_at else 'unknown'} "
            f"({sync_run.sleeps_saved} sleeps, {sync_run.recoveries_saved} recoveries, "
            f"{sync_run.cycles_saved} cycles)"
        )
    return (
        f"whoop: last sync {sync_run.completed_at.isoformat() if sync_run.completed_at else 'unknown'} "
        f"({sync_run.sleeps_saved} sleeps, {sync_run.recoveries_saved} recoveries, "
        f"{sync_run.cycles_saved} cycles)"
    )


def _readiness_text(result: ExamReadiness) -> Text:
    if result.readiness_label == "UPCOMING":
        return Text("UPCOMING", style="bold cyan")
    score = "n/a" if result.readiness_score is None else f"{result.readiness_score:.0f}"
    label = f"{result.readiness_label} {score}"
    style = {
        "LOW": "bold red",
        "MODERATE": "bold yellow",
        "GOOD": "bold green",
        "UNKNOWN": "dim",
        "UPCOMING": "bold cyan",
    }.get(result.readiness_label, "dim")
    return Text(label, style=style)


def _print_exams_table(exams: list[Exam]) -> None:
    table = Table(title="EXAMS", box=box.ASCII)
    table.add_column("Course")
    table.add_column("Exam time")
    table.add_column("Grade")
    table.add_column("Notes")
    for exam in exams:
        table.add_row(
            exam.course,
            exam.exam_at.isoformat(),
            "n/a" if exam.grade is None else f"{exam.grade:g}",
            exam.notes,
        )
    console.print(_ascii_safe_table(table))


def _print_stream_error_samples(summary, *, debug: bool = False) -> None:
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


def _sleep_stream_forbidden(sync_run, raw_check_run) -> bool:
    return any(
        run is not None and SLEEP_STREAM_FORBIDDEN in (run.message or "")
        for run in (sync_run, raw_check_run)
    )


def _print_exam_window_hr(result) -> None:
    _section("EXAM WINDOW HR", width=40)
    console.print(f"[dim]{'exam':<10}[/dim] {escape(result.exam.course)}")
    console.print(
        f"[dim]{'window':<10}[/dim] "
        f"{result.window_start.astimezone().strftime('%H:%M')} - "
        f"{result.window_end.astimezone().strftime('%H:%M')}"
    )
    console.print(f"[dim]{'source':<10}[/dim] real raw HR")
    console.print(f"[dim]{'points':<10}[/dim] {result.points}")
    console.print(f"[dim]{'baseline':<10}[/dim] {_hr_text(result.avg_hr_baseline)}")
    console.print(f"[dim]{'exam avg':<10}[/dim] {_hr_text(result.avg_hr_exam)}")
    console.print(f"[dim]{'dbpm':<10}[/dim] {_bpm_delta_text(result.dbpm)}")
    console.print(f"[dim]{'z-ish':<10}[/dim] {_float_text(result.z_like)}")
    console.print(f"[dim]{'elevated':<10}[/dim] {_percent_text(result.elevated_percent)}")
    console.print(
        "\n[dim]note      [/dim] Real user-provided HR data compared against "
        "a 90-minute local baseline."
    )


def _print_whoop_raw_check(result) -> None:
    _section("WHOOP RAW ACCESS CHECK", width=40)
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


def _print_not_enough_raw_hr_data() -> None:
    console.print("[yellow]Not enough raw HR data for this exam.[/yellow]")
    console.print("Needed:")
    console.print("- 90-min baseline before exam")
    console.print("- HR points during exam window")
    console.print()
    console.print("Run:")
    console.print("python -m app.cli.main research raw-hr audit")
    console.print("to inspect imported data.")


def _print_raw_hr_audit(audit) -> None:
    _section("RAW HR DATA AUDIT", width=40)
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


def _hr_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f} bpm"


def _bpm_delta_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0f} bpm"


def _float_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def _percent_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}%"


def _print_compact_report(
    results: list[ExamReadiness],
    sync_run,
    demo_data: bool = False,
    sleeps: list[WhoopSleep] | None = None,
    sleep_stream_points: list | None = None,
    sleep_stream_forbidden: bool = False,
) -> None:
    ranked = sorted(results, key=_risk_order)
    analyzed = [result for result in ranked if result.readiness_label != "UPCOMING"]
    upcoming = [result for result in ranked if result.readiness_label == "UPCOMING"]
    sleeps = sleeps or []
    sleep_stream_points = sleep_stream_points or []
    night_hr_by_result = {
        id(result): (
            NightHRSignal(status="forbidden")
            if sleep_stream_forbidden
            else analyze_night_hr_signal(
                result,
                sleeps=sleeps,
                stream_points=sleep_stream_points,
            )
        )
        for result in ranked
    }

    _print_report_header(
        sync_run=sync_run,
        demo_data=demo_data,
        total=len(results),
        analyzed_n=len(analyzed),
        upcoming_n=len(upcoming),
    )

    if analyzed:
        _print_leaderboard(ranked)

    if analyzed:
        _section("EXAM DETAIL", width=74)
        for index, result in enumerate(analyzed):
            if index:
                console.print()
            _print_compact_exam_detail(result, night_hr_by_result[id(result)])

    if ranked:
        _section("STRESS DRIVERS", width=74)
        for index, result in enumerate(analyzed):
            if index:
                console.print()
            _print_compact_stress_drivers(result)

    if upcoming:
        _section("UPCOMING", width=74)
        now = utc_now()
        for index, result in enumerate(upcoming):
            if index:
                console.print()
            _print_compact_upcoming(result.exam, now, night_hr_by_result[id(result)])


def _ascii_safe_table(table: Table) -> Table:
    """Avoid Rich's unicode ellipsis when overflowing on non-UTF terminals."""
    if not _UNICODE:
        for column in table.columns:
            column.overflow = "crop"
    return table


def _print_report_header(
    *,
    sync_run,
    demo_data: bool,
    total: int,
    analyzed_n: int,
    upcoming_n: int,
) -> None:
    title = Text()
    title.append("EXAMPULSE", style="bold cyan")
    title.append(f"  {_DOT}  Exam Readiness", style="bold")
    if demo_data:
        title.append("    DEMO DATA", style="bold magenta")
    console.print(
        Panel(title, box=_BANNER_BOX, border_style="cyan", padding=(0, 1))
    )
    console.print(
        Text.assemble(("whoop  ", "bold cyan"), (_compact_sync_line(sync_run), "dim"))
    )
    console.print(
        Text.assemble(
            ("exams  ", "bold cyan"),
            (
                f"{total} total {_DOT} {analyzed_n} analyzed {_DOT} {upcoming_n} upcoming",
                "dim",
            ),
        )
    )
    method = "demo scoring" if demo_data else "scoring"
    sigma = "σ" if _UNICODE else "sd"
    console.print(
        Text.assemble(
            ("method ", "bold cyan"),
            (
                f"{method} vs 14-day baseline {_DOT} mean {_PLUSMINUS} {sigma}, "
                "z-scores, percentiles",
                "dim",
            ),
        )
    )


def _print_leaderboard(ranked: list[ExamReadiness]) -> None:
    _section("EXAM LEADERBOARD", width=74)
    console.print("[dim]night-before physiology vs personal baseline[/dim]")
    table = Table(
        box=_TABLE_BOX,
        header_style="bold",
        pad_edge=False,
        expand=False,
    )
    overflow = "ellipsis" if _UNICODE else "crop"
    table.add_column("Load", justify="right", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Ready", no_wrap=True)
    table.add_column("Sleep", justify="right", no_wrap=True)
    table.add_column("Rec", justify="right", no_wrap=True)
    table.add_column("Exam", no_wrap=True, overflow=overflow)
    table.add_column("Top drivers", no_wrap=True, overflow=overflow)
    for result in ranked:
        table.add_row(*_leaderboard_cells(result))
    console.print(_ascii_safe_table(table))


def _leaderboard_cells(result: ExamReadiness) -> list:
    upcoming = result.readiness_label == "UPCOMING"
    stress = _stress_for_result(result)
    stress_label = _stress_level_text(stress)
    stress_style = _stress_color(stress_label)
    load = _stress_score_text(stress)

    recovery_score = result.recovery.recovery_score if result.recovery else None
    sleep_delta = (
        "--" if upcoming else compact_duration(result.sleep_debt_minutes, signed=True)
    )
    drivers = "pending" if upcoming else _compact_flags(result)

    return [
        Text(load, style=stress_style),
        Text(stress_label, style=stress_style),
        Text(_short_readiness(result), style=status_color(result.readiness_label)),
        Text(sleep_delta, style=_sleep_delta_color(result.sleep_debt_minutes)),
        Text(
            "--" if upcoming else format_percent(recovery_score),
            style="dim" if upcoming else value_color(recovery_score),
        ),
        truncate(result.exam.course, 26),
        Text(truncate(drivers, 24), style="dim"),
    ]


def _short_readiness(result: ExamReadiness) -> str:
    if result.readiness_label == "UPCOMING":
        return "upcoming"
    if result.readiness_score is None:
        return result.readiness_label.casefold()
    return f"{result.readiness_label.casefold()} {result.readiness_score:.0f}"


def _sleep_delta_color(value: float | int | None) -> str:
    if value is None:
        return "dim"
    if value <= -120:
        return "red"
    if value < 0:
        return "yellow"
    return "green"


def _baseline_cell(mean_text: str, std: float | None, unit: str = "") -> str:
    if std is None:
        return f"[dim]{mean_text}[/dim]"
    return f"[dim]{mean_text} {_PLUSMINUS}{std:.0f}{unit}[/dim]"


def _zscore_cell(value: float | None) -> str:
    return f"[{zscore_color(value)}]{format_zscore(value)}[/]"


def _print_compact_exam_detail(result: ExamReadiness, night_hr: NightHRSignal | None = None) -> None:
    label = result.readiness_label.casefold()
    score = "--" if result.readiness_score is None else f"{result.readiness_score:.0f}"
    stress = _stress_for_result(result)
    load_label = "n/a" if stress is None else stress.label
    load_score = "--" if stress is None else f"{stress.score}/100"
    load_color = _stress_color(load_label)
    sleep_minutes = result.sleep.total_sleep_minutes if result.sleep else None
    recovery_score = result.recovery.recovery_score if result.recovery else None
    hrv = result.recovery.hrv_rmssd_milli if result.recovery else None
    rhr = result.recovery.resting_heart_rate if result.recovery else None
    strain = result.previous_cycle.strain if result.previous_cycle else None

    nights = result.baseline_nights
    nights_note = f"n={nights}" if nights else "n<2"
    console.print(
        f"[bold]{escape(result.exam.course)}[/bold] "
        f"[dim]{format_compact_datetime(result.exam.exam_at)}  {_DOT}  baseline {nights_note}[/dim]"
    )

    table = Table(box=_SIMPLE_BOX, pad_edge=False, header_style="dim", expand=False)
    table.add_column("metric", style="dim")
    table.add_column("night", justify="right")
    table.add_column("baseline (14d)", justify="right")
    table.add_column(_DELTA, justify="right")
    table.add_column("trend")
    table.add_column("z", justify="right")

    table.add_row(
        "sleep",
        compact_duration(sleep_minutes),
        _baseline_cell(
            compact_duration(result.baseline_sleep_minutes), result.baseline_sleep_std, "m"
        ),
        Text(
            compact_duration(result.sleep_debt_minutes, signed=True),
            style=_sleep_delta_color(result.sleep_debt_minutes),
        ),
        f"[cyan]{sparkline(result.sleep_series)}[/cyan]",
        _zscore_cell(result.sleep_z),
    )
    table.add_row(
        "recovery",
        Text(format_percent(recovery_score), style=value_color(recovery_score)),
        _baseline_cell(format_percent(result.baseline_recovery_score), result.baseline_recovery_std),
        _percentile_text(result.recovery_percentile),
        f"[cyan]{sparkline(result.recovery_series)}[/cyan]",
        _zscore_cell(result.recovery_z),
    )
    table.add_row(
        "hrv",
        f"{format_float(hrv)} ms" if hrv is not None else "n/a",
        _baseline_cell(format_float(result.baseline_hrv), result.baseline_hrv_std, "ms"),
        _delta_text(result.hrv_delta_percent, "%", good_high=True),
        f"[cyan]{sparkline(result.hrv_series)}[/cyan]",
        _zscore_cell(result.hrv_z),
    )
    table.add_row(
        "rhr",
        f"{rhr:.0f} bpm" if rhr is not None else "n/a",
        _baseline_cell(format_float(result.baseline_rhr), result.baseline_rhr_std, ""),
        _delta_text(result.rhr_delta_bpm, " bpm", good_high=False),
        f"[cyan]{sparkline(result.rhr_series)}[/cyan]",
        _zscore_cell(result.rhr_z),
    )
    table.add_row("strain", format_float(strain), "[dim]prev cycle[/dim]", "", "", "")
    console.print(_ascii_safe_table(table))

    console.print(
        f"[dim]{'score':<10}[/dim] "
        f"[{status_color(result.readiness_label)}]ready {label} {score:<3} "
        f"{colored_bar(result.readiness_score, width=12)}[/]   "
        f"[{load_color}]{load_label} {load_score:<7}[/] "
        f"[{load_color}]{stress_bar(stress.score if stress else None, width=12)}[/]"
    )
    console.print(f"[dim]{'note':<10}[/dim] {escape(result.summary)}")
    _print_night_hr_signal(night_hr)


def _percentile_text(value: float | None) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    color = value_color(value)
    return f"[{color}]p{value:.0f}[/]"


def _delta_text(value: float | None, unit: str, *, good_high: bool) -> Text:
    if value is None:
        return Text("n/a", style="dim")
    if good_high:
        style = "green" if value >= 0 else ("yellow" if value > -15 else "red")
    else:
        style = "green" if value <= 0 else ("yellow" if value < 5 else "red")
    return Text(f"{value:+.0f}{unit}", style=style)


def _print_night_hr_signal(signal: NightHRSignal | None) -> None:
    _section("NIGHT HR SIGNAL", width=40)
    if signal is not None and signal.status == "forbidden":
        console.print("[dim]status    [/dim] WHOOP sleep stream forbidden by API")
        console.print("[dim]source    [/dim] WHOOP band only")
        console.print(
            "[dim]note      [/dim] Summary data is available, but official raw sleep HR "
            "is blocked for this account/app."
        )
        return
    if signal is None or signal.status == "missing_stream":
        console.print("[dim]status    [/dim] no sleep stream data")
        return
    if signal.status == "missing_sleep":
        console.print("[dim]status    [/dim] no matching night-before sleep")
        return
    if signal.status == "pending":
        console.print("[dim]status    [/dim] pending night-before sleep stream data")
        return

    baseline = "low confidence"
    delta = ""
    if signal.baseline_hr is not None:
        baseline = f"{signal.baseline_hr:.0f} bpm"
        if signal.delta_bpm is not None:
            delta = f"    {signal.delta_bpm:+.0f} vs sleep baseline"
    console.print(f"[dim]{'points':<10}[/dim] {signal.points}")
    console.print(f"[dim]{'avg hr':<10}[/dim] {signal.avg_hr:.0f} bpm{delta}")
    console.print(f"[dim]{'max hr':<10}[/dim] {signal.max_hr:.0f} bpm")
    console.print(f"[dim]{'baseline':<10}[/dim] {baseline}")
    if signal.elevated_percent is None:
        console.print(f"[dim]{'elevated':<10}[/dim] low confidence")
        console.print(f"[dim]{'spikes':<10}[/dim] low confidence")
    else:
        console.print(f"[dim]{'elevated':<10}[/dim] {signal.elevated_percent:.0f}%")
        console.print(f"[dim]{'spikes':<10}[/dim] {signal.spike_count}")
    if signal.confidence == "low":
        console.print("[dim]note      [/dim] low confidence: not enough baseline sleep HR stream data")


def _stress_color(label: str) -> str:
    normalized = label.casefold()
    if normalized == "calm":
        return "green"
    if normalized == "mild load":
        return "yellow"
    if normalized == "elevated":
        return "yellow"
    if normalized == "high load":
        return "red"
    return "dim"


def _print_compact_stress_monitor(result: ExamReadiness) -> None:
    console.print(f"[bold]{escape(result.exam.course)}[/bold]")
    if result.readiness_label == "UPCOMING":
        console.print(
            f"[dim]{'status':<10}[/dim] "
            "[cyan]pending night-before WHOOP data[/cyan]"
        )
        return

    stress = compute_exam_stress_index(result)
    if stress is None:
        console.print(f"[dim]{'status':<10}[/dim] n/a")
        return

    color = _stress_color(stress.label)
    labels = {component.name: component.label for component in stress.components}
    recovery_score = result.recovery.recovery_score if result.recovery else None
    previous_strain = result.previous_cycle.strain if result.previous_cycle else None

    console.print(
        f"[dim]{'load':<10}[/dim] "
        f"[{color}]{stress.label} {stress.score}/100[/]    "
        f"[{color}]{stress_bar(stress.score)}[/]"
    )
    console.print(
        f"[dim]{'sleep':<10}[/dim] "
        f"{compact_duration(result.sleep_debt_minutes, signed=True):<18} "
        f"{labels['sleep_debt']}"
    )
    console.print(
        f"[dim]{'recovery':<10}[/dim] "
        f"{format_percent(recovery_score):<18} {labels['recovery_drop']}"
    )
    console.print(
        f"[dim]{'hrv':<10}[/dim] "
        f"{format_percent_delta(result.hrv_delta_percent):<18} {labels['hrv_pressure']}"
    )
    console.print(
        f"[dim]{'rhr':<10}[/dim] "
        f"{format_bpm_delta(result.rhr_delta_bpm):<18} {labels['rhr_elevation']}"
    )
    console.print(
        f"[dim]{'strain':<10}[/dim] "
        f"{format_float(previous_strain):<18} {labels['strain_load']}"
    )

    drivers = top_stress_drivers(stress.components)
    console.print(f"\n[dim]{'drivers':<10}[/dim]")
    if drivers:
        for driver in drivers:
            console.print(f"- {driver.name}: {driver.points}/{driver.max_points}")
    else:
        console.print("- no major physiological load drivers")
    console.print(
        f"\n[dim]{'note':<10}[/dim] This is a physiological load estimate, "
        "not a mental stress diagnosis."
    )


def _print_compact_stress_drivers(result: ExamReadiness) -> None:
    stress = _stress_for_result(result)
    if stress is None:
        _print_compact_stress_monitor(result)
        return

    console.print(f"[bold]{escape(result.exam.course)}[/bold]")
    table = Table(box=_SIMPLE_BOX, pad_edge=False, header_style="dim", expand=False)
    table.add_column("driver", style="dim")
    table.add_column("pts", justify="right")
    table.add_column("share")
    table.add_column("effect")
    table.add_column("note", style="dim")
    for component in stress.components:
        ratio_color = value_color(component.points, max_value=component.max_points, good_high=False)
        bar = make_bar(component.points, max_value=component.max_points, width=10)
        table.add_row(
            _driver_display_name(component.name),
            Text(f"+{component.points}", style=ratio_color),
            f"[{ratio_color}]{bar}[/]",
            Text(component.label, style=ratio_color if component.points else "dim"),
            component.note,
        )
    console.print(_ascii_safe_table(table))

    drivers = top_stress_drivers(stress.components)
    if drivers:
        driver = drivers[0]
        console.print(
            "[dim]top driver [/dim] "
            f"{_driver_display_name(driver.name)} (+{driver.points})"
        )
        console.print(
            f"[dim]{'load bar':<10}[/dim] "
            f"[{_stress_color(stress.label)}]{stress_bar(stress.score, width=16)}[/]"
        )
    else:
        console.print("[dim]top driver [/dim] no major physiological load driver")
    console.print(
        f"[dim]{'note':<10}[/dim] Physiological Load Index estimates body load "
        "from sleep, recovery, HRV, RHR, and strain."
    )
    console.print(
        f"{'':<10} It is not a mental stress diagnosis."
    )


def _print_compact_upcoming(
    exam: Exam,
    now: datetime,
    night_hr: NightHRSignal | None = None,
) -> None:
    console.print(f"[bold]{escape(exam.course)}[/bold]")
    console.print(f"[dim]{'time':<10}[/dim] {format_compact_datetime(exam.exam_at)}")
    console.print(f"[dim]{'remaining':<10}[/dim] {_remaining_text(exam.exam_at, now)}")
    console.print(
        f"[dim]{'status':<10}[/dim] "
        "[cyan]analysis pending night-before WHOOP data[/cyan]"
    )
    _print_night_hr_signal(night_hr or NightHRSignal(status="pending"))


def _print_report(
    results: list[ExamReadiness], sync_run_message: str, demo_data: bool = False
) -> None:
    title = "EXAMPULSE - EXAM READINESS"
    if demo_data:
        title = f"{title}\nDEMO DATA"
    console.print(Panel.fit(title, box=box.ASCII, style="bold"))
    console.print(f"[dim]{sync_run_message}[/dim]")
    console.print("[dim]matching sleep/recovery/cycle data against exams...[/dim]")

    ranked = sorted(results, key=_risk_order)
    table = Table(title="RANKED EXAMS", box=box.ASCII)
    table.add_column("Course")
    table.add_column("Readiness")
    table.add_column("Sleep debt")
    table.add_column("Recovery")
    table.add_column("HRV")
    table.add_column("RHR")
    table.add_column("Strain")

    for result in ranked:
        table.add_row(
            result.exam.course,
            _readiness_text(result),
            minutes_to_hm(result.sleep_debt_minutes, signed=True),
            format_percent(result.recovery.recovery_score if result.recovery else None),
            format_percent_delta(result.hrv_delta_percent),
            format_bpm_delta(result.rhr_delta_bpm),
            format_float(result.previous_cycle.strain if result.previous_cycle else None),
        )
    console.print(_ascii_safe_table(table))

    for result in ranked:
        console.print(_detail_panel(result))


def _detail_panel(result: ExamReadiness) -> Panel:
    if result.readiness_label == "UPCOMING":
        lines = [
            f"Course:       {result.exam.course}",
            f"Time:         {result.exam.exam_at.isoformat()}",
            f"Notes:        {result.exam.notes or 'n/a'}",
            "",
            result.summary,
        ]
        return Panel(
            "\n".join(lines),
            title=f"DETAIL - {result.exam.course}",
            box=box.ASCII,
            border_style="cyan",
        )

    sleep_minutes = result.sleep.total_sleep_minutes if result.sleep else None
    baseline = result.baseline_sleep_minutes
    lines = [
        f"Time:         {result.exam.exam_at.isoformat()}",
        f"Night before: {minutes_to_hm(sleep_minutes)}",
        f"Baseline:     {minutes_to_hm(baseline)}",
        f"Sleep debt:   {minutes_to_hm(result.sleep_debt_minutes, signed=True)}",
        f"Recovery:     {format_percent(result.recovery.recovery_score if result.recovery else None)}",
        f"HRV delta:    {format_percent_delta(result.hrv_delta_percent)}",
        f"RHR delta:    {format_bpm_delta(result.rhr_delta_bpm)}",
        f"Prev strain:  {format_float(result.previous_cycle.strain if result.previous_cycle else None)}",
        f"Flags:        {', '.join(result.flags)}",
        "",
        result.summary,
    ]
    return Panel(
        "\n".join(lines),
        title=f"DETAIL - {result.exam.course}",
        box=box.ASCII,
        border_style={
            "LOW": "red",
            "MODERATE": "yellow",
            "GOOD": "green",
            "UNKNOWN": "dim",
        }.get(result.readiness_label, "dim"),
    )


def _demo_result() -> ExamReadiness:
    exam_at = datetime.now(UTC) + timedelta(days=1)
    exam = Exam(course="Demo Exam", exam_at=exam_at, notes="demo")
    sleep = WhoopSleep(
        id="demo-sleep",
        cycle_id=1,
        start=exam_at - timedelta(hours=10),
        end=exam_at - timedelta(hours=4),
        nap=False,
        score_state="SCORED",
        total_sleep_minutes=275,
        sleep_performance_percentage=62,
    )
    recovery = WhoopRecovery(
        sleep_id="demo-sleep",
        cycle_id=1,
        score_state="SCORED",
        recovery_score=38,
        resting_heart_rate=68,
        hrv_rmssd_milli=31,
    )
    cycle = WhoopCycle(
        id=1,
        start=exam_at - timedelta(days=1),
        end=exam_at - timedelta(hours=12),
        score_state="SCORED",
        strain=14.2,
    )
    return ExamReadiness(
        exam=exam,
        sleep=sleep,
        recovery=recovery,
        previous_cycle=cycle,
        baseline_sleep_minutes=405,
        baseline_recovery_score=55,
        baseline_hrv=38,
        baseline_rhr=60,
        sleep_debt_minutes=-130,
        recovery_delta=-17,
        hrv_delta_percent=-18,
        rhr_delta_bpm=8,
        readiness_score=34,
        readiness_label="LOW",
        flags=["low sleep", "low recovery", "HRV below baseline", "elevated resting HR"],
        summary=(
            "Your physiological readiness before this exam was lower than usual. "
            "Main factors: low sleep, low recovery, HRV below baseline, elevated resting HR. "
            "These are context signals, not proof of causation."
        ),
    )


if __name__ == "__main__":
    app()
