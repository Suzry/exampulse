from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.exam_service import ExamImportError, ExamService
from app.services.ics_parser import ICSParseError, parse_ics_events

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//University//Exams//EN
BEGIN:VEVENT
SUMMARY:Operating Systems Final
DTSTART;TZID=Asia/Riyadh:20260622T101500
LOCATION:Building 12\\, Room 0.003
DESCRIPTION:Bring your ID\\nClosed book
END:VEVENT
BEGIN:VEVENT
SUMMARY:Linear Algebra
  Final Exam
DTSTART:20260624T070000Z
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_events_reads_tzid_and_utc() -> None:
    events = parse_ics_events(SAMPLE_ICS)
    assert len(events) == 2

    first = events[0]
    assert first["course"] == "Operating Systems Final"
    exam_at = first["exam_at"]
    assert isinstance(exam_at, datetime)
    assert exam_at.utcoffset() == timedelta(hours=3)
    assert exam_at.hour == 10 and exam_at.minute == 15
    assert "Building 12, Room 0.003" in str(first["notes"])
    assert "Bring your ID" in str(first["notes"])

    second = events[1]
    # Folded SUMMARY lines are joined.
    assert "Final Exam" in str(second["course"])
    assert second["exam_at"] == datetime(2026, 6, 24, 7, 0, tzinfo=UTC)


def test_parse_ics_rejects_naive_dtstart() -> None:
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:X\n"
        "DTSTART:20260624T070000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    with pytest.raises(ICSParseError, match="timezone"):
        parse_ics_events(text)


def test_parse_ics_rejects_all_day_events() -> None:
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:X\n"
        "DTSTART;VALUE=DATE:20260624\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    with pytest.raises(ICSParseError, match="all-day"):
        parse_ics_events(text)


def test_parse_ics_rejects_missing_summary() -> None:
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
        "DTSTART:20260624T070000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    with pytest.raises(ICSParseError, match="SUMMARY"):
        parse_ics_events(text)


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_exam_service_imports_ics_file(tmp_path: Path) -> None:
    path = tmp_path / "exams.ics"
    path.write_text(SAMPLE_ICS, encoding="utf-8")
    with _session(tmp_path) as session:
        imported = ExamService(session).import_file(path)
        assert len(imported) == 2
        assert imported[0].course == "Operating Systems Final"
        # Importing again is idempotent (upsert by course + time).
        again = ExamService(session).import_file(path)
        assert len(again) == 2
        assert len(ExamService(session).list()) == 2


def test_exam_service_wraps_ics_errors(tmp_path: Path) -> None:
    path = tmp_path / "bad.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:X\n"
        "DTSTART:20260624T070000\nEND:VEVENT\nEND:VCALENDAR\n",
        encoding="utf-8",
    )
    with _session(tmp_path) as session, pytest.raises(ExamImportError):
        ExamService(session).import_file(path)


def test_riyadh_timezone_offset_is_correct() -> None:
    # Sanity-check the tz database resolves Asia/Riyadh to +03:00.
    events = parse_ics_events(SAMPLE_ICS)
    exam_at = events[0]["exam_at"]
    assert isinstance(exam_at, datetime)
    assert exam_at.astimezone(UTC).hour == 7
