from __future__ import annotations

from datetime import datetime

from app.core.models import Exam, WhoopCycle, WhoopRecovery, WhoopSleep
from app.utils.time import to_utc


def next_upcoming_exam(exams: list[Exam], now: datetime) -> Exam | None:
    upcoming = [exam for exam in exams if to_utc(exam.exam_at) > now]
    if not upcoming:
        return None
    return min(upcoming, key=lambda exam: to_utc(exam.exam_at))


def upcoming_exams(exams: list[Exam], now: datetime) -> list[Exam]:
    upcoming = [exam for exam in exams if to_utc(exam.exam_at) > now]
    return sorted(upcoming, key=lambda exam: to_utc(exam.exam_at))


def latest_sleep(sleeps: list[WhoopSleep], now: datetime) -> WhoopSleep | None:
    available = [
        sleep
        for sleep in sleeps
        if sleep.score_state == "SCORED" and to_utc(sleep.end) <= now
    ]
    if not available:
        return None
    main_sleeps = [sleep for sleep in available if not sleep.nap]
    return max(main_sleeps or available, key=lambda sleep: to_utc(sleep.end))


def latest_recovery(
    recoveries: list[WhoopRecovery],
    sleeps: list[WhoopSleep],
    now: datetime,
) -> WhoopRecovery | None:
    sleeps_by_id = {sleep.id: sleep for sleep in sleeps}
    linked = [
        recovery
        for recovery in recoveries
        if recovery.score_state == "SCORED"
        and recovery.sleep_id in sleeps_by_id
        and to_utc(sleeps_by_id[recovery.sleep_id].end) <= now
    ]
    if linked:
        return max(
            linked,
            key=lambda recovery: to_utc(sleeps_by_id[recovery.sleep_id].end),
        )

    scored = [recovery for recovery in recoveries if recovery.score_state == "SCORED"]
    if not scored:
        return None
    return max(scored, key=lambda recovery: recovery.cycle_id)


def latest_cycle(cycles: list[WhoopCycle], now: datetime) -> WhoopCycle | None:
    available = [
        cycle
        for cycle in cycles
        if cycle.score_state == "SCORED"
        and cycle.end is not None
        and to_utc(cycle.end) <= now
    ]
    if not available:
        return None
    return max(available, key=lambda cycle: to_utc(cycle.end or cycle.start))
