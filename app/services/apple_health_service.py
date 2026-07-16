from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import IO
from xml.etree.ElementTree import iterparse

from sqlmodel import Session

from app.storage.repositories import delete_all_whoop_summary, upsert_csv_summary

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
RHR_TYPE = "HKQuantityTypeIdentifierRestingHeartRate"

# Sleep-analysis category values that count as actually asleep.
_ASLEEP_STAGES = {
    "HKCategoryValueSleepAnalysisAsleep": "light",
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "light",
    "HKCategoryValueSleepAnalysisAsleepCore": "light",
    "HKCategoryValueSleepAnalysisAsleepDeep": "slow_wave",
    "HKCategoryValueSleepAnalysisAsleepREM": "rem",
}
_IN_BED = "HKCategoryValueSleepAnalysisInBed"
_AWAKE = "HKCategoryValueSleepAnalysisAwake"

# A gap longer than this between sleep records starts a new session.
_SESSION_GAP_MINUTES = 60

# Sessions shorter than this count as naps.
_NAP_MAX_MINUTES = 180


class AppleHealthImportError(ValueError):
    pass


@dataclass(slots=True)
class AppleHealthImportSummary:
    sleeps_saved: int
    recoveries_saved: int
    hrv_days: int
    rhr_days: int
    replaced: bool


@dataclass(slots=True)
class _SleepRecord:
    start: datetime
    end: datetime
    value: str


def _parse_health_datetime(raw: str) -> datetime:
    # Apple Health exports timestamps like "2026-06-20 23:15:00 +0300".
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")


