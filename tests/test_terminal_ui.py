from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console

import app.cli.main as cli_main
from app.core.analysis import ExamReadiness
from app.core.models import Exam, WhoopCycle, WhoopRecovery
from app.utils.terminal_ui import (
    colored_bar,
    extract_room_from_notes,
    format_zscore,
    make_bar,
    sparkline,
    value_color,
    zscore_color,
)


def _upcoming_result() -> ExamReadiness:
    return ExamReadiness(
        exam=Exam(
            course="Operating Systems",
            exam_at=datetime.now(UTC) + timedelta(days=2),
            notes=(
                "Code: CS2016; Type: Theoretical; Section: 4; "
                "Period: Second; Room: 12-0.003; End: 12:15"
            ),
        ),
        sleep=None,
        recovery=None,
        previous_cycle=None,
        baseline_sleep_minutes=None,
        baseline_recovery_score=None,
        baseline_hrv=None,
        baseline_rhr=None,
        sleep_debt_minutes=None,
        recovery_delta=None,
        hrv_delta_percent=None,
        rhr_delta_bpm=None,
        readiness_score=None,
        readiness_label="UPCOMING",
        flags=[],
        summary="Analysis will be available after WHOOP data exists.",
    )


def _analyzed_result() -> ExamReadiness:
    exam_at = datetime(2026, 6, 14, 10, tzinfo=UTC)
    return ExamReadiness(
        exam=Exam(
            course="Differential equations and Linear Algebra",
            exam_at=exam_at,
        ),
        sleep=None,
        recovery=WhoopRecovery(
            sleep_id="sleep-1",
            cycle_id=1,
            score_state="SCORED",
            recovery_score=70,
        ),
        previous_cycle=WhoopCycle(
            id=1,
            start=exam_at - timedelta(days=1),
            end=exam_at - timedelta(hours=8),
            score_state="SCORED",
            strain=7.3,
        ),
        baseline_sleep_minutes=452,
        baseline_recovery_score=70,
        baseline_hrv=40,
        baseline_rhr=60,
        sleep_debt_minutes=-60,
        recovery_delta=0,
        hrv_delta_percent=0,
        rhr_delta_bpm=2,
        readiness_score=66,
        readiness_label="MODERATE",
        flags=[],
        summary="No major physiological load indicators stood out.",
    )


def test_make_bar_clamps_and_renders_width() -> None:
    assert make_bar(50, width=10, unicode=True) == "█████░░░░░"
    assert make_bar(150, width=10, unicode=True) == "██████████"
    assert make_bar(None, width=5, unicode=True) == "░░░░░"
    assert make_bar(50, width=10, unicode=False) == "#####-----"


def test_sparkline_normalizes_and_drops_none() -> None:
    line = sparkline([0, 50, 100], unicode=True)
    assert line[0] == "▁"
    assert line[-1] == "█"
    assert len(line) == 3
    # None values are skipped, not plotted.
    assert len(sparkline([1, None, 9], unicode=True)) == 2
    # Empty or all-None series render nothing.
    assert sparkline([], unicode=True) == ""
    assert sparkline([None, None], unicode=True) == ""


def test_sparkline_flat_series_is_stable() -> None:
    line = sparkline([5, 5, 5], unicode=True)
    assert len(line) == 3
    assert len(set(line)) == 1


def test_sparkline_ascii_fallback() -> None:
    line = sparkline([0, 100], unicode=False)
    assert all(char in "_.-=+*#" for char in line)


def test_value_color_traffic_light() -> None:
    assert value_color(90) == "green"
    assert value_color(55) == "yellow"
    assert value_color(10) == "red"
    assert value_color(None) == "dim"
    # good_high=False flips the scale (e.g. stress load).
    assert value_color(90, good_high=False) == "red"
    assert value_color(10, good_high=False) == "green"


def test_colored_bar_wraps_markup() -> None:
    bar = colored_bar(90, width=4, good_high=True)
    assert bar.startswith("[green]")
    assert bar.endswith("[/green]")


def test_format_and_color_zscore() -> None:
    assert format_zscore(None) == "n/a"
    assert format_zscore(-2.05).startswith("-2.0")
    assert format_zscore(1.5).startswith("+1.5")
    assert zscore_color(0.5) == "green"
    assert zscore_color(1.7) == "yellow"
    assert zscore_color(-3.0) == "red"
    assert zscore_color(None) == "dim"


def test_extract_room_from_notes() -> None:
    assert extract_room_from_notes("Code: CS2016; Room: 12-0.003; End: 12:15") == "12-0.003"
    assert extract_room_from_notes("room: 14-1.014") == "14-1.014"
    assert extract_room_from_notes("Code: CS2016") is None


def test_compact_report_shows_upcoming_without_fake_detail_fields(monkeypatch) -> None:
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report([_upcoming_result()], sync_run=None, full=True)
    output = test_console.export_text()

    assert "upcoming" in output.casefold()
    assert "analysis pending night-before WHOOP data" in output
    assert "12-0.003" not in output
    assert "pending night-before sleep stream data" in output
    assert "recovery" not in output.casefold()
    assert "hrv" not in output.casefold()
    assert "rhr" not in output.casefold()
    assert "strain" not in output.casefold()


def test_brief_report_is_a_single_table_without_detail_sections(monkeypatch) -> None:
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report([_analyzed_result()], sync_run=None)
    output = test_console.export_text()

    # The concise default shows the brief stress table...
    assert "EXAM STRESS" in output
    assert "Differential" in output
    # ...but not the verbose per-exam sections.
    assert "EXAM DETAIL" not in output
    assert "STRESS DRIVERS" not in output
    assert "NIGHT HR SIGNAL" not in output
    assert "--full" in output


def test_full_report_includes_detail_sections(monkeypatch) -> None:
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report([_analyzed_result()], sync_run=None, full=True)
    output = test_console.export_text()

    assert "EXAM DETAIL" in output
    assert "STRESS DRIVERS" in output


def test_upcoming_exams_do_not_fake_sleep_hr_values(monkeypatch) -> None:
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report([_upcoming_result()], sync_run=None, full=True)
    output = test_console.export_text()

    assert "pending night-before sleep stream data" in output
    assert "avg hr" not in output
    assert "max hr" not in output


def test_compact_report_keeps_upcoming_out_of_stress_drivers(monkeypatch) -> None:
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report(
        [_analyzed_result(), _upcoming_result()], sync_run=None, full=True
    )
    output = test_console.export_text()
    stress_section = output.split("STRESS DRIVERS", 1)[1].split("UPCOMING", 1)[0]

    assert "Operating Systems" not in stress_section
    assert "sleep debt" in stress_section
    assert "sleep_debt" not in stress_section
    assert "top driver  sleep debt (+10)" in stress_section
    assert "UPCOMING" in output
