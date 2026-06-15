from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.services.demo_seed_service import DemoSeedService
from app.services.insight_service import InsightService
from app.storage.repositories import (
    has_demo_data,
    latest_sync_run,
    list_cycles,
    list_exams,
    list_recoveries,
    list_sleeps,
)


def test_demo_seed_creates_offline_dataset() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        summary = DemoSeedService(session).seed(days=30, seed=7)
        results = InsightService(session).generate()
        sync_run = latest_sync_run(session)

        assert summary.sleeps_saved == 30
        assert summary.recoveries_saved == 30
        assert summary.cycles_saved == 30
        assert summary.exams_saved == 3
        assert len(list_sleeps(session)) == 30
        assert len(list_recoveries(session)) == 30
        assert len(list_cycles(session)) == 30
        assert len(list_exams(session)) == 3
        assert results
        assert has_demo_data(session)
        assert sync_run is not None
        assert sync_run.source == "demo"