def _union_minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total minutes covered by the union of intervals (watch + phone often
    report overlapping records; summing them would double-count)."""
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += (current_end - current_start).total_seconds() / 60
            current_start, current_end = start, end
    total += (current_end - current_start).total_seconds() / 60
    return total


class AppleHealthService:
    """Imports Apple Health sleep + HRV + resting HR into the same tables the
    WHOOP report reads, so `exampulse report` works with an Apple Watch.

    Honest caveats: Apple has no recovery score (that component is simply
    absent and the readiness weights renormalize), and Apple's HRV is SDNN
    rather than WHOOP's RMSSD — absolute values differ, but deltas against
    your own baseline from the same source remain meaningful.
    """

    def __init__(self, session: Session):
        self.session = session

    def import_export(
        self, path: str | Path, *, replace: bool = False
    ) -> AppleHealthImportSummary:
        path = Path(path)
        sleep_records, hrv_by_day, rhr_by_day = self._parse(path)
        if not sleep_records:
            raise AppleHealthImportError(
                "No sleep-analysis records found in the export."
            )

        if replace:
            delete_all_whoop_summary(self.session)

        sessions = _group_sessions(sleep_records)
        sleeps: list[dict] = []
        recoveries: list[dict] = []
        for session_records in sessions:
            sleep_row, recovery_row = _session_rows(
                session_records, hrv_by_day, rhr_by_day
            )
            if sleep_row is None:
                continue
            sleeps.append(sleep_row)
            if recovery_row is not None:
                recoveries.append(recovery_row)

        upsert_csv_summary(self.session, cycles=[], sleeps=sleeps, recoveries=recoveries)
        return AppleHealthImportSummary(
            sleeps_saved=len(sleeps),
            recoveries_saved=len(recoveries),
            hrv_days=len(hrv_by_day),
            rhr_days=len(rhr_by_day),
            replaced=replace,
        )

    def _parse(
        self, path: Path
    ) -> tuple[list[_SleepRecord], dict[str, list[float]], dict[str, float]]:
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as archive:
                xml_names = [
                    name
                    for name in archive.namelist()
                    if name.endswith("export.xml") and "cda" not in name.casefold()
                ]
                if not xml_names:
                    raise AppleHealthImportError(
                        "export.xml not found inside the zip archive."
                    )
                with archive.open(xml_names[0]) as handle:
                    return self._parse_xml(handle)
        if path.is_dir():
            xml_path = path / "export.xml"
            if not xml_path.exists():
                xml_path = path / "apple_health_export" / "export.xml"
            if not xml_path.exists():
                raise AppleHealthImportError(f"export.xml not found under {path}.")
            with xml_path.open("rb") as handle:
                return self._parse_xml(handle)
        with path.open("rb") as handle:
            return self._parse_xml(handle)

    def _parse_xml(
        self, handle: IO[bytes]
    ) -> tuple[list[_SleepRecord], dict[str, list[float]], dict[str, float]]:
        sleep_records: list[_SleepRecord] = []
        hrv_by_day: dict[str, list[float]] = {}
        rhr_by_day: dict[str, float] = {}

        for _, element in iterparse(handle, events=("end",)):
            if element.tag != "Record":
                continue
            record_type = element.get("type")
            try:
                if record_type == SLEEP_TYPE:
                    sleep_records.append(
                        _SleepRecord(
                            start=_parse_health_datetime(element.get("startDate", "")),
                            end=_parse_health_datetime(element.get("endDate", "")),
                            value=element.get("value", ""),
                        )
                    )
                elif record_type == HRV_TYPE:
                    start = _parse_health_datetime(element.get("startDate", ""))
                    hrv_by_day.setdefault(f"{start:%Y-%m-%d}", []).append(
                        float(element.get("value", ""))
                    )
                elif record_type == RHR_TYPE:
                    start = _parse_health_datetime(element.get("startDate", ""))
                    rhr_by_day[f"{start:%Y-%m-%d}"] = float(element.get("value", ""))
            except ValueError as exc:
                raise AppleHealthImportError(
                    f"Malformed record in export.xml: {exc}"
                ) from exc
            element.clear()

        return sleep_records, hrv_by_day, rhr_by_day


def _group_sessions(records: list[_SleepRecord]) -> list[list[_SleepRecord]]:
    ordered = sorted(records, key=lambda record: record.start)
    sessions: list[list[_SleepRecord]] = []
    current: list[_SleepRecord] = []
    current_end: datetime | None = None
    for record in ordered:
        if current_end is None:
            current = [record]
        else:
            gap_minutes = (record.start - current_end).total_seconds() / 60
            if gap_minutes > _SESSION_GAP_MINUTES:
                sessions.append(current)
                current = [record]
            else:
                current.append(record)
        current_end = max(record.end, current_end or record.end)
    if current:
        sessions.append(current)
    return sessions


def _session_rows(
    records: list[_SleepRecord],
    hrv_by_day: dict[str, list[float]],
    rhr_by_day: dict[str, float],
) -> tuple[dict | None, dict | None]:
    start = min(record.start for record in records)
    end = max(record.end for record in records)

    stage_intervals: dict[str, list[tuple[datetime, datetime]]] = {
        "light": [],
        "slow_wave": [],
        "rem": [],
    }
    asleep_intervals: list[tuple[datetime, datetime]] = []
    awake_intervals: list[tuple[datetime, datetime]] = []
    in_bed_intervals: list[tuple[datetime, datetime]] = []
    for record in records:
        interval = (record.start, record.end)
        if record.value in _ASLEEP_STAGES:
            stage_intervals[_ASLEEP_STAGES[record.value]].append(interval)
            asleep_intervals.append(interval)
        elif record.value == _AWAKE:
            awake_intervals.append(interval)
        elif record.value == _IN_BED:
            in_bed_intervals.append(interval)

    asleep_minutes = _union_minutes(asleep_intervals)
    if asleep_minutes == 0:
        # Older exports only carry InBed records; treat the in-bed span as sleep.
        asleep_minutes = _union_minutes(in_bed_intervals)
        if asleep_minutes == 0:
            return None, None

    in_bed_minutes = _union_minutes(in_bed_intervals) or (
        (end - start).total_seconds() / 60
    )
    day_key = f"{end:%Y-%m-%d}"
    cycle_id = int(f"{end:%Y%m%d}")
    sleep_id = f"ah-{end:%Y%m%d%H%M}"

    sleep_row = {
        "id": sleep_id,
        "cycle_id": cycle_id,
        "start": start,
        "end": end,
        "timezone_offset": f"{start:%z}",
        "nap": asleep_minutes < _NAP_MAX_MINUTES,
        "score_state": "SCORED",
        "total_in_bed_minutes": int(round(in_bed_minutes)),
        "total_sleep_minutes": int(round(asleep_minutes)),
        "rem_minutes": int(round(_union_minutes(stage_intervals["rem"]))) or None,
        "slow_wave_minutes": int(round(_union_minutes(stage_intervals["slow_wave"])))
        or None,
        "light_sleep_minutes": int(round(_union_minutes(stage_intervals["light"])))
        or None,
        "awake_minutes": int(round(_union_minutes(awake_intervals))) or None,
        "raw_json": '{"source":"apple_health"}',
    }

    hrv_values = hrv_by_day.get(day_key, [])
    rhr_value = rhr_by_day.get(day_key)
    if not hrv_values and rhr_value is None:
        return sleep_row, None
    recovery_row = {
        "sleep_id": sleep_id,
        "cycle_id": cycle_id,
        "score_state": "SCORED",
        "recovery_score": None,
        "resting_heart_rate": int(round(rhr_value)) if rhr_value is not None else None,
        "hrv_rmssd_milli": median(hrv_values) if hrv_values else None,
        "raw_json": '{"source":"apple_health","hrv_metric":"sdnn"}',
    }
    return sleep_row, recovery_row