from __future__ import annotations

import re
import sys
from datetime import datetime


def supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").casefold()
    normalized = encoding.replace("-", "").replace("_", "")
    return normalized.startswith("utf")


def status_color(status: str | None) -> str:
    normalized = (status or "").casefold()
    if normalized == "good":
        return "green"
    if normalized == "moderate":
        return "yellow"
    if normalized == "low":
        return "red"
    if normalized == "upcoming":
        return "cyan"
    return "dim"


def make_bar(
    value: float | int | None,
    max_value: float = 100,
    width: int = 20,
    unicode: bool | None = None,
) -> str:
    use_unicode = supports_unicode() if unicode is None else unicode
    fill = "█" if use_unicode else "#"
    empty = "░" if use_unicode else "-"
    if value is None or max_value <= 0 or width <= 0:
        return empty * max(width, 0)
    ratio = max(0.0, min(float(value) / float(max_value), 1.0))
    filled = int(round(ratio * width))
    return (fill * filled) + (empty * (width - filled))


def horizontal_rule(width: int = 60) -> str:
    return ("─" if supports_unicode() else "-") * width


def sleep_debt_marker(value: float | int | None) -> str:
    if value is None:
        return ""
    if supports_unicode():
        return "▾" if value < 0 else "▴"
    return "v" if value < 0 else "^"


def format_compact_datetime(value: datetime) -> str:
    if value.tzinfo is not None and value.utcoffset() is not None:
        value = value.astimezone()
    return value.strftime("%Y-%m-%d %H:%M")


def extract_room_from_notes(notes: str | None) -> str | None:
    if not notes:
        return None
    match = re.search(r"\broom\s*:\s*([^;]+)", notes, flags=re.IGNORECASE)
    if not match:
        return None
    room = match.group(1).strip()
    return room or None


def truncate(text: str | None, max_len: int) -> str:
    value = str(text or "")
    if max_len <= 0:
        return ""
    if len(value) <= max_len:
        return value
    if max_len <= 1:
        return "…" if supports_unicode() else "."
    suffix = "…" if supports_unicode() else "..."
    if len(suffix) >= max_len:
        return suffix[:max_len]
    return f"{value[: max_len - len(suffix)]}{suffix}"


def compact_duration(minutes: float | int | None, signed: bool = False) -> str:
    if minutes is None:
        return "n/a"
    rounded = int(round(minutes))
    sign = ""
    if signed and rounded > 0:
        sign = "+"
    elif rounded < 0:
        sign = "-"

    absolute = abs(rounded)
    hours, mins = divmod(absolute, 60)
    if hours:
        return f"{sign}{hours}h{mins:02d}m"
    return f"{sign}{mins}m"
