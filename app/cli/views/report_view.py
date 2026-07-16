from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.cli.views.common import (
    DELTA,
    DOT,
    SIMPLE_BOX,
    UNICODE,
    ascii_safe_table,
    awake_color,
    baseline_cell,
    console,
    delta_color,
    delta_text,
    downsample,
    driver_display_name,
    percentile_text,
    remaining_text,
    section,
    section_sub,
    sleep_delta_color,
    stress_color,
    zone_bar,
    zscore_cell,
)
from app.core.analysis import ExamReadiness
from app.core.exam_hr import (
    ExamActivityHR,
    ExamRawHR,
    PreExamHR,
    exam_window_hr,
    match_exam_activity,
    pre_exam_window_hr,
)
from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.core.night_hr import (
    NightHRSignal,
    analyze_night_hr_from_raw,
    analyze_night_hr_signal,
)
from app.core.scoring import get_scoring_config
from app.core.stress import (
    StressResult,
    compute_exam_stress_index,
    stress_bar,
    top_stress_drivers,
)
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
    make_bar,
    sparkline,
    status_color,
    truncate,
    value_color,
)
from app.utils.time import utc_now


def _baseline_days() -> int:
    return get_scoring_config().baseline_window_days


def risk_order(result: ExamReadiness) -> tuple[int, float]:
    order = {"LOW": 0, "MODERATE": 1, "GOOD": 2, "UNKNOWN": 3, "UPCOMING": 4}
    score = result.readiness_score if result.readiness_score is not None else 999
    return order.get(result.readiness_label, 3), float(score)


def stress_for_result(result: ExamReadiness) -> StressResult | None:
    return compute_exam_stress_index(result)


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


def sync_message(sync_run) -> str:
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


def night_hr_signal_for_result(
    result: ExamReadiness,
    *,
    sleeps: list,
    sleep_stream_points: list,
    sleep_stream_forbidden: bool,
    raw_hr_points: list,
) -> NightHRSignal:
    """Night-before sleep HR: official WHOOP stream, else imported raw HR."""
    signal: NightHRSignal | None = None
    if not sleep_stream_forbidden:
        signal = analyze_night_hr_signal(
            result, sleeps=sleeps, stream_points=sleep_stream_points
        )
    # The official stream is blocked (403) or empty for most accounts; fall back
    # to user-imported per-minute HR covering the night-before sleep window.
    if raw_hr_points and (
        signal is None or signal.status in {"missing_stream", "forbidden"}
    ):
        raw_signal = analyze_night_hr_from_raw(
            result, sleeps=sleeps, points=raw_hr_points
        )
        if raw_signal.status == "ok":
            return raw_signal
    if signal is not None:
        return signal
    return NightHRSignal(status="forbidden" if sleep_stream_forbidden else "missing_stream")


def result_to_json(result: ExamReadiness) -> dict:
    stress = stress_for_result(result) if result.readiness_label != "UPCOMING" else None
    return {
        "course": result.exam.course,
        "exam_at": result.exam.exam_at.isoformat(),
        "status": "upcoming" if result.readiness_label == "UPCOMING" else "analyzed",
        "readiness_score": result.readiness_score,
        "readiness_label": result.readiness_label,
        "physiological_stress": (
            {"score": stress.score, "label": stress.label} if stress is not None else None
        ),
        "grade": result.exam.grade,
        "letter_grade": result.exam.letter_grade,
        "baseline_nights": result.baseline_nights,
        "sleep_debt_minutes": result.sleep_debt_minutes,
        "recovery_delta": result.recovery_delta,
        "hrv_delta_percent": result.hrv_delta_percent,
        "rhr_delta_bpm": result.rhr_delta_bpm,
        "summary": result.summary,
    }


def print_report_json(results: list[ExamReadiness]) -> None:
    ranked = sorted(results, key=risk_order)
    # Plain print, not console.print: Rich soft-wraps long lines at terminal
    # width, which would inject newlines into the JSON and break parsing.
    print(json.dumps([result_to_json(result) for result in ranked], indent=2))


