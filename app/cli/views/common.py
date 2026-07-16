from __future__ import annotations

import math
from datetime import datetime

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from app.utils.terminal_ui import (
    format_zscore,
    horizontal_rule,
    supports_unicode,
    value_color,
    zscore_color,
)
from app.utils.time import to_utc

console = Console()

# Decorative glyphs and box styles degrade gracefully on non-UTF terminals.
UNICODE = supports_unicode()
DOT = "·" if UNICODE else "-"
DELTA = "Δ" if UNICODE else "d"
PLUSMINUS = "±" if UNICODE else "+/-"
TABLE_BOX = box.SIMPLE_HEAVY if UNICODE else box.ASCII
SIMPLE_BOX = box.SIMPLE if UNICODE else box.ASCII

ZONE_COLORS = ("green", "cyan", "yellow", "magenta", "red")


def section(title: str, width: int = 60) -> None:
    console.print(f"\n[bold cyan]{escape(title)}[/bold cyan]")
    console.print(f"[dim]{horizontal_rule(width)}[/dim]")


def section_sub(title: str, subtitle: str, width: int = 78) -> None:
    console.print()
    console.print(Text.assemble((title, "bold cyan"), ("   " + subtitle, "dim")))
    console.print(f"[dim]{horizontal_rule(width)}[/dim]")


def ascii_safe_table(table: Table) -> Table:
    """Avoid Rich's unicode ellipsis when overflowing on non-UTF terminals."""
    if not UNICODE:
        for column in table.columns:
            column.overflow = "crop"
    return table


def remaining_text(exam_at: datetime, now: datetime) -> str:
    seconds = max(0, int((to_utc(exam_at) - now).total_seconds()))
    hours_total = (seconds + 3599) // 3600
    days, hours = divmod(hours_total, 24)
    return f"{days}d {hours}h"


def awake_color(hours: float | None) -> str:
    if hours is None:
        return "dim"
    if hours >= 18:
        return "red"
    if hours >= 13:
        return "yellow"
    return "green"


def delta_color(value: float | None, *, good_high: bool) -> str:
    if value is None:
        return "dim"
    if good_high:
        return "green" if value >= 0 else ("yellow" if value > -15 else "red")
    return "green" if value <= 0 else ("yellow" if value < 5 else "red")


def sleep_delta_color(value: float | int | None) -> str:
    if value is None:
        return "dim"
    if value <= -120:
        return "red"
    if value < 0:
        return "yellow"
    return "green"


def stress_color(label: str) -> str:
    normalized = label.casefold()
    if normalized == "low stress":
        return "green"
    if normalized in {"mild stress", "elevated stress"}:
        return "yellow"
    if normalized == "high stress":
        return "red"
    return "dim"


def baseline_cell(mean_text: str, std: float | None, unit: str = "") -> str:
    if std is None:
        return f"[dim]{mean_text}[/dim]"
    return f"[dim]{mean_text} {PLUSMINUS}{std:.0f}{unit}[/dim]"


def zscore_cell(value: float | None) -> str:
    return f"[{zscore_color(value)}]{format_zscore(value)}[/]"


def percentile_text(value: float | None) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    color = value_color(value)
    return f"[{color}]p{value:.0f}[/]"


def delta_text(value: float | None, unit: str, *, good_high: bool) -> Text:
    if value is None:
        return Text("n/a", style="dim")
    return Text(f"{value:+.0f}{unit}", style=delta_color(value, good_high=good_high))


def hr_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f} bpm"


def bpm_delta_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0f} bpm"


def float_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def percent_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}%"


def downsample(series: list[float], target: int = 40) -> list[float]:
    """Average a long series down to at most ``target`` points for a sparkline."""
    if len(series) <= target:
        return series
    bucket = math.ceil(len(series) / target)
    return [
        sum(series[index : index + bucket]) / len(series[index : index + bucket])
        for index in range(0, len(series), bucket)
    ]


def zone_bar(zone_percent: list[float], width: int = 20) -> str:
    """Render the HR-zone distribution as a colored stacked bar."""
    if not zone_percent or sum(zone_percent) <= 0:
        return "[dim]n/a[/dim]"
    fill = "█" if UNICODE else "#"
    total = sum(zone_percent)
    segments = []
    used = 0
    for index, value in enumerate(zone_percent):
        cells = int(round((value / total) * width))
        segments.append(f"[{ZONE_COLORS[index]}]{fill * cells}[/]")
        used += cells
    if used < width:
        segments.append(fill * (width - used))
    return "".join(segments)


def driver_display_name(name: str) -> str:
    return {
        "sleep_debt": "sleep debt",
        "recovery_drop": "recovery drop",
        "hrv_pressure": "HRV pressure",
        "rhr_elevation": "RHR elevation",
        "strain_load": "strain load",
    }.get(name, name.replace("_", " "))
