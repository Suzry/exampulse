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
from app.services.insight_service import InsightService
from app.services.sync_service import SyncService
from app.storage.db import get_session, init_db
from app.storage.repositories import (
    has_demo_data,
    latest_sync_run,
    list_cycles,
    list_recoveries,
    list_sleeps,
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
    compact_duration,
    extract_room_from_notes,
    format_compact_datetime,
    horizontal_rule,
    make_bar,
    sleep_debt_marker,
    status_color,
    truncate,
)

console = Console()
app = typer.Typer(no_args_is_help=True)
exams_app = typer.Typer(help="Import and list exams.")
app.add_typer(exams_app, name="exams")


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
def sync(days: int = typer.Option(30, "--days", min=1, help="Days of WHOOP history.")) -> None:
    """Sync official WHOOP sleep, recovery, and cycle data."""
    _ensure_db()
    try:
        with get_session() as session:
            summary = SyncService(session).sync(days=days)
        console.print(
            "[green]Sync complete:[/green] "
            f"{summary.sleeps_saved} sleeps, "
            f"{summary.recoveries_saved} recoveries, "
            f"{summary.cycles_saved} cycles, "
            f"{summary.skipped_records} pending/skipped."
        )
    except (OAuthError, WhoopAPIError) as exc:
        console.print(f"[red]Sync failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@exams_app.command("import")
def import_exams(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Import exams from a JSON file."""
    _ensure_db()
    try:
        with get_session() as session:
            imported = ExamService(session).import_file(path)
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
        demo_data = has_demo_data(session) or bool(sync_run and sync_run.source == "demo")

    if not results:
        console.print("[yellow]No imported exams found. Showing demo output.[/yellow]")
        results = [_demo_result()]
    if compact:
        _print_compact_report(results, sync_run=sync_run, demo_data=demo_data)
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
    bar = make_bar(recovery.recovery_score)
    return f"{score:<4} [green]{bar}[/green]"


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
        f"[dim]{'stress monitor':<15}[/dim] "
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
    console.print(table)


def _print_compact_report(results: list[ExamReadiness], sync_run, demo_data: bool = False) -> None:
    ranked = sorted(results, key=_risk_order)
    analyzed = [result for result in ranked if result.readiness_label != "UPCOMING"]
    upcoming = [result for result in ranked if result.readiness_label == "UPCOMING"]

    console.print(Text.assemble(("[whoop] ", "cyan"), _compact_sync_line(sync_run)))
    console.print(
        Text.assemble(
            ("[exams] ", "cyan"),
            f"exams.json -> {len(results)} exams, "
            f"{len(analyzed)} analyzed, {len(upcoming)} upcoming",
        )
    )
    run_label = "demo scoring" if demo_data else "scoring"
    console.print(
        Text.assemble(
            ("[run] ", "cyan"),
            f"{run_label} each exam vs 14-day personal baseline ...",
        )
    )
    console.print(
        Text.assemble(
            ("[run] ", "cyan"),
            "estimating night-before physiological load ...",
        )
    )

    _section("EXAM STRESS / READINESS")
    console.print("[dim]night-before physiology vs personal baseline[/dim]")
    console.print(
        f"[dim]{'phys load':<12} {'readiness':<10} {'exam':<30} {'flags'}[/dim]"
    )
    for result in ranked:
        readiness = result.readiness_label.casefold()
        stress = _stress_for_result(result)
        load = _stress_score_text(stress)
        level = _stress_level_text(stress)
        console.print(
            Text.assemble(
                (f"{load} {level:<10} ", _stress_color(level)),
                (f"{readiness:<10} ", status_color(result.readiness_label)),
                f"{truncate(result.exam.course, 30):<30} ",
                truncate(_compact_flags(result), 24),
            )
        )

    if analyzed:
        _section("DETAIL")
        for index, result in enumerate(analyzed):
            if index:
                console.print()
            _print_compact_exam_detail(result)

    if ranked:
        _section("STRESS DRIVERS")
        for index, result in enumerate(analyzed):
            if index:
                console.print()
            _print_compact_stress_drivers(result)

    if upcoming:
        _section("UPCOMING")
        now = utc_now()
        for index, result in enumerate(upcoming):
            if index:
                console.print()
            _print_compact_upcoming(result.exam, now)


def _print_compact_exam_detail(result: ExamReadiness) -> None:
    label = result.readiness_label.casefold()
    score = "--" if result.readiness_score is None else f"{result.readiness_score:.0f}"
    stress = _stress_for_result(result)
    load_label = "n/a" if stress is None else stress.label
    load_score = "--" if stress is None else f"{stress.score}/100"
    load_color = _stress_color(load_label)
    sleep_minutes = result.sleep.total_sleep_minutes if result.sleep else None
    recovery_score = result.recovery.recovery_score if result.recovery else None
    sleep_line = (
        f"{compact_duration(sleep_minutes)} vs "
        f"{compact_duration(result.baseline_sleep_minutes)} baseline"
    )
    sleep_debt = compact_duration(result.sleep_debt_minutes, signed=True)

    console.print(f"[bold]{escape(result.exam.course)}[/bold]")
    console.print(f"[dim]{'time':<10}[/dim] {format_compact_datetime(result.exam.exam_at)}")
    console.print(
        f"[dim]{'sleep':<10}[/dim] {sleep_line:<30} "
        f"{sleep_debt} {_sleep_debt_marker(result.sleep_debt_minutes)}"
    )
    console.print(
        f"[dim]{'recovery':<10}[/dim] "
        f"{format_percent(recovery_score):<5} [green]{make_bar(recovery_score)}[/green]"
    )
    console.print(f"[dim]{'hrv':<10}[/dim] {format_percent_delta(result.hrv_delta_percent)}")
    console.print(f"[dim]{'rhr':<10}[/dim] {format_bpm_delta(result.rhr_delta_bpm)}")
    console.print(
        f"[dim]{'strain':<10}[/dim] "
        f"{format_float(result.previous_cycle.strain if result.previous_cycle else None)}"
    )
    console.print(
        f"\n[dim]{'readiness':<10}[/dim] "
        f"[{status_color(result.readiness_label)}]{label} {score:<5}[/] "
        f"[{status_color(result.readiness_label)}]{make_bar(result.readiness_score)}[/]"
    )
    console.print(
        f"[dim]{'phys load':<10}[/dim] "
        f"[{load_color}]{load_label} {load_score:<7}[/] "
        f"[{load_color}]{stress_bar(stress.score if stress else None)}[/]"
    )
    console.print(f"\n[dim]{'note':<10}[/dim] {escape(result.summary)}")


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
    console.print(f"[dim]{'driver':<18} {'points':>6}   {'effect':<12} note[/dim]")
    for component in stress.components:
        console.print(
            f"{_driver_display_name(component.name):<18} "
            f"+{component.points:<5}   "
            f"{component.label:<12} "
            f"{component.note}"
        )

    drivers = top_stress_drivers(stress.components)
    if drivers:
        driver = drivers[0]
        console.print(
            "[dim]top driver [/dim] "
            f"{_driver_display_name(driver.name)} (+{driver.points})"
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


def _print_compact_upcoming(exam: Exam, now: datetime) -> None:
    room = extract_room_from_notes(exam.notes)
    console.print(f"[bold]{escape(exam.course)}[/bold]")
    console.print(f"[dim]{'time':<10}[/dim] {format_compact_datetime(exam.exam_at)}")
    console.print(f"[dim]{'room':<10}[/dim] {escape(room or 'n/a')}")
    console.print(f"[dim]{'remaining':<10}[/dim] {_remaining_text(exam.exam_at, now)}")
    console.print(
        f"[dim]{'status':<10}[/dim] "
        "[cyan]analysis pending night-before WHOOP data[/cyan]"
    )


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
    console.print(table)

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
