from __future__ import annotations

import re
import sys
from collections.abc import Sequence
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


def value_color(
    value: float | int | None,
    max_value: float = 100,
    good_high: bool = True,
    low: float = 0.40,
    high: float = 0.70,
) -> str:
    """Traffic-light color for a value, scaled against ``max_value``.

    ``good_high`` flips the scale so that high readings (e.g. stress load)
    read as red instead of green.
    """
    if value is None or max_value <= 0:
        return "dim"
    ratio = max(0.0, min(float(value) / float(max_value), 1.0))
    if not good_high:
        ratio = 1.0 - ratio
    if ratio >= high:
        return "green"
    if ratio >= low:
        return "yellow"
    return "red"


def colored_bar(
    value: float | int | None,
    max_value: float = 100,
    width: int = 20,
    good_high: bool = True,
) -> str:
    """A ``make_bar`` wrapped in Rich color markup based on the value."""
    bar = make_bar(value, max_value=max_value, width=width)
    color = value_color(value, max_value=max_value, good_high=good_high)
    return f"[{color}]{bar}[/{color}]"


_SPARK_TICKS_UNICODE = "▁▂▃▄▅▆▇█"
_SPARK_TICKS_ASCII = "_.-=+*#"


def sparkline(
    values: Sequence[float | int | None],
    unicode: bool | None = None,
) -> str:
    """Render a numeric series as a one-line sparkline.

    Values are min/max normalized across the series. ``None`` entries are
    dropped. Returns an empty string when there is nothing to plot.
    """
    use_unicode = supports_unicode() if unicode is None else unicode
    ticks = _SPARK_TICKS_UNICODE if use_unicode else _SPARK_TICKS_ASCII
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return ""
    low = min(usable)
    high = max(usable)
    span = high - low
    out = []
    for value in usable:
        if span <= 0:
            index = len(ticks) // 2
        else:
            index = int(round(((value - low) / span) * (len(ticks) - 1)))
        out.append(ticks[max(0, min(index, len(ticks) - 1))])
    return "".join(out)


def format_zscore(value: float | None) -> str:
    """Format a z-score with a sigma marker, e.g. ``-2.1σ``."""
    if value is None:
        return "n/a"
    sigma = "σ" if supports_unicode() else "sd"
    return f"{value:+.1f}{sigma}"


def zscore_color(value: float | None, threshold: float = 1.5) -> str:
    """Color a z-score by how many standard deviations it sits from baseline."""
    if value is None:
        return "dim"
    magnitude = abs(value)
    if magnitude >= threshold * 1.5:
        return "red"
    if magnitude >= threshold:
        return "yellow"
    return "green"


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
