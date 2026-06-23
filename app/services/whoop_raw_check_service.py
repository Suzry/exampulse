from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session

from app.integrations.whoop_client import WhoopAPIError, WhoopClient
from app.storage import repositories
from app.utils.time import utc_now

RAW_CHECK_SOURCE = "whoop_raw_check"
SLEEP_STREAM_FORBIDDEN = "sleep_stream_forbidden_403"


@dataclass(frozen=True, slots=True)
class WhoopRawAccessCheck:
    summary_status: str
    sleep_stream_status: str
    sleep_stream_status_code: int | None
    sleep_stream_message: str

    @property
    def sleep_stream_forbidden(self) -> bool:
        return self.sleep_stream_status == "forbidden" and self.sleep_stream_status_code == 403


class WhoopRawCheckService:
    def __init__(self, session: Session, client: WhoopClient | None = None):
        self.session = session
        self.client = client or WhoopClient(session)

    def check(self, days: int = 30) -> WhoopRawAccessCheck:
        end = utc_now()
        start = end - timedelta(days=days)
        summary_status = "available"
        sleep_stream_status = "no scored sleep"
        sleep_stream_status_code = None
        sleep_stream_message = ""

        try:
            sleeps = self.client.get_sleep_collection(start, end)
        except WhoopAPIError as exc:
            summary_status = f"failed {exc.status_code or 'unknown'}"
            sleeps = []

        scored_sleep = next(
            (sleep for sleep in sleeps if sleep.get("score_state") == "SCORED" and sleep.get("id")),
            None,
        )
        if scored_sleep is not None:
            try:
                self.client.get_sleep_stream(str(scored_sleep["id"]), stream_type="hr")
                sleep_stream_status = "available"
            except WhoopAPIError as exc:
                sleep_stream_status_code = exc.status_code
                if exc.status_code == 403:
                    sleep_stream_status = "forbidden"
                    sleep_stream_message = _safe_stream_message(exc)
                else:
                    sleep_stream_status = "failed"
                    sleep_stream_message = _safe_stream_message(exc)

        message = SLEEP_STREAM_FORBIDDEN if sleep_stream_status == "forbidden" else ""
        repositories.save_sync_run(
            self.session,
            source=RAW_CHECK_SOURCE,
            days=days,
            sleeps_saved=0,
            recoveries_saved=0,
            cycles_saved=0,
            skipped_records=0,
            started_at=end,
            message=message,
        )
        return WhoopRawAccessCheck(
            summary_status=summary_status,
            sleep_stream_status=sleep_stream_status,
            sleep_stream_status_code=sleep_stream_status_code,
            sleep_stream_message=sleep_stream_message,
        )


def _safe_stream_message(exc: WhoopAPIError) -> str:
    payload = exc.response_json or {}
    for key in ("error", "message", "description", "error_description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:120]
    return " ".join((exc.response_text or "").split())[:120]
