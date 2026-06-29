from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.analysis import analyze_exam
from app.core.exam_hr import exam_window, match_exam_activity
from app.core.models import Exam, WhoopCycle, WhoopWorkout
from app.services.whoop_export_service import WhoopExportService
from app.storage.repositories import (
    list_cycles,
    list_recoveries,
    list_sleeps,
    list_whoop_workouts,
    upsert_exam,
)
from app.utils.time import to_utc

CYCLES_HEADER = (
    "Cycle start time,Cycle end time,Cycle timezone,Recovery score %,"
    "Resting heart rate (bpm),Heart rate variability (ms),Skin temp (celsius),"
    "Blood oxygen %,Day Strain,Energy burned (cal),Max HR (bpm),Average HR (bpm),"
    "Sleep onset,Wake onset,Sleep performance %,Respiratory rate (rpm),"
    "Asleep duration (min),In bed duration (min),Light sleep duration (min),"
    "Deep (SWS) duration (min),REM duration (min),Awake duration (min),"
    "Sleep need (min),Sleep debt (min),Sleep efficiency %,Sleep consistency %"
)


def _write_cycles(path, rows: list[str]) -> None:
    path.write_text("\n".join([CYCLES_HEADER, *rows]) + "\n", encoding="utf-8")


SLEEPS_HEADER = (
    "Cycle start time,Cycle end time,Cycle timezone,Sleep onset,Wake onset,"
    "Sleep performance %,Respiratory rate (rpm),Asleep duration (min),"
    "In bed duration (min),Light sleep duration (min),Deep (SWS) duration (min),"
    "REM duration (min),Awake duration (min),Sleep need (min),Sleep debt (min),"
    "Sleep efficiency %,Sleep consistency %,Nap"
)


def _write_sleeps(path, rows: list[str]) -> None:
    path.write_text("\n".join([SLEEPS_HEADER, *rows]) + "\n", encoding="utf-8")

