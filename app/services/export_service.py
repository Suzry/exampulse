from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from app.core.night_hr import analyze_night_hr_signal
from app.core.stress import compute_exam_stress_index
from app.services.insight_service import InsightService
from app.storage import repositories


@dataclass(frozen=True, slots=True)
class ExportSummary:
    directory: Path
    files: list[Path]


class ExportService:
    def __init__(self, session: Session):
        self.session = session

    def export(self, directory: str | Path = "exports") -> ExportSummary:
        output_dir = Path(directory)
        output_dir.mkdir(parents=True, exist_ok=True)

        files = [
            self._export_exam_scores(output_dir / "exam_scores.csv"),
            self._export_sleep_hr_stream(output_dir / "sleep_hr_stream.csv"),
            self._export_research_raw_hr(output_dir / "research_raw_hr.csv"),
        ]
        return ExportSummary(directory=output_dir, files=files)

    def _export_exam_scores(self, path: Path) -> Path:
        results = InsightService(self.session).generate()
        sleeps = repositories.list_sleeps(self.session)
        stream_points = repositories.list_sleep_stream_points(self.session)
        fields = [
            "course",
            "exam_at",
            "readiness_label",
            "readiness_score",
            "phys_load_score",
            "phys_load_label",
            "sleep_debt_minutes",
            "recovery_delta",
            "hrv_delta_percent",
            "rhr_delta_bpm",
            "night_hr_points",
            "night_hr_avg",
            "night_hr_max",
            "night_hr_baseline",
            "night_hr_delta_bpm",
            "night_hr_elevated_percent",
            "night_hr_spike_count",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for result in results:
                stress = compute_exam_stress_index(result)
                night_hr = analyze_night_hr_signal(
                    result,
                    sleeps=sleeps,
                    stream_points=stream_points,
                )
                writer.writerow(
                    {
                        "course": result.exam.course,
                        "exam_at": result.exam.exam_at.isoformat(),
                        "readiness_label": result.readiness_label,
                        "readiness_score": _csv_float(result.readiness_score),
                        "phys_load_score": stress.score if stress else "",
                        "phys_load_label": stress.label if stress else "",
                        "sleep_debt_minutes": _csv_float(result.sleep_debt_minutes),
                        "recovery_delta": _csv_float(result.recovery_delta),
                        "hrv_delta_percent": _csv_float(result.hrv_delta_percent),
                        "rhr_delta_bpm": _csv_float(result.rhr_delta_bpm),
                        "night_hr_points": night_hr.points or "",
                        "night_hr_avg": _csv_float(night_hr.avg_hr),
                        "night_hr_max": night_hr.max_hr or "",
                        "night_hr_baseline": _csv_float(night_hr.baseline_hr),
                        "night_hr_delta_bpm": _csv_float(night_hr.delta_bpm),
                        "night_hr_elevated_percent": _csv_float(night_hr.elevated_percent),
                        "night_hr_spike_count": night_hr.spike_count
                        if night_hr.spike_count is not None
                        else "",
                    }
                )
        return path

    def _export_sleep_hr_stream(self, path: Path) -> Path:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["sleep_id", "timestamp", "hr", "is_sleeping"],
            )
            writer.writeheader()
            for point in repositories.list_sleep_stream_points(self.session):
                writer.writerow(
                    {
                        "sleep_id": point.sleep_id,
                        "timestamp": point.timestamp.isoformat(),
                        "hr": point.hr,
                        "is_sleeping": point.is_sleeping,
                    }
                )
        return path

    def _export_research_raw_hr(self, path: Path) -> Path:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp", "hr", "source"])
            writer.writeheader()
            for point in repositories.list_research_raw_hr_points(self.session):
                writer.writerow(
                    {
                        "timestamp": point.timestamp.isoformat(),
                        "hr": point.hr,
                        "source": point.source,
                    }
                )
        return path


def _csv_float(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}"