def print_compact_report(
    results: list[ExamReadiness],
    sync_run,
    demo_data: bool = False,
    sleeps: list[WhoopSleep] | None = None,
    sleep_stream_points: list | None = None,
    sleep_stream_forbidden: bool = False,
    workouts: list | None = None,
    raw_hr_points: list | None = None,
    full: bool = False,
) -> None:
    ranked = sorted(results, key=risk_order)
    analyzed = [result for result in ranked if result.readiness_label != "UPCOMING"]
    upcoming = [result for result in ranked if result.readiness_label == "UPCOMING"]
    sleeps = sleeps or []
    sleep_stream_points = sleep_stream_points or []
    workouts = workouts or []
    raw_hr_points = raw_hr_points or []
    activity_hr_by_result = {
        id(result): match_exam_activity(result.exam, workouts) for result in ranked
    }
    raw_hr_by_result = {
        id(result): (
            exam_window_hr(result.exam, raw_hr_points) if raw_hr_points else None
        )
        for result in ranked
    }
    pre_exam_hr_by_result = {
        id(result): (
            pre_exam_window_hr(result.exam, raw_hr_points) if raw_hr_points else None
        )
        for result in ranked
    }
    night_hr_by_result = {
        id(result): night_hr_signal_for_result(
            result,
            sleeps=sleeps,
            sleep_stream_points=sleep_stream_points,
            sleep_stream_forbidden=sleep_stream_forbidden,
            raw_hr_points=raw_hr_points,
        )
        for result in ranked
    }

    _print_process_header(
        sync_run=sync_run,
        demo_data=demo_data,
        total=len(results),
        analyzed_n=len(analyzed),
        upcoming_n=len(upcoming),
    )

    if analyzed:
        _print_exam_stress(analyzed)
        _print_readiness_chart(analyzed)

    if not full:
        if upcoming:
            _print_upcoming_brief(upcoming)
        _print_footer_note()
        return

    if analyzed:
        section("EXAM DETAIL", width=74)
        for index, result in enumerate(analyzed):
            if index:
                console.print()
            print_compact_exam_detail(
                result,
                night_hr_by_result[id(result)],
                activity_hr=activity_hr_by_result[id(result)],
                raw_hr=raw_hr_by_result[id(result)],
                pre_exam_hr=pre_exam_hr_by_result[id(result)],
            )

    if ranked:
        section("STRESS DRIVERS", width=74)
        for index, result in enumerate(analyzed):
            if index:
                console.print()
            print_compact_stress_drivers(result)

    if upcoming:
        section("UPCOMING", width=74)
        now = utc_now()
        for index, result in enumerate(upcoming):
            if index:
                console.print()
            print_compact_upcoming(result.exam, now, night_hr_by_result[id(result)])


def _print_process_header(
    *,
    sync_run,
    demo_data: bool,
    total: int,
    analyzed_n: int,
    upcoming_n: int,
) -> None:
    def line(tag: str, body: str) -> None:
        console.print(Text.assemble((tag, "bold cyan"), (" " + body, "dim")))

    if demo_data:
        console.print(Text("[ DEMO DATA ]", style="bold magenta"))
    line("[whoop]", _compact_sync_line(sync_run))
    label = "demo scoring" if demo_data else "scoring"
    line(
        "[exams]",
        f"exams.json -> {total} exams, {analyzed_n} analyzed, {upcoming_n} upcoming",
    )
    line(
        "[run]  ",
        f"{label} each exam vs {_baseline_days()}-day personal baseline ...",
    )
    line("[run]  ", "estimating night-before physiological stress ...")


def _print_exam_stress(ranked: list[ExamReadiness]) -> None:
    section_sub(
        "EXAM STRESS",
        f"night-before physiological stress vs {_baseline_days()}-day baseline",
    )
    console.print(
        f"[dim]{'stress':>6}  {'awake':>5}  {'sleep':>7}  {'rec':>4}  {'hrv':>5}  "
        f"{'':<10}  exam[/dim]"
    )

    def load_key(result: ExamReadiness) -> float:
        stress = stress_for_result(result)
        return -(stress.score if stress else -1)

    for result in sorted(ranked, key=load_key):
        console.print(_exam_stress_row(result))


