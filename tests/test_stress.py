from __future__ import annotations

from datetime import UTC, datetime

from app.core.analysis import ExamReadiness
from app.core.models import Exam, WhoopCycle, WhoopRecovery
from app.core.stress import (
    StressComponent,
    classify_stress,
    compute_exam_stress_index,
    top_stress_drivers,
)


def _readiness(
    *,
    sleep_debt: float | None = 0,
    recovery_score: int | None = 85,
    hrv_delta: float | None = 0,
    rhr_delta: float | None = 0,
    strain: float | None = 4,
    label: str = "GOOD",
) -> ExamReadiness:
    recovery = None
    if recovery_score is not None:
        recovery = WhoopRecovery(
            sleep_id="sleep-1",
            cycle_id=1,
            score_state="SCORED",
            recovery_score=recovery_score,
        )
    cycle = None
    if strain is not None:
        cycle = WhoopCycle(
            id=1,
            start=datetime(2026, 6, 13, tzinfo=UTC),
            end=datetime(2026, 6, 14, tzinfo=UTC),
            score_state="SCORED",
            strain=strain,
        )
    return ExamReadiness(
        exam=Exam(course="Operating Systems", exam_at=datetime(2026, 6, 14, tzinfo=UTC)),
        sleep=None,
        recovery=recovery,
        previous_cycle=cycle,
        baseline_sleep_minutes=None,
        baseline_recovery_score=None,
        baseline_hrv=None,
        baseline_rhr=None,
        sleep_debt_minutes=sleep_debt,
        recovery_delta=None,
        hrv_delta_percent=hrv_delta,
        rhr_delta_bpm=rhr_delta,
        readiness_score=80,
        readiness_label=label,
        flags=[],
        summary="ok",
    )


def test_low_load_stress_case() -> None:
    stress = compute_exam_stress_index(_readiness())

    assert stress is not None
    assert stress.score == 0
    assert stress.label == "low stress"


def test_high_load_stress_case_is_capped_at_100() -> None:
    stress = compute_exam_stress_index(
        _readiness(
            sleep_debt=-240,
            recovery_score=20,
            hrv_delta=-30,
            rhr_delta=12,
            strain=18,
        )
    )

    assert stress is not None
    assert stress.score == 100
    assert classify_stress(stress.score) == "high stress"


def test_future_exam_does_not_compute_stress() -> None:
    assert compute_exam_stress_index(_readiness(label="UPCOMING")) is None


def test_top_stress_drivers_returns_highest_components() -> None:
    components = [
        StressComponent("sleep_debt", 10, 25, "mild pressure", "1h below baseline"),
        StressComponent("recovery_drop", 25, 25, "high pressure", "recovery below 30%"),
        StressComponent("hrv_pressure", 0, 20, "neutral", "no meaningful drop"),
        StressComponent("rhr_elevation", 14, 20, "elevated pressure", "+8 bpm"),
        StressComponent("strain_load", 6, 10, "moderate", "previous strain 12.1"),
    ]

    drivers = top_stress_drivers(components, limit=3)

    assert [driver.name for driver in drivers] == [
        "recovery_drop",
        "rhr_elevation",
        "sleep_debt",
    ]
