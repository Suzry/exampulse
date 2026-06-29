from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from app.storage import repositories
from app.utils.time import parse_datetime, utc_now

WORKOUTS_FILENAME = "workouts.csv"
CYCLES_FILENAME = "physiological_cycles.csv"
SLEEPS_FILENAME = "sleeps.csv"

# Marker stored in raw_json so CSV-imported rows are distinguishable and never
# mistaken for demo-seed data (which uses a different marker).
CSV_SOURCE_MARKER = '{"source":"whoop_export_csv"}'


class WhoopExportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WhoopExportSummary:
    workouts_saved: int
    activities_with_hr: int
    cycles_saved: int
    sleeps_saved: int
    recoveries_saved: int
    replaced: bool
    source: str


def _read_member(path: Path, filename: str) -> str | None:
    """Return the text of ``filename`` from a zip, directory, or matching file."""
    if path.is_dir():
        candidate = path / filename
        return candidate.read_text(encoding="utf-8") if candidate.exists() else None
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(filename)]
            return archive.read(names[0]).decode("utf-8") if names else None
    if path.suffix.lower() == ".csv" and path.name.endswith(filename):
        return path.read_text(encoding="utf-8")
    return None


def _tz_offset(timezone_value: str | None) -> str:
    """Convert WHOOP's ``UTC+03:00`` / ``UTCZ`` timezone column to an ISO offset."""
    if not timezone_value:
        return "+00:00"
    offset = timezone_value.strip().upper().replace("UTC", "").strip()
    return offset or "+00:00"


def _combine(local_value: str, offset: str) -> str:
    return f"{local_value.strip()}{offset}"


def _float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    number = _float(value)
    return int(round(number)) if number is not None else None


def _header_map(fieldnames) -> dict[str, str]:
    return {(name or "").strip().casefold(): name for name in (fieldnames or [])}


def _sleep_id_from(onset_iso: str) -> str:
    """Stable sleep id derived from the sleep onset timestamp."""
    return f"export-sleep-{int(parse_datetime(onset_iso).timestamp())}"


