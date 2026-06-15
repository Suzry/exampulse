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
