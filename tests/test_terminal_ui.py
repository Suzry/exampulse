from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console

import app.cli.main as cli_main
from app.core.analysis import ExamReadiness
from app.core.models import Exam
from app.utils.terminal_ui import extract_room_from_notes, make_bar


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


def test_make_bar_clamps_and_renders_width() -> None:
    assert make_bar(50, width=10, unicode=True) == "█████░░░░░"
    assert make_bar(150, width=10, unicode=True) == "██████████"
    assert make_bar(None, width=5, unicode=True) == "░░░░░"
    assert make_bar(50, width=10, unicode=False) == "#####-----"


def test_extract_room_from_notes() -> None:
    assert extract_room_from_notes("Code: CS2016; Room: 12-0.003; End: 12:15") == "12-0.003"
    assert extract_room_from_notes("room: 14-1.014") == "14-1.014"
    assert extract_room_from_notes("Code: CS2016") is None


def test_compact_report_shows_upcoming_without_fake_detail_fields(monkeypatch) -> None:
    test_console = Console(record=True, width=100, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report([_upcoming_result()], sync_run=None)
    output = test_console.export_text()

    assert "upcoming" in output.casefold()
    assert "analysis pending night-before WHOOP data" in output
    assert "12-0.003" not in output
    assert "sleep" not in output.casefold()
    assert "recovery" not in output.casefold()
    assert "hrv" not in output.casefold()
    assert "rhr" not in output.casefold()
    assert "strain" not in output.casefold()
