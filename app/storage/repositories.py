from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlmodel import Session, delete, desc, select

from app.core.models import (
    Exam,
    ExamInsight,
    OAuthToken,
    ResearchRawHRPoint,
    SyncRun,
    WhoopCycle,
    WhoopRecovery,
    WhoopSleep,
    WhoopSleepStreamPoint,
)
from app.utils.time import parse_datetime, utc_now


def _minutes_from_millis(value: int | float | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value) / 60000))


def _as_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def get_oauth_token(session: Session, provider: str = "whoop") -> OAuthToken | None:
    return session.exec(
        select(OAuthToken).where(OAuthToken.provider == provider)
    ).first()


def upsert_oauth_token(
    session: Session,
    *,
    provider: str = "whoop",
    access_token: str,
    refresh_token: str | None,
    expires_in: int,
    scope: str | None,
    token_type: str | None,
) -> OAuthToken:
    token = get_oauth_token(session, provider)
    expires_at = utc_now() + timedelta(seconds=max(expires_in - 60, 0))
    if token is None:
        token = OAuthToken(
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
            token_type=token_type or "bearer",
        )
    else:
        token.access_token = access_token
        if refresh_token:
            token.refresh_token = refresh_token
        token.expires_at = expires_at
        token.scope = scope
        token.token_type = token_type or token.token_type
        token.updated_at = utc_now()

    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def upsert_sleep(session: Session, payload: dict[str, Any]) -> WhoopSleep:
    score = payload.get("score") or {}
    stage = score.get("stage_summary") or {}
    light_minutes = _minutes_from_millis(stage.get("total_light_sleep_time_milli"))
    slow_minutes = _minutes_from_millis(stage.get("total_slow_wave_sleep_time_milli"))
    rem_minutes = _minutes_from_millis(stage.get("total_rem_sleep_time_milli"))
    total_sleep_minutes = None
    if None not in (light_minutes, slow_minutes, rem_minutes):
        total_sleep_minutes = light_minutes + slow_minutes + rem_minutes

    sleep = session.get(WhoopSleep, payload["id"])
    values = {
        "cycle_id": payload["cycle_id"],
        "user_id": payload.get("user_id"),
        "created_at": parse_datetime(payload["created_at"])
        if payload.get("created_at")
        else None,
        "updated_at": parse_datetime(payload["updated_at"])
        if payload.get("updated_at")
        else None,
        "start": parse_datetime(payload["start"]),
        "end": parse_datetime(payload["end"]),
        "timezone_offset": payload.get("timezone_offset"),
        "nap": bool(payload.get("nap", False)),
        "score_state": payload.get("score_state", "UNKNOWN"),
        "total_in_bed_minutes": _minutes_from_millis(
            stage.get("total_in_bed_time_milli")
        ),
        "total_sleep_minutes": total_sleep_minutes,
        "rem_minutes": rem_minutes,
        "slow_wave_minutes": slow_minutes,
        "light_sleep_minutes": light_minutes,
        "awake_minutes": _minutes_from_millis(stage.get("total_awake_time_milli")),
        "sleep_performance_percentage": score.get("sleep_performance_percentage"),
        "sleep_efficiency_percentage": score.get("sleep_efficiency_percentage"),
        "sleep_consistency_percentage": score.get("sleep_consistency_percentage"),
        "respiratory_rate": score.get("respiratory_rate"),
        "raw_json": _as_json(payload),
    }
    if sleep is None:
        sleep = WhoopSleep(id=payload["id"], **values)
    else:
        for key, value in values.items():
            setattr(sleep, key, value)
    session.add(sleep)
    session.commit()
    session.refresh(sleep)
    return sleep


