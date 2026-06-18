from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.exam_service import ExamImportError, ExamService


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_import_exams_requires_timezone(tmp_path, session) -> None:
    path = tmp_path / "exams.json"
    path.write_text(
        json.dumps([{"course": "Math", "exam_at": "2026-06-16T10:00:00"}]),
        encoding="utf-8",
    )

    with pytest.raises(ExamImportError):
        ExamService(session).import_file(path)


def test_import_exams_upserts_by_course_and_time(tmp_path, session) -> None:
    path = tmp_path / "exams.json"
    payload = [{"course": "Math", "exam_at": "2026-06-16T10:00:00+03:00"}]
    path.write_text(json.dumps(payload), encoding="utf-8")

    service = ExamService(session)
    first = service.import_file(path)
    second = service.import_file(path)

    assert len(first) == 1
    assert len(second) == 1
    assert len(service.list()) == 1


def test_import_exams_can_replace_existing_exams(tmp_path, session) -> None:
    first_path = tmp_path / "first.json"
    first_path.write_text(
        json.dumps(
            [
                {"course": "Old Exam", "exam_at": "2026-06-15T10:00:00+03:00"},
                {"course": "Another Old Exam", "exam_at": "2026-06-16T10:00:00+03:00"},
            ]
        ),
        encoding="utf-8",
    )
    next_path = tmp_path / "next.json"
    next_path.write_text(
        json.dumps([{"course": "Real Exam", "exam_at": "2026-06-22T10:15:00+03:00"}]),
        encoding="utf-8",
    )

    service = ExamService(session)
    service.import_file(first_path)
    imported = service.import_file(next_path, replace=True)

    assert [exam.course for exam in imported] == ["Real Exam"]
    assert [exam.course for exam in service.list()] == ["Real Exam"]
