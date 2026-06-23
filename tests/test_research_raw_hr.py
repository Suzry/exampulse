from __future__ import annotations

import csv
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine
from typer.testing import CliRunner

from app.cli.main import app
from app.research.raw_hr.service import RawHRDataError
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
        summary = RawHRService(session).import_csv(path, source="whoop_export")
        points = list_research_raw_hr_points(session, source="whoop_export")

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
        service.import_csv(path, source="whoop_export")
        result = service.exam_window("Operating", source="whoop_export")

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
        service.import_csv(path, source="whoop_export")
        result = service.exam_window("Operating", source="whoop_export")

    assert result.dbpm == 16
    assert result.z_like is None


def test_exam_window_refuses_no_real_raw_hr_data() -> None:
    start = datetime.fromisoformat("2026-06-22T10:15:00+03:00")
    with _session() as session:
        upsert_exam(session, course="Operating Systems", exam_at=start, notes="")
        with pytest.raises(RawHRDataError):
            RawHRService(session).exam_window("Operating")


def test_audit_summarizes_sources_and_date_ranges(tmp_path) -> None:
    first = tmp_path / "first.csv"
    with first.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "hr"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-06-22T09:00:00+03:00", "hr": "72"})
        writer.writerow({"timestamp": "2026-06-22T09:01:00+03:00", "hr": "73"})
    second = tmp_path / "second.csv"
    with second.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "hr"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-06-23T09:00:00+03:00", "hr": "80"})

    with _session() as session:
        service = RawHRService(session)
        service.import_csv(first, source="whoop_export:band")
        service.import_csv(second, source="whoop_export:sleep")
        audit = service.audit()

    assert audit.total_points == 3
    assert len(audit.sources) == 2
    assert audit.first.isoformat() == "2026-06-22T09:00:00+03:00"
    assert audit.last.isoformat() == "2026-06-23T09:00:00+03:00"


def test_no_raw_hr_demo_command_exists() -> None:
    result = CliRunner().invoke(app, ["research", "raw-hr", "--help"])

    assert result.exit_code == 0
    assert "demo" not in result.stdout.casefold()


def test_raw_hr_help_does_not_suggest_apple_health() -> None:
    result = CliRunner().invoke(app, ["research", "raw-hr", "--help"])

    assert result.exit_code == 0
    assert "apple" not in result.stdout.casefold()
    assert "whoop" in result.stdout.casefold()