def upsert_recovery(session: Session, payload: dict[str, Any]) -> WhoopRecovery:
    score = payload.get("score") or {}
    recovery = session.get(WhoopRecovery, payload["sleep_id"])
    values = {
        "cycle_id": payload["cycle_id"],
        "user_id": payload.get("user_id"),
        "created_at": parse_datetime(payload["created_at"])
        if payload.get("created_at")
        else None,
        "updated_at": parse_datetime(payload["updated_at"])
        if payload.get("updated_at")
        else None,
        "score_state": payload.get("score_state", "UNKNOWN"),
        "recovery_score": score.get("recovery_score"),
        "resting_heart_rate": score.get("resting_heart_rate"),
        "hrv_rmssd_milli": score.get("hrv_rmssd_milli"),
        "spo2_percentage": score.get("spo2_percentage"),
        "skin_temp_celsius": score.get("skin_temp_celsius"),
        "raw_json": _as_json(payload),
    }
    if recovery is None:
        recovery = WhoopRecovery(sleep_id=payload["sleep_id"], **values)
    else:
        for key, value in values.items():
            setattr(recovery, key, value)
    session.add(recovery)
    session.commit()
    session.refresh(recovery)
    return recovery


def upsert_cycle(session: Session, payload: dict[str, Any]) -> WhoopCycle:
    score = payload.get("score") or {}
    cycle = session.get(WhoopCycle, payload["id"])
    values = {
        "user_id": payload.get("user_id"),
        "created_at": parse_datetime(payload["created_at"])
        if payload.get("created_at")
        else None,
        "updated_at": parse_datetime(payload["updated_at"])
        if payload.get("updated_at")
        else None,
        "start": parse_datetime(payload["start"]),
        "end": parse_datetime(payload["end"]) if payload.get("end") else None,
        "timezone_offset": payload.get("timezone_offset"),
        "score_state": payload.get("score_state", "UNKNOWN"),
        "strain": score.get("strain"),
        "kilojoule": score.get("kilojoule"),
        "average_heart_rate": score.get("average_heart_rate"),
        "max_heart_rate": score.get("max_heart_rate"),
        "raw_json": _as_json(payload),
    }
    if cycle is None:
        cycle = WhoopCycle(id=payload["id"], **values)
    else:
        for key, value in values.items():
            setattr(cycle, key, value)
    session.add(cycle)
    session.commit()
    session.refresh(cycle)
    return cycle


