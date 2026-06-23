from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev

from sqlmodel import Session

from app.core.models import Exam, ResearchRawHRPoint
from app.storage import repositories
from app.utils.time import parse_datetime, to_utc


@dataclass(frozen=True, slots=True)
class RawHRImportSummary:
    rows_imported: int
    source: str


@dataclass(frozen=True, slots=True)
class RawHRAuditSource:
    source: str
    points: int
    first: datetime
    last: datetime


@dataclass(frozen=True, slots=True)
class RawHRAudit:
    total_points: int
    sources: list[RawHRAuditSource]

    @property
    def first(self) -> datetime | None:
        if not self.sources:
            return None
        return min(source.first for source in self.sources)

    @property
    def last(self) -> datetime | None:
        if not self.sources:
            return None
        return max(source.last for source in self.sources)


@dataclass(frozen=True, slots=True)
class ExamWindowHRResult:
    exam: Exam
    window_start: datetime
    window_end: datetime
    points: int
    baseline_points: int
    avg_hr_exam: float | None
    avg_hr_baseline: float | None
    dbpm: float | None
    elevated_percent: float | None
    z_like: float | None


class RawHRDataError(ValueError):
    pass


class RawHRService:
    def __init__(self, session: Session):
        self.session = session

    def import_csv(self, path: str | Path, *, source: str) -> RawHRImportSummary:
        points: list[dict] = []
        with Path(path).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV must include timestamp and hr columns.")
            missing = {"timestamp", "hr"} - set(reader.fieldnames)
            if missing:
                raise ValueError("CSV must include timestamp and hr columns.")
            for row in reader:
                timestamp = parse_datetime(str(row["timestamp"]))
                hr = int(round(float(row["hr"])))
                points.append({"timestamp": timestamp, "hr": hr})

        rows = repositories.upsert_research_raw_hr_points(
            self.session,
            source=source,
            points=points,
        )
        return RawHRImportSummary(rows_imported=rows, source=source)

    def audit(self) -> RawHRAudit:
        points = repositories.list_research_raw_hr_points(self.session)
        by_source: dict[str, list[ResearchRawHRPoint]] = {}
        for point in points:
            by_source.setdefault(point.source, []).append(point)
        sources = [
            RawHRAuditSource(
                source=source,
                points=len(source_points),
                first=min(point.timestamp for point in source_points),
                last=max(point.timestamp for point in source_points),
            )
            for source, source_points in sorted(by_source.items())
        ]
        return RawHRAudit(total_points=len(points), sources=sources)

    def exam_window(self, exam_name: str, *, source: str | None = None) -> ExamWindowHRResult:
        exam = self._find_exam(exam_name)
        window_start = to_utc(exam.exam_at)
        window_end = _exam_end(exam)
        baseline_start = window_start - timedelta(minutes=90)
        points = repositories.list_research_raw_hr_points(self.session, source=source)

        exam_points = [
            point for point in points if window_start <= to_utc(point.timestamp) < window_end
        ]
        baseline_points = [
            point
            for point in points
            if baseline_start <= to_utc(point.timestamp) < window_start
        ]

        if not exam_points or not baseline_points:
            raise RawHRDataError("not enough raw HR data")

        avg_exam = _avg_hr(exam_points)
        avg_baseline = _avg_hr(baseline_points)
        dbpm = (
            avg_exam - avg_baseline
            if avg_exam is not None and avg_baseline is not None
            else None
        )
        elevated_percent = None
        if avg_baseline is not None and exam_points:
            elevated = [point for point in exam_points if point.hr > avg_baseline + 10]
            elevated_percent = (len(elevated) / len(exam_points)) * 100

        z_like = None
        baseline_stddev = _stddev_hr(baseline_points)
        if dbpm is not None and baseline_stddev and baseline_stddev > 0:
            z_like = dbpm / baseline_stddev

        return ExamWindowHRResult(
            exam=exam,
            window_start=window_start,
            window_end=window_end,
            points=len(exam_points),
            baseline_points=len(baseline_points),
            avg_hr_exam=avg_exam,
            avg_hr_baseline=avg_baseline,
            dbpm=dbpm,
            elevated_percent=elevated_percent,
            z_like=z_like,
        )

    def _find_exam(self, exam_name: str) -> Exam:
        needle = exam_name.casefold()
        matches = [
            exam
            for exam in repositories.list_exams(self.session)
            if needle in exam.course.casefold()
        ]
        if not matches:
            raise ValueError(f"No exam found matching {exam_name!r}.")
        return matches[0]


def _exam_end(exam: Exam) -> datetime:
    start = to_utc(exam.exam_at)
    match = re.search(r"\bend\s*:\s*(\d{1,2}):(\d{2})", exam.notes or "", re.IGNORECASE)
    if not match:
        return start + timedelta(hours=2)

    local_start = exam.exam_at
    end_local = local_start.replace(
        hour=int(match.group(1)),
        minute=int(match.group(2)),
        second=0,
        microsecond=0,
    )
    if end_local <= local_start:
        end_local += timedelta(days=1)
    return to_utc(end_local)


def _avg_hr(points: list[ResearchRawHRPoint]) -> float | None:
    if not points:
        return None
    return mean(point.hr for point in points)


def _stddev_hr(points: list[ResearchRawHRPoint]) -> float | None:
    if len(points) < 2:
        return None
    return pstdev(point.hr for point in points)
