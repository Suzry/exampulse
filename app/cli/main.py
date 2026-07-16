from __future__ import annotations

import json
import time
import zipfile
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import typer

from app.cli.views import (
    correlate_view,
    plan_view,
    report_view,
    today_view,
    week_view,
    whoop_views,
)
from app.cli.views.common import console
from app.core.correlation import correlate as compute_correlation
from app.core.correlation import exam_outcomes
from app.core.exam_hr import match_exam_activity
from app.core.planning import (
    DEFAULT_PREP_BUFFER_MINUTES,
    plan_exam,
    short_recovery_windows,
)
from app.core.selectors import (
    latest_cycle,
    latest_recovery,
    latest_sleep,
    next_upcoming_exam,
    upcoming_exams,
)
from app.integrations.whoop_client import WhoopAPIError
from app.integrations.whoop_oauth import OAuthError, run_local_oauth_flow
from app.research.raw_hr.service import RawHRDataError, RawHRService
from app.services.apple_health_service import (
    AppleHealthImportError,
    AppleHealthService,
)
from app.services.demo_seed_service import DemoSeedService
from app.services.exam_service import ExamImportError, ExamService
from app.services.export_service import ExportService
from app.services.insight_service import InsightService
from app.services.semester_service import build_semester_report
from app.services.sync_service import SyncService
from app.services.whoop_export_service import WhoopExportError, WhoopExportService
from app.services.whoop_raw_check_service import (
    SLEEP_STREAM_FORBIDDEN,
    WhoopRawCheckService,
)
from app.storage.db import get_session, init_db
from app.storage.repositories import (
    has_demo_data,
    latest_sync_run,
    latest_whoop_raw_check,
    list_cycles,
    list_recoveries,
    list_research_raw_hr_points,
    list_sleep_stream_points,
    list_sleeps,
    list_whoop_workouts,
)
from app.utils.time import utc_now

app = typer.Typer(no_args_is_help=True)
exams_app = typer.Typer(help="Import and list exams.")
research_app = typer.Typer(help="Research tools for user-owned datasets.")
raw_hr_app = typer.Typer(help="User-provided raw heart-rate CSV tools.")
whoop_app = typer.Typer(help="WHOOP official API checks.")
apple_app = typer.Typer(help="Apple Health export import.")
app.add_typer(exams_app, name="exams")
research_app.add_typer(raw_hr_app, name="raw-hr")
app.add_typer(research_app, name="research")
app.add_typer(whoop_app, name="whoop")
app.add_typer(apple_app, name="apple")


def _version_callback(value: bool) -> None:
    if not value:
        return
    try:
        console.print(f"exampulse {package_version('exampulse')}")
    except PackageNotFoundError:
        console.print("exampulse (not installed)")
    raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the Exampulse version and exit.",
    ),
) -> None:
    """CLI-first WHOOP exam readiness analyzer."""


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
                whoop_views.print_stream_error_samples(summary, debug=debug_streams)
    except (OAuthError, WhoopAPIError) as exc:
        console.print(f"[red]Sync failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@exams_app.command("import")
def import_exams(
    path: Path = typer.Argument(..., exists=True, readable=True),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Replace existing exams with the file contents.",
    ),
) -> None:
    """Import exams from a JSON or iCal (.ics) file."""
    _ensure_db()
    try:
        with get_session() as session:
            imported = ExamService(session).import_file(path, replace=replace)
        console.print(f"[green]Imported {len(imported)} exam(s).[/green]")
    except (ExamImportError, OSError, ValueError) as exc:
        console.print(f"[red]Exam import failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@exams_app.command("list")
def list_exam_command(
    json_output: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of the table."
    ),
) -> None:
    """List imported exams."""
    _ensure_db()
    with get_session() as session:
        exams = ExamService(session).list()
    if json_output:
        # Plain print, not console.print: Rich soft-wraps long lines at
        # terminal width, which would inject newlines into the JSON.
        print(
            json.dumps(
                [
                    {
                        "course": exam.course,
                        "exam_at": exam.exam_at.isoformat(),
                        "grade": exam.grade,
                        "letter_grade": exam.letter_grade,
                        "notes": exam.notes,
                    }
                    for exam in exams
                ],
                indent=2,
            )
        )
        return
    if not exams:
        console.print("No exams imported yet. Try: exampulse exams import exams.json")
        return
    whoop_views.print_exams_table(exams)


@raw_hr_app.command("import-csv")
def import_raw_hr_csv(
    path: Path = typer.Argument(..., exists=True, readable=True),
    source: str = typer.Option(
        "whoop_export",
        "--source",
        help="Source label for this WHOOP-owned HR export.",
    ),
    timestamp_col: str | None = typer.Option(
        None, "--timestamp-col", help="Override the timestamp column name."
    ),
    hr_col: str | None = typer.Option(
        None, "--hr-col", help="Override the heart-rate column name."
    ),
) -> None:
    """Import real WHOOP-owned per-minute raw HR points from CSV.

    Timestamp and HR columns are auto-detected (timestamp/time/datetime, hr/bpm/
    heart rate, ...). Use --timestamp-col / --hr-col to override.
    """
    _ensure_db()
    try:
        with get_session() as session:
            summary = RawHRService(session).import_csv(
                path, source=source, timestamp_col=timestamp_col, hr_col=hr_col
            )
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
    whoop_views.print_raw_hr_audit(audit)


