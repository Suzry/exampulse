from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from app.core.analysis import ExamReadiness, clamp, classify_readiness
from app.core.models import Exam, WhoopCycle, WhoopRecovery
from app.core.scoring import ScoringConfig
from app.core.stress import (
    _hrv_pressure,
    _recovery_pressure,
    _rhr_pressure,
    _sleep_pressure,
    _strain_pressure,
    classify_stress,
    compute_exam_stress_index,
)

_CONFIG = ScoringConfig()

maybe_floats = st.one_of(st.none(), st.floats(-2000, 2000, allow_nan=False))


def _readiness(
    sleep_debt: float | None,
    recovery_score: int | None,
    hrv_delta: float | None,
    rhr_delta: float | None,
    strain: float | None,
) -> ExamReadiness:
    recovery = None
    if recovery_score is not None:
        recovery = WhoopRecovery(
            sleep_id="s", cycle_id=1, score_state="SCORED", recovery_score=recovery_score
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
        exam=Exam(course="X", exam_at=datetime(2026, 6, 14, tzinfo=UTC)),
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
        readiness_score=50,
        readiness_label="MODERATE",
        flags=[],
        summary="",
    )


@given(
    sleep_debt=maybe_floats,
    recovery_score=st.one_of(st.none(), st.integers(0, 100)),
    hrv_delta=maybe_floats,
    rhr_delta=maybe_floats,
    strain=st.one_of(st.none(), st.floats(0, 21, allow_nan=False)),
)
def test_stress_score_is_always_within_bounds(
    sleep_debt, recovery_score, hrv_delta, rhr_delta, strain
) -> None:
    result = compute_exam_stress_index(
        _readiness(sleep_debt, recovery_score, hrv_delta, rhr_delta, strain)
    )
    assert result is not None
    assert 0 <= result.score <= 100
    assert result.label in {"low stress", "mild stress", "elevated stress", "high stress"}
    for component in result.components:
        assert 0 <= component.points <= component.max_points


@given(worse=st.floats(-2000, 0, allow_nan=False), better=st.floats(-2000, 0, allow_nan=False))
def test_more_sleep_debt_never_lowers_stress(worse, better) -> None:
    low, high = sorted([worse, better])
    # `low` is deeper sleep debt (more negative), so at least as much pressure.
    assert (
        _sleep_pressure(low, _CONFIG).points >= _sleep_pressure(high, _CONFIG).points
    )


@given(low_recovery=st.integers(0, 100), high_recovery=st.integers(0, 100))
def test_lower_recovery_never_lowers_stress(low_recovery, high_recovery) -> None:
    low, high = sorted([low_recovery, high_recovery])
    assert (
        _recovery_pressure(low, _CONFIG).points
        >= _recovery_pressure(high, _CONFIG).points
    )


@given(a=st.floats(-100, 100, allow_nan=False), b=st.floats(-100, 100, allow_nan=False))
def test_deeper_hrv_drop_never_lowers_stress(a, b) -> None:
    low, high = sorted([a, b])
    assert _hrv_pressure(low, _CONFIG).points >= _hrv_pressure(high, _CONFIG).points


@given(a=st.floats(-50, 50, allow_nan=False), b=st.floats(-50, 50, allow_nan=False))
def test_higher_rhr_never_lowers_stress(a, b) -> None:
    low, high = sorted([a, b])
    assert _rhr_pressure(high, _CONFIG).points >= _rhr_pressure(low, _CONFIG).points


@given(a=st.floats(0, 21, allow_nan=False), b=st.floats(0, 21, allow_nan=False))
def test_higher_strain_never_lowers_stress(a, b) -> None:
    low, high = sorted([a, b])
    assert (
        _strain_pressure(high, _CONFIG).points >= _strain_pressure(low, _CONFIG).points
    )


@given(value=st.floats(-1e6, 1e6, allow_nan=False))
def test_clamp_stays_in_range(value) -> None:
    assert 0 <= clamp(value) <= 100


@given(score=st.one_of(st.none(), st.floats(-50, 150, allow_nan=False)))
def test_readiness_labels_are_total(score) -> None:
    assert classify_readiness(score) in {"LOW", "MODERATE", "GOOD", "UNKNOWN"}


@given(score=st.floats(-50, 150, allow_nan=False))
def test_stress_labels_are_total(score) -> None:
    assert classify_stress(score) in {
        "low stress",
        "mild stress",
        "elevated stress",
        "high stress",
    }