WORKOUTS_HEADER = (
    "Cycle start time,Cycle end time,Cycle timezone,Workout start time,"
    "Workout end time,Duration (min),Activity name,Activity Strain,"
    "Energy burned (cal),Max HR (bpm),Average HR (bpm),HR Zone 1 %,HR Zone 2 %,"
    "HR Zone 3 %,HR Zone 4 %,HR Zone 5 %,GPS enabled"
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _write_workouts(path, rows: list[str]) -> None:
    path.write_text("\n".join([WORKOUTS_HEADER, *rows]) + "\n", encoding="utf-8")


def test_import_workouts_parses_local_times_and_zones(tmp_path) -> None:
    path = tmp_path / "workouts.csv"
    _write_workouts(
        path,
        [
            "2026-06-24 08:17:54,2026-06-24 13:35:36,UTC+03:00,"
            "2026-06-24 11:58:00,2026-06-24 12:17:59,19,Activity,6.0,138.0,"
            "178,126,60,19,7,2,0,false"
        ],
    )

    with _session() as session:
        summary = WhoopExportService(session).import_workouts(path)
        workouts = list_whoop_workouts(session)

    assert summary.workouts_saved == 1
    assert summary.activities_with_hr == 1
    workout = workouts[0]
    assert workout.avg_hr == 126
    assert workout.max_hr == 178
    assert workout.hr_zone1_percent == 60
    # Local 11:58 +03:00 -> 08:58 UTC (offset preserved on storage).
    assert to_utc(workout.start).isoformat() == "2026-06-24T08:58:00+00:00"


def test_import_workouts_is_idempotent(tmp_path) -> None:
    path = tmp_path / "workouts.csv"
    _write_workouts(
        path,
        [
            "2026-06-24 08:17:54,2026-06-24 13:35:36,UTC+03:00,"
            "2026-06-24 11:58:00,2026-06-24 12:17:59,19,Activity,6.0,138.0,"
            "178,126,60,19,7,2,0,false"
        ],
    )
    with _session() as session:
        service = WhoopExportService(session)
        service.import_workouts(path)
        service.import_workouts(path)
        assert len(list_whoop_workouts(session)) == 1


def test_exam_window_reads_end_from_notes() -> None:
    exam = Exam(
        course="X",
        exam_at=datetime.fromisoformat("2026-06-24T10:15:00+03:00"),
        notes="Room: 1; End: 12:15",
    )
    start, end = exam_window(exam)
    assert (end - start).total_seconds() == 2 * 60 * 60


def test_match_exam_activity_overlap_and_coverage() -> None:
    exam = Exam(
        course="Software Construction",
        exam_at=datetime.fromisoformat("2026-06-24T10:15:00+03:00"),
        notes="End: 12:15",
    )
    # Activity covers 11:58 -> 12:17 local; overlap with window is 11:58 -> 12:15.
    workout = WhoopWorkout(
        start=datetime.fromisoformat("2026-06-24T11:58:00+03:00"),
        end=datetime.fromisoformat("2026-06-24T12:17:59+03:00"),
        activity_name="Activity",
        avg_hr=126,
        max_hr=178,
        hr_zone1_percent=60,
        hr_zone2_percent=19,
        hr_zone3_percent=7,
        hr_zone4_percent=2,
        hr_zone5_percent=0,
    )

    result = match_exam_activity(exam, [workout])

    assert result.status == "ok"
    assert result.matched == 1
    assert result.avg_hr == 126
    assert result.max_hr == 178
    assert 16 <= result.overlap_minutes <= 18
    # 17 minutes of a 120-minute window.
    assert result.coverage_percent is not None
    assert 13 <= result.coverage_percent <= 15
    assert result.zone_percent[0] == 60


def test_match_exam_activity_reports_no_activity() -> None:
    exam = Exam(
        course="No Activity",
        exam_at=datetime.fromisoformat("2026-06-14T10:15:00+03:00"),
        notes="End: 12:15",
    )
    far_workout = WhoopWorkout(
        start=datetime.fromisoformat("2026-06-14T20:00:00+03:00"),
        end=datetime.fromisoformat("2026-06-14T20:30:00+03:00"),
        activity_name="Run",
        avg_hr=140,
    )
    result = match_exam_activity(exam, [far_workout])
    assert result.status == "no_activity"
    assert result.avg_hr is None


def test_match_exam_activity_weights_multiple_overlaps() -> None:
    exam = Exam(
        course="Long",
        exam_at=datetime.fromisoformat("2026-06-24T10:00:00+03:00"),
        notes="End: 12:00",
    )
    # 60 min at HR 100, then 20 min at HR 160 -> weighted avg ~115.
    first = WhoopWorkout(
        start=datetime.fromisoformat("2026-06-24T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-06-24T11:00:00+03:00"),
        activity_name="A",
        avg_hr=100,
        max_hr=130,
    )
    second = WhoopWorkout(
        start=datetime.fromisoformat("2026-06-24T11:00:00+03:00"),
        end=datetime.fromisoformat("2026-06-24T11:20:00+03:00"),
        activity_name="B",
        avg_hr=160,
        max_hr=180,
    )
    result = match_exam_activity(exam, [first, second])
    assert result.matched == 2
    assert result.max_hr == 180
    assert result.avg_hr is not None
    assert 113 <= result.avg_hr <= 117


def test_import_export_summary_populates_cycles_sleeps_recoveries(tmp_path) -> None:
    _write_cycles(
        tmp_path / "physiological_cycles.csv",
        [
            # A clean night: recovery 70, RHR 60, HRV 40, 7h asleep.
            "2026-06-22 23:00:00,2026-06-23 23:00:00,UTC+03:00,70,60,40,33.0,97.0,"
            "8.0,2000,150,76,2026-06-22 23:00:00,2026-06-23 07:00:00,80,16.5,"
            "420,440,200,110,110,20,480,30,95,70"
        ],
    )

    with _session() as session:
        summary = WhoopExportService(session).import_export(tmp_path)
        cycles = list_cycles(session)
        sleeps = list_sleeps(session)
        recoveries = list_recoveries(session)

    assert summary.cycles_saved == 1
    assert summary.sleeps_saved == 1
    assert summary.recoveries_saved == 1
    assert cycles[0].strain == 8.0
    assert cycles[0].score_state == "SCORED"
    assert sleeps[0].total_sleep_minutes == 420
    assert sleeps[0].nap is False
    assert recoveries[0].recovery_score == 70
    assert recoveries[0].resting_heart_rate == 60
    assert recoveries[0].hrv_rmssd_milli == 40
    # Sleep links back to its cycle so the analysis can join them.
    assert recoveries[0].cycle_id == cycles[0].id
    assert recoveries[0].sleep_id == sleeps[0].id


def test_import_export_feeds_readiness_analysis(tmp_path) -> None:
    rows = []
    # 13 stable baseline nights before the exam, then a worse final night.
    for day in range(1, 14):
        rows.append(
            f"2026-06-{day:02d} 23:00:00,2026-06-{day + 1:02d} 23:00:00,UTC+03:00,"
            f"72,60,40,33.0,97.0,8.0,2000,150,76,"
            f"2026-06-{day:02d} 23:00:00,2026-06-{day + 1:02d} 07:00:00,82,16.5,"
            "440,460,210,115,115,20,480,10,95,72"
        )
    _write_cycles(tmp_path / "physiological_cycles.csv", rows)

    with _session() as session:
        WhoopExportService(session).import_export(tmp_path)
        sleeps = list_sleeps(session)
        recoveries = list_recoveries(session)
        cycles = list_cycles(session)
        exam = Exam(
            course="Final",
            exam_at=datetime.fromisoformat("2026-06-15T10:15:00+03:00"),
            notes="End: 12:15",
        )
        result = analyze_exam(exam, sleeps, recoveries, cycles)

    assert result.recovery is not None
    assert result.recovery.recovery_score == 72
    assert result.baseline_nights >= 10
    assert result.readiness_score is not None


def test_sleeps_csv_is_authoritative_over_cycles(tmp_path) -> None:
    # physiological_cycles links the cycle to a late nap and omits the main
    # morning sleep; sleeps.csv has both. The importer must keep both.
    _write_cycles(
        tmp_path / "physiological_cycles.csv",
        [
            "2026-06-21 06:01:00,2026-06-23 08:17:00,UTC+03:00,47,67,30,34.0,98.0,"
            "8.5,2278,160,81,2026-06-22 22:46:00,2026-06-23 02:05:00,56,17.4,"
            "191,288,52,116,96,24,481,0,91,22"
        ],
    )
    _write_sleeps(
        tmp_path / "sleeps.csv",
        [
            # main morning sleep that physiological_cycles omitted
            "2026-06-21 06:01:00,2026-06-23 08:17:00,UTC+03:00,2026-06-21 06:01:00,"
            "2026-06-21 12:12:00,80,16.5,354,380,150,100,104,26,480,0,93,40,false",
            # the late nap
            "2026-06-21 06:01:00,2026-06-23 08:17:00,UTC+03:00,2026-06-22 22:46:00,"
            "2026-06-23 02:05:00,56,17.4,191,288,52,116,96,24,481,0,91,22,true",
        ],
    )

    with _session() as session:
        WhoopExportService(session).import_export(tmp_path)
        sleeps = list_sleeps(session)

    assert len(sleeps) == 2
    mains = [s for s in sleeps if not s.nap]
    naps = [s for s in sleeps if s.nap]
    assert len(mains) == 1
    assert len(naps) == 1
    assert mains[0].total_sleep_minutes == 354


def test_import_export_replace_clears_existing_summary(tmp_path) -> None:
    _write_cycles(
        tmp_path / "physiological_cycles.csv",
        [
            "2026-06-22 23:00:00,2026-06-23 23:00:00,UTC+03:00,70,60,40,33.0,97.0,"
            "8.0,2000,150,76,2026-06-22 23:00:00,2026-06-23 07:00:00,80,16.5,"
            "420,440,200,110,110,20,480,30,95,70"
        ],
    )
    with _session() as session:
        # Pre-existing API-style row that should be wiped by --replace.
        session.add(
            WhoopCycle(
                id=999,
                start=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
                end=datetime.fromisoformat("2020-01-01T23:00:00+00:00"),
                score_state="SCORED",
                strain=10.0,
            )
        )
        session.commit()
        WhoopExportService(session).import_export(tmp_path, replace=True)
        cycles = list_cycles(session)

    assert all(cycle.id != 999 for cycle in cycles)
    assert len(cycles) == 1


def test_import_export_summary_is_idempotent(tmp_path) -> None:
    _write_cycles(
        tmp_path / "physiological_cycles.csv",
        [
            "2026-06-22 23:00:00,2026-06-23 23:00:00,UTC+03:00,70,60,40,33.0,97.0,"
            "8.0,2000,150,76,2026-06-22 23:00:00,2026-06-23 07:00:00,80,16.5,"
            "420,440,200,110,110,20,480,30,95,70"
        ],
    )
    with _session() as session:
        service = WhoopExportService(session)
        service.import_export(tmp_path)
        service.import_export(tmp_path)
        assert len(list_cycles(session)) == 1
        assert len(list_sleeps(session)) == 1


def test_import_export_requires_a_known_csv(tmp_path) -> None:
    (tmp_path / "unrelated.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    from app.services.whoop_export_service import WhoopExportError

    with _session() as session, pytest.raises(WhoopExportError):
        WhoopExportService(session).import_export(tmp_path / "unrelated.csv")


def test_import_exam_hr_end_to_end(tmp_path) -> None:
    path = tmp_path / "workouts.csv"
    _write_workouts(
        path,
        [
            "2026-06-24 08:17:54,2026-06-24 13:35:36,UTC+03:00,"
            "2026-06-24 11:58:00,2026-06-24 12:17:59,19,Activity,6.0,138.0,"
            "178,126,60,19,7,2,0,false"
        ],
    )
    with _session() as session:
        upsert_exam(
            session,
            course="Software Construction",
            exam_at=datetime.fromisoformat("2026-06-24T10:15:00+03:00"),
            notes="End: 12:15",
        )
        WhoopExportService(session).import_workouts(path)
        workouts = list_whoop_workouts(session)
        exam = session.get(Exam, 1)
        result = match_exam_activity(exam, workouts)

    assert result.status == "ok"
    assert result.avg_hr == 126
