from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.apple_health_service import (
    AppleHealthImportError,
    AppleHealthService,
)
from app.storage.repositories import list_recoveries, list_sleeps

EXPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
   value="HKCategoryValueSleepAnalysisAsleepCore"
   startDate="2026-06-20 23:30:00 +0300" endDate="2026-06-21 02:00:00 +0300"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
   value="HKCategoryValueSleepAnalysisAsleepDeep"
   startDate="2026-06-21 02:00:00 +0300" endDate="2026-06-21 03:30:00 +0300"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
   value="HKCategoryValueSleepAnalysisAsleepREM"
   startDate="2026-06-21 03:30:00 +0300" endDate="2026-06-21 05:00:00 +0300"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
   value="HKCategoryValueSleepAnalysisAwake"
   startDate="2026-06-21 05:00:00 +0300" endDate="2026-06-21 05:20:00 +0300"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
   value="HKCategoryValueSleepAnalysisAsleepCore"
   startDate="2026-06-21 05:20:00 +0300" endDate="2026-06-21 06:45:00 +0300"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
   value="HKCategoryValueSleepAnalysisAsleepCore"
   startDate="2026-06-21 14:00:00 +0300" endDate="2026-06-21 15:00:00 +0300"/>
 <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
   value="52.5" startDate="2026-06-21 04:10:00 +0300"
   endDate="2026-06-21 04:11:00 +0300"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate"
   value="57" startDate="2026-06-21 09:00:00 +0300"
   endDate="2026-06-21 09:00:00 +0300"/>
</HealthData>
"""


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_import_groups_sessions_and_links_recovery(tmp_path: Path) -> None:
    xml_path = tmp_path / "export.xml"
    xml_path.write_text(EXPORT_XML, encoding="utf-8")

    with _session(tmp_path) as session:
        summary = AppleHealthService(session).import_export(xml_path)
        sleeps = list_sleeps(session)
        recoveries = list_recoveries(session)

    # One overnight session plus one afternoon nap.
    assert summary.sleeps_saved == 2
    assert len(sleeps) == 2

    main = next(sleep for sleep in sleeps if not sleep.nap)
    nap = next(sleep for sleep in sleeps if sleep.nap)
    # 150 core + 90 deep + 90 rem + 85 core = 415 asleep minutes (awake excluded).
    assert main.total_sleep_minutes == 415
    assert main.slow_wave_minutes == 90
    assert main.rem_minutes == 90
    assert main.awake_minutes == 20
    assert main.score_state == "SCORED"
    assert nap.total_sleep_minutes == 60

    # HRV/RHR from the wake day attach to sessions ending that day.
    assert summary.recoveries_saved == 2
    main_recovery = next(
        recovery for recovery in recoveries if recovery.sleep_id == main.id
    )
    assert main_recovery.hrv_rmssd_milli == 52.5
    assert main_recovery.resting_heart_rate == 57
    assert main_recovery.recovery_score is None


def test_import_is_idempotent(tmp_path: Path) -> None:
    xml_path = tmp_path / "export.xml"
    xml_path.write_text(EXPORT_XML, encoding="utf-8")

    with _session(tmp_path) as session:
        AppleHealthService(session).import_export(xml_path)
        AppleHealthService(session).import_export(xml_path)
        assert len(list_sleeps(session)) == 2


def test_import_reads_zip_archives(tmp_path: Path) -> None:
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("apple_health_export/export.xml", EXPORT_XML)

    with _session(tmp_path) as session:
        summary = AppleHealthService(session).import_export(zip_path)
    assert summary.sleeps_saved == 2


def test_import_rejects_export_without_sleep(tmp_path: Path) -> None:
    xml_path = tmp_path / "export.xml"
    xml_path.write_text(
        '<?xml version="1.0"?><HealthData></HealthData>', encoding="utf-8"
    )
    with _session(tmp_path) as session, pytest.raises(AppleHealthImportError):
        AppleHealthService(session).import_export(xml_path)


def test_overlapping_watch_and_phone_records_do_not_double_count(
    tmp_path: Path,
) -> None:
    xml = """<?xml version="1.0"?><HealthData>
     <Record type="HKCategoryTypeIdentifierSleepAnalysis"
       value="HKCategoryValueSleepAnalysisAsleepCore"
       startDate="2026-06-20 23:00:00 +0300" endDate="2026-06-21 03:00:00 +0300"/>
     <Record type="HKCategoryTypeIdentifierSleepAnalysis"
       value="HKCategoryValueSleepAnalysisAsleepCore"
       startDate="2026-06-20 23:30:00 +0300" endDate="2026-06-21 03:00:00 +0300"/>
    </HealthData>"""
    xml_path = tmp_path / "export.xml"
    xml_path.write_text(xml, encoding="utf-8")

    with _session(tmp_path) as session:
        AppleHealthService(session).import_export(xml_path)
        sleeps = list_sleeps(session)

    assert len(sleeps) == 1
    # Union of 23:00-03:00 and 23:30-03:00 is 240 minutes, not 450.
    assert sleeps[0].total_sleep_minutes == 240
