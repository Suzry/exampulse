from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session

from app.integrations.whoop_client import WhoopAPIError, WhoopClient
from app.storage import repositories
from app.utils.time import utc_now


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


class SyncService:
    def __init__(self, session: Session, client: WhoopClient | None = None):
        self.session = session
        self.client = client or WhoopClient(session)

    def sync(self, days: int = 30, *, streams: bool = False) -> SyncSummary:
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
                except WhoopAPIError:
                    sleep_stream_errors += 1
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

        repositories.save_sync_run(
            self.session,
            days=days,
            sleeps_saved=sleeps_saved,
            recoveries_saved=recoveries_saved,
            cycles_saved=cycles_saved,
            skipped_records=skipped,
            started_at=started_at,
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
        )


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