def _exam_stress_row(result: ExamReadiness) -> str:
    upcoming = result.readiness_label == "UPCOMING"
    stress = stress_for_result(result)
    load = stress.score if stress else None
    load_col = value_color(load, good_high=False) if load is not None else "dim"
    load_num = "--" if load is None else str(load)
    bar = make_bar(load, max_value=100, width=10) if load is not None else " " * 10

    awake = result.awake_hours_before
    awake_cell = f"{awake:.0f}h" if awake is not None else "--"
    rec = result.recovery.recovery_score if result.recovery else None
    sleep_cell = "--" if upcoming else compact_duration(result.sleep_debt_minutes, signed=True)
    rec_cell = "--" if upcoming else format_percent(rec)
    hrv_cell = "--" if upcoming else format_percent_delta(result.hrv_delta_percent)

    return (
        f"[{load_col}]{load_num:>6}[/]  "
        f"[{awake_color(awake)}]{awake_cell:>5}[/]  "
        f"[{sleep_delta_color(result.sleep_debt_minutes)}]{sleep_cell:>7}[/]  "
        f"[{value_color(rec)}]{rec_cell:>4}[/]  "
        f"[{delta_color(result.hrv_delta_percent, good_high=True)}]{hrv_cell:>5}[/]  "
        f"[{load_col}]{bar}[/]  "
        f"{escape(truncate(result.exam.course, 34))}"
    )


def _print_readiness_chart(analyzed: list[ExamReadiness]) -> None:
    section_sub("READINESS", "0-100, diverging from the 50 midpoint")
    half = 18
    fill = "█" if UNICODE else "#"
    axis = "│" if UNICODE else "|"
    left = "low".ljust(half)
    right = "ready".rjust(half)
    console.print(f"[dim]{'exam':<26} {'score':>5}   {left}{axis}{right}[/dim]")
    for result in sorted(analyzed, key=lambda item: -(item.readiness_score or 0)):
        score = result.readiness_score or 0
        magnitude = max(0, min(int(round(abs(score - 50) / 50 * half)), half))
        if score >= 50:
            chart = " " * half + axis + f"[green]{fill * magnitude}[/green]"
        else:
            chart = " " * (half - magnitude) + f"[red]{fill * magnitude}[/red]" + axis
        console.print(
            f"{escape(truncate(result.exam.course, 26)):<26} "
            f"[{status_color(result.readiness_label)}]{score:>5.0f}[/]   {chart}"
        )


def _print_upcoming_brief(upcoming: list[ExamReadiness]) -> None:
    section_sub("UPCOMING", "analysis unlocks after the exam + night-before data")
    now = utc_now()
    for result in upcoming:
        console.print(
            Text.assemble(
                (truncate(result.exam.course, 34), "cyan"),
                (
                    f"   {format_compact_datetime(result.exam.exam_at)}"
                    f"  {DOT}  in {remaining_text(result.exam.exam_at, now)}",
                    "dim",
                ),
            )
        )


def _print_footer_note() -> None:
    console.print()
    console.print(
        Text.assemble(
            ("report --full", "cyan"),
            (f"  for per-exam detail, drivers & HR  {DOT}  ", "dim"),
            ("exampulse export", "cyan"),
            ("  writes CSVs", "dim"),
        )
    )


