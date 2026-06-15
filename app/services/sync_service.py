from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session

from app.integrations.whoop_client import WhoopClient
from app.storage import repositories
from app.utils.time import utc_now


@dataclass(slots=True)
class SyncSummary:
    days: int
    sleeps_saved: int
    recoveries_saved: int
    cycles_saved: int
    skipped_records: int


class SyncService:
    def __init__(self, session: Session, client: WhoopClient | None = None):
        self.session = session
        self.client = client or WhoopClient(session)

    def sync(self, days: int = 30) -> SyncSummary:
        started_at = utc_now()
        end = started_at
        start = end - timedelta(days=days)

        skipped = 0
        sleeps_saved = 0
        recoveries_saved = 0
        cycles_saved = 0

        for payload in self.client.get_sleep_collection(start, end):
            if payload.get("score_state") != "SCORED":
                skipped += 1
                continue
            repositories.upsert_sleep(self.session, payload)
            sleeps_saved += 1

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
        )