@raw_hr_app.command("exam-window")
def research_exam_window(
    exam: str = typer.Option(..., "--exam", help="Course name or substring."),
    source: str | None = typer.Option(None, "--source", help="Optional source filter."),
) -> None:
    """Compare exam-window HR against the 90-minute local baseline."""
    _ensure_db()
    try:
        with get_session() as session:
            result = RawHRService(session).exam_window(exam, source=source)
    except RawHRDataError as exc:
        whoop_views.print_not_enough_raw_hr_data()
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]Exam-window HR failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    whoop_views.print_exam_window_hr(result)


@whoop_app.command("raw-check")
def whoop_raw_check() -> None:
    """Check official WHOOP raw HR availability safely."""
    _ensure_db()
    with get_session() as session:
        result = WhoopRawCheckService(session).check()
    whoop_views.print_whoop_raw_check(result)


@whoop_app.command("import-export")
def whoop_import_export(
    path: Path = typer.Argument(..., exists=True, readable=True),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Clear existing synced WHOOP sleep/recovery/cycle data first.",
    ),
    source: str = typer.Option(
        "whoop_export",
        "--source",
        help="Source label for these WHOOP-owned records.",
    ),
) -> None:
    """Import the official WHOOP data export (activities + daily summary).

    PATH can be the export .zip, the unzipped folder, or a single CSV file. This
    loads workouts (per-activity HR) and the per-cycle recovery/sleep/strain
    summary, so the readiness report runs offline without the API or ngrok.
    """
    _ensure_db()
    try:
        with get_session() as session:
            summary = WhoopExportService(session).import_export(
                path, source=source, replace=replace
            )
    except WhoopExportError as exc:
        console.print(f"[red]WHOOP export import failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if summary.replaced:
        console.print("[magenta]Cleared existing WHOOP sleep/recovery/cycle data.[/magenta]")
    console.print(
        "[green]WHOOP daily summary imported:[/green] "
        f"{summary.cycles_saved} cycles, {summary.sleeps_saved} sleeps, "
        f"{summary.recoveries_saved} recoveries."
    )
    console.print(
        "[green]WHOOP activities imported:[/green] "
        f"{summary.workouts_saved} activities "
        f"({summary.activities_with_hr} with heart-rate data)."
    )
    console.print(
        "[dim]Note: WHOOP exports per-activity HR summaries and per-day "
        "recovery/sleep, not a minute-by-minute timeline.[/dim]"
    )


@whoop_app.command("exam-hr")
def whoop_exam_hr(
    exam: str | None = typer.Option(None, "--exam", help="Filter by course name."),
) -> None:
    """Show heart rate during each exam window from logged WHOOP activities."""
    _ensure_db()
    with get_session() as session:
        exams = ExamService(session).list()
        workouts = list_whoop_workouts(session)
    if exam:
        needle = exam.casefold()
        exams = [item for item in exams if needle in item.course.casefold()]
    if not exams:
        console.print("[yellow]No matching exams found.[/yellow]")
        return
    results = [match_exam_activity(item, workouts) for item in exams]
    whoop_views.print_exam_hr(results, has_workouts=bool(workouts))


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


def _sleep_stream_forbidden(sync_run, raw_check_run) -> bool:
    return any(
        run is not None and SLEEP_STREAM_FORBIDDEN in (run.message or "")
        for run in (sync_run, raw_check_run)
    )


@app.command()
def report(
    exam: str | None = typer.Option(None, "--exam", help="Filter by course name."),
    compact: bool = typer.Option(
        True,
        "--compact/--classic",
        help="Use the compact dashboard layout or the classic boxed report.",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Show per-exam detail, stress drivers, and HR (default is a brief table).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON (one object per exam) instead of the dashboard.",
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
        workouts = list_whoop_workouts(session)
        raw_hr_points = list_research_raw_hr_points(session)

    if not results:
        if not json_output:
            console.print("[yellow]No imported exams found. Showing demo output.[/yellow]")
        results = [report_view.demo_result()]
    if json_output:
        report_view.print_report_json(results)
        return
    if compact:
        report_view.print_compact_report(
            results,
            sync_run=sync_run,
            demo_data=demo_data,
            sleeps=sleeps,
            sleep_stream_points=sleep_stream_points,
            sleep_stream_forbidden=sleep_stream_forbidden,
            workouts=workouts,
            raw_hr_points=raw_hr_points,
            full=full,
        )
    else:
        report_view.print_report(
            results,
            sync_run_message=report_view.sync_message(sync_run),
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

    next_exam = next_upcoming_exam(exams, now)
    if next_exam is None:
        console.print("[yellow]No upcoming exams found.[/yellow]")
        return

    sleep = latest_sleep(sleeps, now)
    recovery = latest_recovery(recoveries, sleeps, now)
    cycle = latest_cycle(cycles, now)
    if compact:
        today_view.print_compact_today(
            exam=next_exam,
            now=now,
            sleep=sleep,
            recovery=recovery,
            cycle=cycle,
        )
    else:
        console.print(
            today_view.today_panel(
                exam=next_exam,
                now=now,
                sleep=sleep,
                recovery=recovery,
                cycle=cycle,
            )
        )


@app.command()
def plan(
    prep_buffer: int = typer.Option(
        DEFAULT_PREP_BUFFER_MINUTES,
        "--prep-buffer",
        min=0,
        help="Minutes reserved before the exam for waking up, food, and travel.",
    ),
) -> None:
    """Bedtime targets and readiness projections for upcoming exams."""
    _ensure_db()
    now = utc_now()
    with get_session() as session:
        exams = ExamService(session).list()
        sleeps = list_sleeps(session)
        recoveries = list_recoveries(session)

    upcoming = upcoming_exams(exams, now)
    plans = [
        plan_exam(
            exam, sleeps, recoveries, now, prep_buffer_minutes=prep_buffer
        )
        for exam in upcoming
    ]
    target_sleeps = [p.target_sleep_minutes for p in plans]
    windows = short_recovery_windows(
        upcoming,
        target_sleep_minutes=min(target_sleeps) if target_sleeps else 480.0,
        prep_buffer_minutes=prep_buffer,
    )
    plan_view.print_plan(plans, windows, now)


@app.command()
def week(
    days: int = typer.Option(7, "--days", min=1, help="Days ahead to show."),
) -> None:
    """Exam-week overview with short-recovery-window warnings."""
    _ensure_db()
    now = utc_now()
    with get_session() as session:
        exams = ExamService(session).list()
        sleeps = list_sleeps(session)

    plans_target = 480.0
    upcoming = upcoming_exams(exams, now)
    if upcoming and sleeps:
        first_plan = plan_exam(upcoming[0], sleeps, [], now)
        plans_target = first_plan.target_sleep_minutes
    windows = short_recovery_windows(exams, target_sleep_minutes=plans_target)
    week_view.print_week(exams, windows, now, days=days)


@app.command()
def correlate() -> None:
    """Correlate night-before physiology with recorded exam grades."""
    _ensure_db()
    with get_session() as session:
        results = InsightService(session).generate()

    outcomes = exam_outcomes(results)

    def paired(attribute: str):
        pairs = [
            (getattr(outcome, attribute), outcome.grade)
            for outcome in outcomes
            if getattr(outcome, attribute) is not None
        ]
        return compute_correlation(
            [pair[0] for pair in pairs], [pair[1] for pair in pairs]
        )

    correlate_view.print_correlation_report(
        outcomes,
        readiness_grade=paired("readiness"),
        stress_grade=paired("stress"),
        sleep_grade=paired("sleep_debt_minutes"),
    )


@app.command()
def semester(
    output: Path = typer.Option(
        Path("semester-report.md"),
        "--output",
        help="Where to write the markdown report.",
    ),
    stdout: bool = typer.Option(
        False, "--stdout", help="Print the markdown instead of writing a file."
    ),
) -> None:
    """Write an end-of-semester markdown summary of readiness, stress, and grades."""
    _ensure_db()
    with get_session() as session:
        results = InsightService(session).generate()
    markdown = build_semester_report(results)
    if stdout:
        print(markdown, end="")
        return
    output.write_text(markdown, encoding="utf-8")
    console.print(f"[green]Semester report written:[/green] {output}")


@apple_app.command("import-export")
def apple_import_export(
    path: Path = typer.Argument(..., exists=True, readable=True),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Clear existing synced sleep/recovery/cycle data first.",
    ),
) -> None:
    """Import an Apple Health export (export.zip, folder, or export.xml).

    Loads sleep sessions, nightly HRV (SDNN), and resting heart rate into the
    same tables the readiness report reads, so Exampulse works with an Apple
    Watch instead of WHOOP.
    """
    _ensure_db()
    try:
        with get_session() as session:
            summary = AppleHealthService(session).import_export(path, replace=replace)
    except (AppleHealthImportError, OSError, zipfile.BadZipFile) as exc:
        console.print(f"[red]Apple Health import failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if summary.replaced:
        console.print("[magenta]Cleared existing sleep/recovery/cycle data.[/magenta]")
    console.print(
        "[green]Apple Health imported:[/green] "
        f"{summary.sleeps_saved} sleep sessions, "
        f"{summary.recoveries_saved} recovery rows "
        f"(HRV on {summary.hrv_days} day(s), RHR on {summary.rhr_days} day(s))."
    )
    console.print(
        "[dim]Note: Apple has no recovery score; that component is skipped and "
        "the readiness weights renormalize. Apple HRV is SDNN, not WHOOP's "
        "RMSSD — deltas vs your own baseline stay meaningful.[/dim]"
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


if __name__ == "__main__":
    app()
