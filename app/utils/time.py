from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def require_timezone(value: str, field_name: str = "datetime") -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def to_utc(value: datetime | str) -> datetime:
    return parse_datetime(value).astimezone(UTC)


def isoformat_utc(value: datetime | str) -> str:
    return to_utc(value).isoformat().replace("+00:00", "Z")


def minutes_ago(minutes: int) -> datetime:
    return utc_now() - timedelta(minutes=minutes)
