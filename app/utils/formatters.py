from __future__ import annotations


def minutes_to_hm(minutes: float | int | None, signed: bool = False) -> str:
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
        return f"{sign}{hours}h {mins:02d}m"
    return f"{sign}{mins}m"


def format_percent(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}%"


def format_percent_delta(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0f}%"


def format_bpm_delta(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0f} bpm"


def format_float(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"