def print_compact_exam_detail(
    result: ExamReadiness,
    night_hr: NightHRSignal | None = None,
    activity_hr: ExamActivityHR | None = None,
    raw_hr: ExamRawHR | None = None,
    pre_exam_hr: PreExamHR | None = None,
) -> None:
    label = result.readiness_label.casefold()
    score = "--" if result.readiness_score is None else f"{result.readiness_score:.0f}"
    stress = stress_for_result(result)
    load_label = "n/a" if stress is None else stress.label
    load_score = "--" if stress is None else f"{stress.score}/100"
    load_color = stress_color(load_label)
    sleep_minutes = result.sleep.total_sleep_minutes if result.sleep else None
    recovery_score = result.recovery.recovery_score if result.recovery else None
    hrv = result.recovery.hrv_rmssd_milli if result.recovery else None
    rhr = result.recovery.resting_heart_rate if result.recovery else None
    strain = result.previous_cycle.strain if result.previous_cycle else None

    nights = result.baseline_nights
    min_nights = get_scoring_config().min_baseline_nights
    nights_note = f"n={nights}" if nights else "n<2"
    if nights and nights < min_nights:
        nights_note = f"n={nights} (thin; z withheld below n={min_nights})"
    console.print(
        f"[bold]{escape(result.exam.course)}[/bold] "
        f"[dim]{format_compact_datetime(result.exam.exam_at)}  {DOT}  baseline {nights_note}[/dim]"
    )

    table = Table(box=SIMPLE_BOX, pad_edge=False, header_style="dim", expand=False)
    table.add_column("metric", style="dim")
    table.add_column("night", justify="right")
    table.add_column(f"baseline ({_baseline_days()}d)", justify="right")
    table.add_column(DELTA, justify="right")
    table.add_column("trend")
    table.add_column("z", justify="right")

    table.add_row(
        "sleep",
        compact_duration(sleep_minutes),
        baseline_cell(
            compact_duration(result.baseline_sleep_minutes), result.baseline_sleep_std, "m"
        ),
        Text(
            compact_duration(result.sleep_debt_minutes, signed=True),
            style=sleep_delta_color(result.sleep_debt_minutes),
        ),
        f"[cyan]{sparkline(result.sleep_series)}[/cyan]",
        zscore_cell(result.sleep_z),
    )
    table.add_row(
        "recovery",
        Text(format_percent(recovery_score), style=value_color(recovery_score)),
        baseline_cell(format_percent(result.baseline_recovery_score), result.baseline_recovery_std),
        percentile_text(result.recovery_percentile),
        f"[cyan]{sparkline(result.recovery_series)}[/cyan]",
        zscore_cell(result.recovery_z),
    )
    table.add_row(
        "hrv",
        f"{format_float(hrv)} ms" if hrv is not None else "n/a",
        baseline_cell(format_float(result.baseline_hrv), result.baseline_hrv_std, "ms"),
        delta_text(result.hrv_delta_percent, "%", good_high=True),
        f"[cyan]{sparkline(result.hrv_series)}[/cyan]",
        zscore_cell(result.hrv_z),
    )
    table.add_row(
        "rhr",
        f"{rhr:.0f} bpm" if rhr is not None else "n/a",
        baseline_cell(format_float(result.baseline_rhr), result.baseline_rhr_std, ""),
        delta_text(result.rhr_delta_bpm, " bpm", good_high=False),
        f"[cyan]{sparkline(result.rhr_series)}[/cyan]",
        zscore_cell(result.rhr_z),
    )
    table.add_row("strain", format_float(strain), "[dim]prev cycle[/dim]", "", "", "")
    console.print(ascii_safe_table(table))

    _print_awake_line(result)
    _print_night_arousal_line(result)
    console.print(
        f"[dim]{'score':<10}[/dim] "
        f"[{status_color(result.readiness_label)}]ready {label} {score:<3} "
        f"{colored_bar(result.readiness_score, width=12)}[/]   "
        f"[{load_color}]{load_label} {load_score:<7}[/] "
        f"[{load_color}]{stress_bar(stress.score if stress else None, width=12)}[/]"
    )
    console.print(f"[dim]{'note':<10}[/dim] {escape(result.summary)}")
    _print_result_line(result)
    _print_pre_exam_hr_line(pre_exam_hr)
    _print_exam_raw_hr_line(raw_hr)
    _print_exam_hr_line(activity_hr)
    print_night_hr_signal(night_hr)


def _awake_verdict(hours: float | None) -> tuple[str, str]:
    dash = "—" if UNICODE else "-"
    if hours is None:
        return "unknown", "dim"
    if hours >= 24:
        return f"{hours:.0f}h awake {dash} 24h+ no sleep", "red"
    if hours >= 18:
        return f"{hours:.0f}h awake {dash} all-nighter (no night sleep)", "red"
    if hours >= 13:
        return f"{hours:.0f}h awake {dash} long awake stretch", "yellow"
    return f"{hours:.0f}h awake {dash} rested", "green"


def _print_awake_line(result: ExamReadiness) -> None:
    if result.awake_hours_before is None:
        return
    verdict, style = _awake_verdict(result.awake_hours_before)
    note = ""
    if result.awake_hours_before >= 18:
        note = (
            "   [dim](no sleep the night before; the 'sleep' above is your last "
            "sleep, hours earlier)[/dim]"
        )
    console.print(f"[dim]{'awake':<10}[/dim] [{style}]{verdict}[/{style}]{note}")


