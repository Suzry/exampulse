from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlmodel import Session

from app.core.exam_hr import exam_window_hr
from app.core.models import Exam, ResearchRawHRPoint
from app.storage import repositories
from app.utils.time import parse_datetime


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


# Common column-name aliases so per-minute exports from different tools import
# without renaming. Matching is case-insensitive and whitespace-insensitive.
_TIMESTAMP_ALIASES = (
    "timestamp",
    "time",
    "datetime",
    "date",
    "ts",
    "start",
    "start time",
    "time (iso)",
)
_HR_ALIASES = (
    "hr",
    "heart_rate",
    "heart rate",
    "heartrate",
    "bpm",
    "heart rate (bpm)",
    "hr (bpm)",
    "value",
)


def _resolve_column(
    fieldnames: Sequence[str], requested: str | None, aliases: tuple[str, ...], label: str
) -> str:
    lookup = {(name or "").strip().casefold(): name for name in fieldnames}
    if requested:
        match = lookup.get(requested.strip().casefold())
        if match is None:
            raise ValueError(f"Column {requested!r} not found in CSV.")
        return match
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    raise ValueError(
        f"Could not find a {label} column. Looked for {', '.join(aliases[:4])}, ... "
        f"Pass an explicit column name to override."
    )


class RawHRService:
    def __init__(self, session: Session):
        self.session = session

    def import_csv(
        self,
        path: str | Path,
        *,
        source: str,
        timestamp_col: str | None = None,
        hr_col: str | None = None,
    ) -> RawHRImportSummary:
        points: list[dict] = []
        with Path(path).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV is empty or has no header row.")
            ts_field = _resolve_column(
                reader.fieldnames, timestamp_col, _TIMESTAMP_ALIASES, "timestamp"
            )
            hr_field = _resolve_column(reader.fieldnames, hr_col, _HR_ALIASES, "heart-rate")
            for row in reader:
                raw_ts = str(row.get(ts_field) or "").strip()
                raw_hr = str(row.get(hr_field) or "").strip()
                if not raw_ts or not raw_hr:
                    continue
                timestamp = parse_datetime(raw_ts)
                hr = int(round(float(raw_hr)))
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
        points = repositories.list_research_raw_hr_points(self.session, source=source)

        result = exam_window_hr(exam, points)
        if result.status != "ok":
            raise RawHRDataError("not enough raw HR data")

        return ExamWindowHRResult(
            exam=exam,
            window_start=result.window_start,
            window_end=result.window_end,
            points=result.exam_points,
            baseline_points=result.baseline_points,
            avg_hr_exam=result.avg_exam,
            avg_hr_baseline=result.avg_baseline,
            dbpm=result.dbpm,
            elevated_percent=result.elevated_percent,
            z_like=result.z,
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
