from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ICSParseError(ValueError):
    pass


def _unfold_lines(text: str) -> list[str]:
    """RFC 5545 line unfolding: a line starting with space/tab continues the
    previous one."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return [line for line in lines if line]


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    """Split ``NAME;PARAM=X:value`` into (name, params, value)."""
    head, separator, value = line.partition(":")
    if not separator:
        return None
    parts = head.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for param in parts[1:]:
        key, _, param_value = param.partition("=")
        params[key.upper()] = param_value.strip('"')
    return name, params, value


def _parse_dtstart(params: dict[str, str], value: str, event_label: str) -> datetime:
    if params.get("VALUE") == "DATE":
        raise ICSParseError(
            f"{event_label}: all-day events have no exam time; give the event "
            "a start time in the calendar."
        )
    raw = value.strip()
    if raw.endswith("Z"):
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    tzid = params.get("TZID")
    if not tzid:
        raise ICSParseError(
            f"{event_label}: DTSTART has no timezone (no TZID and no Z suffix); "
            "export the calendar with timezone information."
        )
    try:
        return parsed.replace(tzinfo=ZoneInfo(tzid))
    except ZoneInfoNotFoundError as exc:
        raise ICSParseError(f"{event_label}: unknown timezone '{tzid}'.") from exc


def parse_ics_events(text: str) -> list[dict[str, object]]:
    """Parse VEVENTs into exam dicts: {course, exam_at, notes}.

    SUMMARY becomes the course name; LOCATION and DESCRIPTION are folded into
    notes. Events without a start time (all-day) or timezone raise, because a
    wrong exam time silently corrupts every downstream night match.
    """
    events: list[dict[str, object]] = []
    current: dict[str, str] | None = None
    current_params: dict[str, dict[str, str]] = {}

    for line in _unfold_lines(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            current_params = {}
            continue
        if upper == "END:VEVENT":
            if current is None:
                raise ICSParseError("END:VEVENT without a matching BEGIN:VEVENT.")
            events.append(_build_event(current, current_params, len(events) + 1))
            current = None
            continue
        if current is None:
            continue
        parsed = _split_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name in {"SUMMARY", "DTSTART", "LOCATION", "DESCRIPTION"}:
            current[name] = value
            current_params[name] = params

    if current is not None:
        raise ICSParseError("Unterminated VEVENT (missing END:VEVENT).")
    return events


def _build_event(
    fields: dict[str, str],
    params: dict[str, dict[str, str]],
    index: int,
) -> dict[str, object]:
    label = f"Event #{index}"
    summary = _unescape(fields.get("SUMMARY", "")).strip()
    if not summary:
        raise ICSParseError(f"{label} has no SUMMARY to use as the course name.")
    if "DTSTART" not in fields:
        raise ICSParseError(f"{label} ('{summary}') has no DTSTART.")
    exam_at = _parse_dtstart(
        params.get("DTSTART", {}), fields["DTSTART"], f"{label} ('{summary}')"
    )

    note_parts = []
    location = _unescape(fields.get("LOCATION", "")).strip()
    if location:
        note_parts.append(location)
    description = _unescape(fields.get("DESCRIPTION", "")).strip()
    if description:
        note_parts.append(description)

    return {
        "course": summary,
        "exam_at": exam_at,
        "notes": "; ".join(note_parts),
    }
