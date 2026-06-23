from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from sqlmodel import Session, SQLModel, create_engine

import app.cli.main as cli_main
from app.core.analysis import ExamReadiness
from app.core.models import Exam
from app.integrations.whoop_client import WhoopAPIError
from app.services.whoop_raw_check_service import WhoopRawCheckService


class ForbiddenStreamClient:
    def get_sleep_collection(self, start, end):
        return [
            {
                "id": "sleep-secret-full-id-1234567890",
                "score_state": "SCORED",
            }
        ]

    def get_sleep_stream(self, sleep_id: str, stream_type: str = "hr"):
        raise WhoopAPIError(
            "WHOOP API request failed: 403",
            status_code=403,
            path=f"/v2/activity/sleep/{sleep_id}/stream",
            response_json={
                "error": "forbidden",
                "message": "Authorization Bearer access_token_secret",
                "client_secret": "client_secret_value",
            },
            response_text="refresh_token_secret",
        )


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _upcoming_result() -> ExamReadiness:
    return ExamReadiness(
        exam=Exam(course="Operating Systems", exam_at=datetime(2026, 6, 22, 10, tzinfo=UTC)),
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
        summary="pending",
    )


def test_raw_check_redacts_ids_and_does_not_print_secrets(monkeypatch) -> None:
    with _session() as session:
        result = WhoopRawCheckService(session, client=ForbiddenStreamClient()).check()

    test_console = Console(record=True, width=120, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)
    cli_main._print_whoop_raw_check(result)
    output = test_console.export_text()

    assert "sleep-secret-full-id" not in output
    assert "access_token_secret" not in output
    assert "refresh_token_secret" not in output
    assert "client_secret_value" not in output
    assert "forbidden 403" in output
    assert "WHOOP band only" in output


def test_report_shows_forbidden_sleep_stream_status(monkeypatch) -> None:
    test_console = Console(record=True, width=120, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_compact_report(
        [_upcoming_result()],
        sync_run=None,
        sleep_stream_forbidden=True,
    )
    output = test_console.export_text()

    assert "WHOOP sleep stream forbidden by API" in output
    assert "WHOOP band only" in output