class WhoopExportService:
    def __init__(self, session: Session):
        self.session = session

    # -- workouts ---------------------------------------------------------

    def import_workouts(
        self, path: str | Path, *, source: str = "whoop_export"
    ) -> WhoopExportSummary:
        text = _read_member(Path(path), WORKOUTS_FILENAME)
        if text is None:
            raise WhoopExportError(
                f"{WORKOUTS_FILENAME} not found in {Path(path).name}"
            )
        saved, with_hr = self._import_workouts_text(text, source)
        return WhoopExportSummary(
            workouts_saved=saved,
            activities_with_hr=with_hr,
            cycles_saved=0,
            sleeps_saved=0,
            recoveries_saved=0,
            replaced=False,
            source=source,
        )

    def _import_workouts_text(self, text: str, source: str) -> tuple[int, int]:
        reader = csv.DictReader(io.StringIO(text))
        header = _header_map(reader.fieldnames)
        missing = {"workout start time", "workout end time", "activity name"} - set(
            header
        )
        if missing:
            raise WhoopExportError(
                "workouts.csv is missing expected columns: "
                + ", ".join(sorted(missing))
            )

        def cell(row: dict, key: str) -> str | None:
            name = header.get(key)
            return row.get(name) if name else None

        workouts: list[dict] = []
        for row in reader:
            start_local = cell(row, "workout start time")
            end_local = cell(row, "workout end time")
            if not start_local or not end_local:
                continue
            offset = _tz_offset(cell(row, "cycle timezone"))
            workouts.append(
                {
                    "start": _combine(start_local, offset),
                    "end": _combine(end_local, offset),
                    "timezone_offset": offset,
                    "activity_name": (cell(row, "activity name") or "Activity").strip(),
                    "duration_minutes": _float(cell(row, "duration (min)")),
                    "strain": _float(cell(row, "activity strain")),
                    "energy_cal": _float(cell(row, "energy burned (cal)")),
                    "max_hr": _int(cell(row, "max hr (bpm)")),
                    "avg_hr": _int(cell(row, "average hr (bpm)")),
                    "hr_zone1_percent": _float(cell(row, "hr zone 1 %")),
                    "hr_zone2_percent": _float(cell(row, "hr zone 2 %")),
                    "hr_zone3_percent": _float(cell(row, "hr zone 3 %")),
                    "hr_zone4_percent": _float(cell(row, "hr zone 4 %")),
                    "hr_zone5_percent": _float(cell(row, "hr zone 5 %")),
                    "source": source,
                }
            )
        saved = repositories.upsert_whoop_workouts(self.session, workouts=workouts)
        with_hr = sum(1 for workout in workouts if workout["avg_hr"] is not None)
        return saved, with_hr

    # -- full export ------------------------------------------------------

    def import_export(
        self,
        path: str | Path,
        *,
        source: str = "whoop_export",
        replace: bool = False,
    ) -> WhoopExportSummary:
        """Import workouts and the per-cycle summary (recovery/sleep/strain).

        With ``replace=True``, existing synced WHOOP sleep/recovery/cycle rows
        are cleared first so the report runs purely on the export — useful for a
        clean offline dataset without the API or ngrok.
        """
        base = Path(path)
        started_at = utc_now()

        workouts_text = _read_member(base, WORKOUTS_FILENAME)
        cycles_text = _read_member(base, CYCLES_FILENAME)
        sleeps_text = _read_member(base, SLEEPS_FILENAME)
        if workouts_text is None and cycles_text is None and sleeps_text is None:
            raise WhoopExportError(
                f"None of {WORKOUTS_FILENAME}, {CYCLES_FILENAME}, or "
                f"{SLEEPS_FILENAME} found in {base.name}."
            )

        workouts_saved = with_hr = 0
        if workouts_text is not None:
            workouts_saved, with_hr = self._import_workouts_text(workouts_text, source)

        if replace:
            repositories.delete_all_whoop_summary(self.session)

        cycles_saved = sleeps_saved = recoveries_saved = 0
        if cycles_text is not None or sleeps_text is not None:
            cycles, sleeps_fallback, recoveries = (
                self._parse_cycles_text(cycles_text) if cycles_text else ([], [], [])
            )
            # sleeps.csv is the authoritative, complete sleep list (it includes
            # naps and main sleeps that physiological_cycles.csv omits).
            sleeps = (
                self._parse_sleeps_text(sleeps_text)
                if sleeps_text is not None
                else sleeps_fallback
            )
            repositories.upsert_csv_summary(
                self.session,
                cycles=cycles,
                sleeps=sleeps,
                recoveries=recoveries,
            )
            cycles_saved = len(cycles)
            sleeps_saved = len(sleeps)
            recoveries_saved = len(recoveries)
            repositories.save_sync_run(
                self.session,
                source="whoop_export",
                days=cycles_saved,
                sleeps_saved=sleeps_saved,
                recoveries_saved=recoveries_saved,
                cycles_saved=cycles_saved,
                skipped_records=0,
                started_at=started_at,
                message="imported from WHOOP CSV export",
            )

        return WhoopExportSummary(
            workouts_saved=workouts_saved,
            activities_with_hr=with_hr,
            cycles_saved=cycles_saved,
            sleeps_saved=sleeps_saved,
            recoveries_saved=recoveries_saved,
            replaced=replace,
            source=source,
        )

    def _parse_cycles_text(
        self, text: str
    ) -> tuple[list[dict], list[dict], list[dict]]:
        reader = csv.DictReader(io.StringIO(text))
        header = _header_map(reader.fieldnames)
        if "cycle start time" not in header:
            raise WhoopExportError(
                f"{CYCLES_FILENAME} is missing the 'Cycle start time' column."
            )

        def cell(row: dict, key: str) -> str | None:
            name = header.get(key)
            return row.get(name) if name else None

        cycles: list[dict] = []
        sleeps: list[dict] = []
        recoveries: list[dict] = []
        for row in reader:
            cycle_start = cell(row, "cycle start time")
            if not cycle_start:
                continue
            offset = _tz_offset(cell(row, "cycle timezone"))
            start_iso = _combine(cycle_start, offset)
            cycle_id = int(parse_datetime(start_iso).timestamp())
            cycle_end = cell(row, "cycle end time")
            end_iso = _combine(cycle_end, offset) if cycle_end else None
            strain = _float(cell(row, "day strain"))

            cycles.append(
                {
                    "id": cycle_id,
                    "start": start_iso,
                    "end": end_iso,
                    "score_state": "SCORED" if strain is not None and end_iso else "UNKNOWN",
                    "strain": strain,
                    "average_heart_rate": _int(cell(row, "average hr (bpm)")),
                    "max_heart_rate": _int(cell(row, "max hr (bpm)")),
                    "raw_json": CSV_SOURCE_MARKER,
                }
            )

            onset = cell(row, "sleep onset")
            wake = cell(row, "wake onset")
            if onset and wake:
                onset_iso = _combine(onset, offset)
                sleep_id = _sleep_id_from(onset_iso)
                asleep = _int(cell(row, "asleep duration (min)"))
                sleeps.append(
                    {
                        "id": sleep_id,
                        "cycle_id": cycle_id,
                        "start": _combine(onset, offset),
                        "end": _combine(wake, offset),
                        "nap": False,
                        "score_state": "SCORED" if asleep is not None else "UNKNOWN",
                        "total_in_bed_minutes": _int(cell(row, "in bed duration (min)")),
                        "total_sleep_minutes": asleep,
                        "rem_minutes": _int(cell(row, "rem duration (min)")),
                        "slow_wave_minutes": _int(cell(row, "deep (sws) duration (min)")),
                        "light_sleep_minutes": _int(cell(row, "light sleep duration (min)")),
                        "awake_minutes": _int(cell(row, "awake duration (min)")),
                        "sleep_performance_percentage": _float(
                            cell(row, "sleep performance %")
                        ),
                        "sleep_efficiency_percentage": _float(
                            cell(row, "sleep efficiency %")
                        ),
                        "sleep_consistency_percentage": _float(
                            cell(row, "sleep consistency %")
                        ),
                        "respiratory_rate": _float(cell(row, "respiratory rate (rpm)")),
                        "raw_json": CSV_SOURCE_MARKER,
                    }
                )

                recovery_score = _int(cell(row, "recovery score %"))
                rhr = _int(cell(row, "resting heart rate (bpm)"))
                hrv = _float(cell(row, "heart rate variability (ms)"))
                if recovery_score is not None or rhr is not None:
                    recoveries.append(
                        {
                            "sleep_id": sleep_id,
                            "cycle_id": cycle_id,
                            "score_state": "SCORED"
                            if recovery_score is not None
                            else "UNKNOWN",
                            "recovery_score": recovery_score,
                            "resting_heart_rate": rhr,
                            "hrv_rmssd_milli": hrv,
                            "spo2_percentage": _float(cell(row, "blood oxygen %")),
                            "skin_temp_celsius": _float(cell(row, "skin temp (celsius)")),
                            "raw_json": CSV_SOURCE_MARKER,
                        }
                    )

        return cycles, sleeps, recoveries

    def _parse_sleeps_text(self, text: str) -> list[dict]:
        """Parse the authoritative sleep list from ``sleeps.csv`` (incl. naps)."""
        reader = csv.DictReader(io.StringIO(text))
        header = _header_map(reader.fieldnames)
        if "sleep onset" not in header:
            raise WhoopExportError(
                f"{SLEEPS_FILENAME} is missing the 'Sleep onset' column."
            )

        def cell(row: dict, key: str) -> str | None:
            name = header.get(key)
            return row.get(name) if name else None

        sleeps: list[dict] = []
        for row in reader:
            onset = cell(row, "sleep onset")
            wake = cell(row, "wake onset")
            if not onset or not wake:
                continue
            offset = _tz_offset(cell(row, "cycle timezone"))
            onset_iso = _combine(onset, offset)
            cycle_start = cell(row, "cycle start time")
            cycle_id = (
                int(parse_datetime(_combine(cycle_start, offset)).timestamp())
                if cycle_start
                else 0
            )
            asleep = _int(cell(row, "asleep duration (min)"))
            nap = (cell(row, "nap") or "").strip().casefold() == "true"
            sleeps.append(
                {
                    "id": _sleep_id_from(onset_iso),
                    "cycle_id": cycle_id,
                    "start": onset_iso,
                    "end": _combine(wake, offset),
                    "nap": nap,
                    "score_state": "SCORED" if asleep is not None else "UNKNOWN",
                    "total_in_bed_minutes": _int(cell(row, "in bed duration (min)")),
                    "total_sleep_minutes": asleep,
                    "rem_minutes": _int(cell(row, "rem duration (min)")),
                    "slow_wave_minutes": _int(cell(row, "deep (sws) duration (min)")),
                    "light_sleep_minutes": _int(cell(row, "light sleep duration (min)")),
                    "awake_minutes": _int(cell(row, "awake duration (min)")),
                    "sleep_performance_percentage": _float(
                        cell(row, "sleep performance %")
                    ),
                    "sleep_efficiency_percentage": _float(cell(row, "sleep efficiency %")),
                    "sleep_consistency_percentage": _float(
                        cell(row, "sleep consistency %")
                    ),
                    "respiratory_rate": _float(cell(row, "respiratory rate (rpm)")),
                    "raw_json": CSV_SOURCE_MARKER,
                }
            )
        return sleeps
