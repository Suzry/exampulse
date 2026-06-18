from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel, UniqueConstraint

from app.utils.time import parse_datetime, utc_now


class AwareDateTime(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return parse_datetime(value).isoformat()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return parse_datetime(value)


def datetime_column(*, nullable: bool = False, index: bool = False) -> Column:
    return Column(AwareDateTime(), nullable=nullable, index=index)


class OAuthToken(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("provider", name="uq_oauth_provider"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(default="whoop", index=True)
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime = Field(sa_column=datetime_column())
    scope: str | None = None
    token_type: str = "bearer"
    updated_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())


class WhoopSleep(SQLModel, table=True):
    id: str = Field(primary_key=True)
    cycle_id: int = Field(index=True)
    user_id: int | None = Field(default=None, index=True)
    created_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    updated_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    start: datetime = Field(sa_column=datetime_column(index=True))
    end: datetime = Field(sa_column=datetime_column(index=True))
    timezone_offset: str | None = None
    nap: bool = Field(default=False, index=True)
    score_state: str = Field(default="UNKNOWN", index=True)
    total_in_bed_minutes: int | None = None
    total_sleep_minutes: int | None = None
    rem_minutes: int | None = None
    slow_wave_minutes: int | None = None
    light_sleep_minutes: int | None = None
    awake_minutes: int | None = None
    sleep_performance_percentage: float | None = None
    sleep_efficiency_percentage: float | None = None
    sleep_consistency_percentage: float | None = None
    respiratory_rate: float | None = None
    raw_json: str = ""


class WhoopSleepStreamPoint(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("sleep_id", "timestamp", name="uq_sleep_stream_sleep_time"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    sleep_id: str = Field(index=True)
    timestamp: datetime = Field(sa_column=datetime_column(index=True))
    hr: int
    is_sleeping: bool = True
    created_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())


class WhoopRecovery(SQLModel, table=True):
    sleep_id: str = Field(primary_key=True)
    cycle_id: int = Field(index=True)
    user_id: int | None = Field(default=None, index=True)
    created_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    updated_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    score_state: str = Field(default="UNKNOWN", index=True)
    recovery_score: int | None = None
    resting_heart_rate: int | None = None
    hrv_rmssd_milli: float | None = None
    spo2_percentage: float | None = None
    skin_temp_celsius: float | None = None
    raw_json: str = ""


class WhoopCycle(SQLModel, table=True):
    id: int = Field(primary_key=True)
    user_id: int | None = Field(default=None, index=True)
    created_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    updated_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    start: datetime = Field(sa_column=datetime_column(index=True))
    end: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True, index=True)
    )
    timezone_offset: str | None = None
    score_state: str = Field(default="UNKNOWN", index=True)
    strain: float | None = None
    kilojoule: float | None = None
    average_heart_rate: int | None = None
    max_heart_rate: int | None = None
    raw_json: str = ""


class Exam(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("course", "exam_at", name="uq_exam_course_time"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    course: str = Field(index=True)
    exam_at: datetime = Field(sa_column=datetime_column(index=True))
    grade: float | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())
    updated_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())


class ExamInsight(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("exam_id", name="uq_exam_insight_exam"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    readiness_score: float | None = Field(default=None, index=True)
    readiness_label: str = Field(default="UNKNOWN", index=True)
    sleep_debt_minutes: float | None = None
    hrv_delta_percent: float | None = None
    rhr_delta_bpm: float | None = None
    recovery_delta: float | None = None
    summary: str = ""
    computed_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())


class SyncRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(default="whoop", index=True)
    started_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())
    completed_at: datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True)
    )
    days: int
    sleeps_saved: int = 0
    recoveries_saved: int = 0
    cycles_saved: int = 0
    skipped_records: int = 0
    status: str = Field(default="ok", index=True)
    message: str = ""


class ResearchRawHRPoint(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("timestamp", "source", name="uq_research_hr_time_source"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(sa_column=datetime_column(index=True))
    hr: int
    source: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, sa_column=datetime_column())
