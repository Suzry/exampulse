from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session

from app.integrations.whoop_client import WhoopAPIError, WhoopClient
from app.storage import repositories
from app.utils.time import utc_now

SLEEP_STREAM_FORBIDDEN = "sleep_stream_forbidden_403"


@dataclass(slots=True)
class StreamFetchError:
    sleep_id: str
    status_code: int | None
    error: str
    message: str
    response_text: str
    path: str

    @property
    def redacted_sleep_id(self) -> str:
        return redact_sleep_id(self.sleep_id)


@dataclass(slots=True)
class SyncSummary:
    days: int
    sleeps_saved: int
    recoveries_saved: int
    cycles_saved: int
    skipped_records: int
    sleep_stream_points_saved: int = 0
    sleep_stream_sleeps_synced: int = 0
    sleep_stream_errors: int = 0
    sleep_stream_error_samples: list[StreamFetchError] | None = None


class SyncService:
    def __init__(self, session: Session, client: WhoopClient | None = None):
        self.session = session
        self.client = client or WhoopClient(session)

    def sync(
        self,
        days: int = 30,
        *,
        streams: bool = False,
        stream_error_sample_limit: int = 2,
    ) -> SyncSummary:
        started_at = utc_now()
        end = started_at
        start = end - timedelta(days=days)

        skipped = 0
        sleeps_saved = 0
        recoveries_saved = 0
        cycles_saved = 0
        sleep_stream_points_saved = 0
        sleep_stream_sleeps_synced = 0
        sleep_stream_errors = 0
        sleep_stream_error_samples: list[StreamFetchError] = []
        scored_sleep_ids: list[str] = []

        for payload in self.client.get_sleep_collection(start, end):
            if payload.get("score_state") != "SCORED":
                skipped += 1
                continue
            repositories.upsert_sleep(self.session, payload)
            sleeps_saved += 1
            scored_sleep_ids.append(str(payload["id"]))

        for payload in self.client.get_recovery_collection(start, end):
            if payload.get("score_state") != "SCORED":
                skipped += 1
                continue
            repositories.upsert_recovery(self.session, payload)
            recoveries_saved += 1

        for payload in self.client.get_cycle_collection(start, end):
            if payload.get("score_state") != "SCORED":
                skipped += 1
                continue
            repositories.upsert_cycle(self.session, payload)
            cycles_saved += 1

        if streams:
            for sleep_id in scored_sleep_ids:
                try:
                    stream_payload = self.client.get_sleep_stream(
                        sleep_id, stream_type="hr"
                    )
                except WhoopAPIError as exc:
                    sleep_stream_errors += 1
                    if len(sleep_stream_error_samples) < stream_error_sample_limit:
                        sleep_stream_error_samples.append(
                            _stream_fetch_error(sleep_id, exc)
                        )
                    continue
                points = _extract_hr_stream_points(stream_payload)
                if not points:
                    continue
                sleep_stream_points_saved += repositories.upsert_sleep_stream_points(
                    self.session,
                    sleep_id=sleep_id,
                    points=points,
                )
                sleep_stream_sleeps_synced += 1

        stream_forbidden = (
            streams
            and sleep_stream_errors > 0
            and sleep_stream_points_saved == 0
            and any(error.status_code == 403 for error in sleep_stream_error_samples)
        )

        repositories.save_sync_run(
            self.session,
            days=days,
            sleeps_saved=sleeps_saved,
            recoveries_saved=recoveries_saved,
            cycles_saved=cycles_saved,
            skipped_records=skipped,
            started_at=started_at,
            message=SLEEP_STREAM_FORBIDDEN if stream_forbidden else "",
        )
        return SyncSummary(
            days=days,
            sleeps_saved=sleeps_saved,
            recoveries_saved=recoveries_saved,
            cycles_saved=cycles_saved,
            skipped_records=skipped,
            sleep_stream_points_saved=sleep_stream_points_saved,
            sleep_stream_sleeps_synced=sleep_stream_sleeps_synced,
            sleep_stream_errors=sleep_stream_errors,
            sleep_stream_error_samples=sleep_stream_error_samples,
        )


def redact_sleep_id(sleep_id: str) -> str:
    value = str(sleep_id)
    if len(value) <= 10:
        return f"{value[:2]}...{value[-2:]}" if len(value) > 4 else "..."
    return f"{value[:6]}...{value[-4:]}"


def _stream_fetch_error(sleep_id: str, exc: WhoopAPIError) -> StreamFetchError:
    payload = exc.response_json or {}
    error = _first_string(payload, ("error", "code", "error_code", "type"))
    message = _first_string(payload, ("message", "description", "error_description"))
    response_text = ""
    if not error and not message:
        response_text = _safe_short_text(exc.response_text)
    return StreamFetchError(
        sleep_id=sleep_id,
        status_code=exc.status_code,
        error=error,
        message=message,
        response_text=response_text,
        path=exc.path or "",
    )


def _first_string(payload: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_short_text(value)
    return ""


def _safe_short_text(value: str) -> str:
    return " ".join(str(value or "").split())[:200]


def _extract_hr_stream_points(payload) -> list[dict]:
    if isinstance(payload, list):
        raw_points = payload
    elif isinstance(payload, dict):
        raw_points = (
            payload.get("hr")
            or payload.get("stream")
            or payload.get("records")
            or payload.get("data")
            or payload.get("points")
            or []
        )
    else:
        raw_points = []

    points: list[dict] = []
    for item in raw_points:
        timestamp = None
        hr = None
        is_sleeping = True
        if isinstance(item, dict):
            timestamp = item.get("timestamp") or item.get("time") or item.get("datetime")
            hr = item.get("hr") or item.get("heart_rate") or item.get("value")
            is_sleeping = item.get("is_sleeping", item.get("isSleeping", True))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            timestamp, hr = item[0], item[1]
            if len(item) >= 3:
                is_sleeping = item[2]
        if timestamp is None or hr is None:
            continue
        points.append(
            {
                "timestamp": timestamp,
                "hr": int(round(float(hr))),
                "is_sleeping": bool(is_sleeping),
            }
        )
    return points
