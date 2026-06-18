from __future__ import annotations

import csv
from datetime import datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine

from app.research.raw_hr.service import RawHRService
from app.storage.repositories import list_research_raw_hr_points, upsert_exam


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_research_raw_hr_csv_import(tmp_path) -> None:
    path = tmp_path / "hr.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "hr"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-06-22T09:00:00+03:00", "hr": "72"})

    with _session() as session:
        summary = RawHRService(session).import_csv(path, source="apple_health")
        points = list_research_raw_hr_points(session, source="apple_health")

    assert summary.rows_imported == 1
    assert len(points) == 1
    assert points[0].hr == 72


def test_exam_window_hr_baseline_comparison(tmp_path) -> None:
    start = datetime.fromisoformat("2026-06-22T10:15:00+03:00")
    path = tmp_path / "hr.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "hr"])
        writer.writeheader()
        for index in range(6):
            writer.writerow(
                {
                    "timestamp": (start - timedelta(minutes=60 - index)).isoformat(),
                    "hr": "70",
                }
            )
        for index in range(6):
            writer.writerow(
                {
                    "timestamp": (start + timedelta(minutes=index)).isoformat(),
                    "hr": "86",
                }
            )

    with _session() as session:
        upsert_exam(
            session,
            course="Operating Systems",
            exam_at=start,
            notes="End: 12:15",
        )
        service = RawHRService(session)
        service.import_csv(path, source="apple_health")
        result = service.exam_window("Operating", source="apple_health")

    assert result.points == 6
    assert result.avg_hr_baseline == 70
    assert result.avg_hr_exam == 86
    assert result.dbpm == 16
    assert result.elevated_percent == 100


def test_exam_window_z_like_handles_zero_stddev_safely(tmp_path) -> None:
    start = datetime.fromisoformat("2026-06-22T10:15:00+03:00")
    path = tmp_path / "hr.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "hr"])
        writer.writeheader()
        for index in range(3):
            writer.writerow(
                {
                    "timestamp": (start - timedelta(minutes=30 - index)).isoformat(),
                    "hr": "74",
                }
            )
        writer.writerow({"timestamp": start.isoformat(), "hr": "90"})

    with _session() as session:
        upsert_exam(session, course="Operating Systems", exam_at=start, notes="")
        service = RawHRService(session)
        service.import_csv(path, source="apple_health")
        result = service.exam_window("Operating", source="apple_health")

    assert result.dbpm == 16
    assert result.z_like is None