def _print_result_line(result: ExamReadiness) -> None:
    exam = result.exam
    if exam.grade is None and not exam.letter_grade:
        return
    grade_text = f"{exam.grade:g}" if exam.grade is not None else "n/a"
    letter_text = f" ({exam.letter_grade})" if exam.letter_grade else ""
    console.print(f"[dim]{'result':<10}[/dim] [bold]{grade_text}{letter_text}[/bold]")


def _night_arousal(result: ExamReadiness) -> tuple[str, str]:
    """Combine night-before RHR + HRV into one arousal verdict.

    Elevated resting HR plus suppressed HRV the night before is the classic
    physiological signature of pre-exam stress / poor recovery.
    """
    hrv = result.hrv_delta_percent
    rhr = result.rhr_delta_bpm
    if hrv is None and rhr is None:
        return "no HRV/RHR data", "dim"
    high = (hrv is not None and hrv <= -15) or (rhr is not None and rhr >= 5)
    mild = (hrv is not None and hrv <= -8) or (rhr is not None and rhr >= 3)
    if high:
        return "elevated arousal (stress signal)", "red"
    if mild:
        return "mild arousal", "yellow"
    return "calm", "green"


def _print_night_arousal_line(result: ExamReadiness) -> None:
    verdict, style = _night_arousal(result)
    rhr_text = format_bpm_delta(result.rhr_delta_bpm)
    hrv_text = format_percent_delta(result.hrv_delta_percent)
    arrow = "→" if UNICODE else "->"
    console.print(
        f"[dim]{'night':<10}[/dim] "
        f"RHR {rhr_text} {DOT} HRV {hrv_text}  {arrow}  [{style}]{verdict}[/{style}]"
    )


def _print_pre_exam_hr_line(pre_exam_hr: PreExamHR | None) -> None:
    if pre_exam_hr is None:
        return
    if pre_exam_hr.status != "ok":
        console.print(
            f"[dim]{'pre-exam':<10}[/dim] "
            f"[dim]no per-minute HR in the {pre_exam_hr.hours_before:.0f}h before exam[/dim]"
        )
        return
    console.print(
        f"[dim]{'pre-exam':<10}[/dim] "
        f"[cyan]{pre_exam_hr.avg_hr:.0f} avg[/cyan] "
        f"[dim]({pre_exam_hr.min_hr}-{pre_exam_hr.max_hr} bpm, "
        f"{pre_exam_hr.hours_before:.0f}h before, {pre_exam_hr.points} pts)[/dim]"
    )
    console.print(
        f"[dim]{'':<10}[/dim] [cyan]{sparkline(downsample(pre_exam_hr.minute_series))}[/cyan]"
    )


def _print_exam_raw_hr_line(raw_hr: ExamRawHR | None) -> None:
    if raw_hr is None:
        return
    sep = DOT
    if raw_hr.status != "ok":
        console.print(
            f"[dim]{'hr/min':<10}[/dim] [dim]no per-minute HR in exam window[/dim]"
        )
        return
    dbpm_style = "red" if (raw_hr.dbpm or 0) >= 5 else (
        "yellow" if (raw_hr.dbpm or 0) > 0 else "green"
    )
    z_text = f"z {raw_hr.z:+.1f}" if raw_hr.z is not None else "z n/a"
    console.print(
        f"[dim]{'hr/min':<10}[/dim] "
        f"[cyan]{raw_hr.avg_exam:.0f} avg[/cyan]   "
        f"[{dbpm_style}]{raw_hr.dbpm:+.0f} vs baseline[/{dbpm_style}]   "
        f"[dim]{z_text} {sep} {raw_hr.elevated_percent:.0f}% elevated {sep} "
        f"{raw_hr.exam_points} pts[/dim]"
    )
    console.print(
        f"[dim]{'':<10}[/dim] [cyan]{sparkline(downsample(raw_hr.minute_series))}[/cyan]"
    )


def _print_exam_hr_line(activity_hr: ExamActivityHR | None) -> None:
    if activity_hr is None:
        return
    if activity_hr.status != "ok":
        console.print(
            f"[dim]{'exam hr':<10}[/dim] [dim]no WHOOP activity logged in window[/dim]"
        )
        return
    avg = f"{activity_hr.avg_hr:.0f}" if activity_hr.avg_hr is not None else "--"
    coverage = (
        f"{activity_hr.coverage_percent:.0f}%"
        if activity_hr.coverage_percent is not None
        else "--"
    )
    console.print(
        f"[dim]{'exam hr':<10}[/dim] "
        f"[cyan]{avg} avg[/cyan] / {activity_hr.max_hr} max bpm   "
        f"[dim]covers {coverage} of window[/dim]   "
        f"{zone_bar(activity_hr.zone_percent, width=12)}"
    )


