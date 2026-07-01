from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.core.models import Exam
from app.storage.repositories import delete_all_exams, list_exams, upsert_exam
from app.utils.time import require_timezone


class ExamImportError(ValueError):
    pass


class ExamService:
    def __init__(self, session: Session):
        self.session = session

    def import_file(self, path: str | Path, *, replace: bool = False) -> list[Exam]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ExamImportError("exams.json must contain a list of exams.")

        if replace:
            delete_all_exams(self.session)

        imported: list[Exam] = []
        for index, item in enumerate(data, start=1):
            imported.append(self._import_item(item, index))
        return imported

    def _import_item(self, item: dict[str, Any], index: int) -> Exam:
        if not isinstance(item, dict):
            raise ExamImportError(f"Exam #{index} must be an object.")
        course = str(item.get("course") or "").strip()
        if not course:
            raise ExamImportError(f"Exam #{index} is missing course.")
        exam_at_raw = item.get("exam_at")
        if not exam_at_raw:
            raise ExamImportError(f"Exam #{index} is missing exam_at.")
        try:
            exam_at = require_timezone(str(exam_at_raw), "exam_at")
        except ValueError as exc:
            raise ExamImportError(f"Exam #{index}: {exc}") from exc

        grade = item.get("grade")
        if grade is not None:
            grade = float(grade)
        letter_grade = item.get("letter_grade")
        if letter_grade is not None:
            letter_grade = str(letter_grade).strip() or None
        notes = str(item.get("notes") or "")
        return upsert_exam(
            self.session,
            course=course,
            exam_at=exam_at,
            grade=grade,
            letter_grade=letter_grade,
            notes=notes,
        )

    def list(self) -> list[Exam]:
        return list_exams(self.session)