def upsert_sleep_stream_points(
    session: Session,
    *,
    sleep_id: str,
    points: list[dict[str, Any]],
) -> int:
    saved = 0
    for point in points:
        timestamp = parse_datetime(point["timestamp"])
        existing = session.exec(
            select(WhoopSleepStreamPoint).where(
                WhoopSleepStreamPoint.sleep_id == sleep_id,
                WhoopSleepStreamPoint.timestamp == timestamp,
            )
        ).first()
        values = {
            "hr": int(point["hr"]),
            "is_sleeping": bool(point.get("is_sleeping", True)),
        }
        if existing is None:
            existing = WhoopSleepStreamPoint(
                sleep_id=sleep_id,
                timestamp=timestamp,
                **values,
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        session.add(existing)
        saved += 1
    session.commit()
    return saved


def upsert_exam(
    session: Session,
    *,
    course: str,
    exam_at,
    grade: float | None = None,
    notes: str = "",
) -> Exam:
    existing = session.exec(
        select(Exam).where(Exam.course == course, Exam.exam_at == exam_at)
    ).first()
    if existing is None:
        existing = Exam(course=course, exam_at=exam_at, grade=grade, notes=notes or "")
    else:
        existing.grade = grade
        existing.notes = notes or ""
        existing.updated_at = utc_now()
    session.add(existing)
    session.commit()
    session.refresh(existing)
    return existing


def list_exams(session: Session) -> list[Exam]:
    return list(session.exec(select(Exam).order_by(Exam.exam_at)))


def delete_all_exams(session: Session) -> None:
    session.exec(delete(ExamInsight))
    session.exec(delete(Exam))
    session.commit()


def list_sleeps(session: Session) -> list[WhoopSleep]:
    return list(session.exec(select(WhoopSleep).order_by(WhoopSleep.end)))


def list_recoveries(session: Session) -> list[WhoopRecovery]:
    return list(session.exec(select(WhoopRecovery)))


def list_cycles(session: Session) -> list[WhoopCycle]:
    return list(session.exec(select(WhoopCycle).order_by(WhoopCycle.start)))


def list_sleep_stream_points(
    session: Session,
    *,
    sleep_id: str | None = None,
) -> list[WhoopSleepStreamPoint]:
    statement = select(WhoopSleepStreamPoint)
    if sleep_id is not None:
        statement = statement.where(WhoopSleepStreamPoint.sleep_id == sleep_id)
    return list(session.exec(statement.order_by(WhoopSleepStreamPoint.timestamp)))


def upsert_research_raw_hr_points(
    session: Session,
    *,
    source: str,
    points: list[dict[str, Any]],
) -> int:
    saved = 0
    for point in points:
        timestamp = parse_datetime(point["timestamp"])
        existing = session.exec(
            select(ResearchRawHRPoint).where(
                ResearchRawHRPoint.timestamp == timestamp,
                ResearchRawHRPoint.source == source,
            )
        ).first()
        if existing is None:
            existing = ResearchRawHRPoint(
                timestamp=timestamp,
                hr=int(point["hr"]),
                source=source,
            )
        else:
            existing.hr = int(point["hr"])
        session.add(existing)
        saved += 1
    session.commit()
    return saved


def list_research_raw_hr_points(
    session: Session,
    *,
    source: str | None = None,
) -> list[ResearchRawHRPoint]:
    statement = select(ResearchRawHRPoint)
    if source is not None:
        statement = statement.where(ResearchRawHRPoint.source == source)
    return list(session.exec(statement.order_by(ResearchRawHRPoint.timestamp)))


def save_exam_insight(
    session: Session,
    *,
    exam_id: int,
    readiness_score: float | None,
    readiness_label: str,
    sleep_debt_minutes: float | None,
    hrv_delta_percent: float | None,
    rhr_delta_bpm: float | None,
    recovery_delta: float | None,
    summary: str,
) -> ExamInsight:
    insight = session.exec(
        select(ExamInsight).where(ExamInsight.exam_id == exam_id)
    ).first()
    values = {
        "readiness_score": readiness_score,
        "readiness_label": readiness_label,
        "sleep_debt_minutes": sleep_debt_minutes,
        "hrv_delta_percent": hrv_delta_percent,
        "rhr_delta_bpm": rhr_delta_bpm,
        "recovery_delta": recovery_delta,
        "summary": summary,
        "computed_at": utc_now(),
    }
    if insight is None:
        insight = ExamInsight(exam_id=exam_id, **values)
    else:
        for key, value in values.items():
            setattr(insight, key, value)
    session.add(insight)
    session.commit()
    session.refresh(insight)
    return insight


def save_sync_run(
    session: Session,
    *,
    source: str = "whoop",
    days: int,
    sleeps_saved: int,
    recoveries_saved: int,
    cycles_saved: int,
    skipped_records: int,
    started_at,
    status: str = "ok",
    message: str = "",
) -> SyncRun:
    run = SyncRun(
        source=source,
        days=days,
        sleeps_saved=sleeps_saved,
        recoveries_saved=recoveries_saved,
        cycles_saved=cycles_saved,
        skipped_records=skipped_records,
        started_at=started_at,
        completed_at=utc_now(),
        status=status,
        message=message,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def latest_sync_run(session: Session) -> SyncRun | None:
    return session.exec(
        select(SyncRun)
        .where(SyncRun.source != "whoop_raw_check")
        .order_by(desc(SyncRun.started_at))
    ).first()


def latest_whoop_raw_check(session: Session) -> SyncRun | None:
    return session.exec(
        select(SyncRun)
        .where(SyncRun.source == "whoop_raw_check")
        .order_by(desc(SyncRun.started_at))
    ).first()


def has_demo_data(session: Session) -> bool:
    demo_marker = '"source":"demo-seed"'
    sleep = session.exec(
        select(WhoopSleep).where(WhoopSleep.raw_json.contains(demo_marker))
    ).first()
    if sleep is not None:
        return True
    recovery = session.exec(
        select(WhoopRecovery).where(WhoopRecovery.raw_json.contains(demo_marker))
    ).first()
    if recovery is not None:
        return True
    cycle = session.exec(
        select(WhoopCycle).where(WhoopCycle.raw_json.contains(demo_marker))
    ).first()
    return cycle is not None