def print_night_hr_signal(signal: NightHRSignal | None) -> None:
    section("NIGHT HR SIGNAL", width=40)
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
    source = (
        "imported per-minute HR"
        if signal.source == "imported_raw"
        else "official WHOOP sleep stream"
    )
    console.print(f"[dim]{'source':<10}[/dim] {source}")
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

    color = stress_color(stress.label)
    labels = {component.name: component.label for component in stress.components}
    recovery_score = result.recovery.recovery_score if result.recovery else None
    previous_strain = result.previous_cycle.strain if result.previous_cycle else None

    console.print(
        f"[dim]{'stress':<10}[/dim] "
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
        console.print("- no major stress drivers")
    console.print(
        f"\n[dim]{'note':<10}[/dim] This is a physiological stress estimate from "
        "sleep, recovery, HRV, RHR, and strain — a proxy, not a direct "
        "measurement of mental stress."
    )


def print_compact_stress_drivers(result: ExamReadiness) -> None:
    stress = stress_for_result(result)
    if stress is None:
        _print_compact_stress_monitor(result)
        return

    console.print(f"[bold]{escape(result.exam.course)}[/bold]")
    table = Table(box=SIMPLE_BOX, pad_edge=False, header_style="dim", expand=False)
    table.add_column("driver", style="dim")
    table.add_column("pts", justify="right")
    table.add_column("share")
    table.add_column("effect")
    table.add_column("note", style="dim")
    for component in stress.components:
        ratio_color = value_color(component.points, max_value=component.max_points, good_high=False)
        bar = make_bar(component.points, max_value=component.max_points, width=10)
        table.add_row(
            driver_display_name(component.name),
            Text(f"+{component.points}", style=ratio_color),
            f"[{ratio_color}]{bar}[/]",
            Text(component.label, style=ratio_color if component.points else "dim"),
            component.note,
        )
    console.print(ascii_safe_table(table))

    drivers = top_stress_drivers(stress.components)
    if drivers:
        driver = drivers[0]
        console.print(
            "[dim]top driver [/dim] "
            f"{driver_display_name(driver.name)} (+{driver.points})"
        )
        console.print(
            f"[dim]{'stress bar':<10}[/dim] "
            f"[{stress_color(stress.label)}]{stress_bar(stress.score, width=16)}[/]"
        )
    else:
        console.print("[dim]top driver [/dim] no major stress driver")
    console.print(
        f"[dim]{'note':<10}[/dim] Physiological Stress Index estimates body stress "
        "from sleep, recovery, HRV, RHR, and strain."
    )
    console.print(
        f"{'':<10} It is a proxy, not a direct measurement of mental stress."
    )


def print_compact_upcoming(
    exam: Exam,
    now: datetime,
    night_hr: NightHRSignal | None = None,
) -> None:
    console.print(f"[bold]{escape(exam.course)}[/bold]")
    console.print(f"[dim]{'time':<10}[/dim] {format_compact_datetime(exam.exam_at)}")
    console.print(f"[dim]{'remaining':<10}[/dim] {remaining_text(exam.exam_at, now)}")
    console.print(
        f"[dim]{'status':<10}[/dim] "
        "[cyan]analysis pending night-before WHOOP data[/cyan]"
    )
    print_night_hr_signal(night_hr or NightHRSignal(status="pending"))


def print_report(
    results: list[ExamReadiness], sync_run_message: str, demo_data: bool = False
) -> None:
    title = "EXAMPULSE - EXAM READINESS"
    if demo_data:
        title = f"{title}\nDEMO DATA"
    console.print(Panel.fit(title, box=box.ASCII, style="bold"))
    console.print(f"[dim]{sync_run_message}[/dim]")
    console.print("[dim]matching sleep/recovery/cycle data against exams...[/dim]")

    ranked = sorted(results, key=risk_order)
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
    console.print(ascii_safe_table(table))

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


def demo_result() -> ExamReadiness:
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
