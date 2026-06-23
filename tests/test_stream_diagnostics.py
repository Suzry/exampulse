from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console
from sqlmodel import Session, SQLModel, create_engine

import app.cli.main as cli_main
from app.integrations.whoop_client import WhoopAPIError
from app.services.sync_service import SyncService, redact_sleep_id


class FailingStreamClient:
    def __init__(self, sleep_count: int = 3):
        self.sleep_count = sleep_count

    def get_sleep_collection(self, start, end):
        return [
            {
                "id": f"sleep-secret-token-{index:04d}",
                "cycle_id": index,
                "start": (datetime(2026, 6, 1, tzinfo=UTC) + timedelta(days=index)).isoformat(),
                "end": (datetime(2026, 6, 1, 8, tzinfo=UTC) + timedelta(days=index)).isoformat(),
                "score_state": "SCORED",
                "score": {"stage_summary": {}},
            }
            for index in range(self.sleep_count)
        ]

    def get_recovery_collection(self, start, end):
        return []

    def get_cycle_collection(self, start, end):
        return []

    def get_sleep_stream(self, sleep_id: str, stream_type: str = "hr"):
        raise WhoopAPIError(
            "WHOOP API request failed: 403",
            status_code=403,
            path=f"/v2/activity/sleep/{sleep_id}/stream",
            response_json={
                "error": "deactivated_user",
                "message": "Authorization header Bearer access_token_secret should not leak",
                "access_token": "access_token_secret",
                "refresh_token": "refresh_token_secret",
                "client_secret": "client_secret_value",
            },
            response_text="fallback text with refresh_token_secret",
        )


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_403_json_body_is_summarized_safely() -> None:
    with _session() as session:
        summary = SyncService(session, client=FailingStreamClient()).sync(
            days=30,
            streams=True,
        )

    assert summary.sleep_stream_errors == 3
    assert len(summary.sleep_stream_error_samples or []) == 2
    sample = summary.sleep_stream_error_samples[0]
    assert sample.status_code == 403
    assert sample.error == "deactivated_user"
    assert sample.redacted_sleep_id == redact_sleep_id("sleep-secret-token-0000")


def test_tokens_and_authorization_headers_are_never_printed(monkeypatch) -> None:
    with _session() as session:
        summary = SyncService(session, client=FailingStreamClient()).sync(
            days=30,
            streams=True,
        )
    test_console = Console(record=True, width=120, color_system=None)
    monkeypatch.setattr(cli_main, "console", test_console)

    cli_main._print_stream_error_samples(summary, debug=True)
    output = test_console.export_text()

    assert "deactivated_user" in output
    assert "sleep-...0000" in output
    assert "sleep-secret-token-0000" not in output
    assert "access_token_secret" not in output
    assert "refresh_token_secret" not in output
    assert "client_secret_value" not in output
    assert "Authorization header" not in output


def test_debug_streams_increases_error_sample_count() -> None:
    with _session() as session:
        normal = SyncService(session, client=FailingStreamClient(sleep_count=6)).sync(
            days=30,
            streams=True,
            stream_error_sample_limit=2,
        )
    with _session() as session:
        debug = SyncService(session, client=FailingStreamClient(sleep_count=6)).sync(
            days=30,
            streams=True,
            stream_error_sample_limit=5,
        )

    assert len(normal.sleep_stream_error_samples or []) == 2
    assert len(debug.sleep_stream_error_samples or []) == 5
