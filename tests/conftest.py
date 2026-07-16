from __future__ import annotations

from rich.console import Console

from app.cli.views import common, report_view, today_view, whoop_views

_CONSOLE_MODULES = (common, report_view, today_view, whoop_views)


def patch_console(monkeypatch, *, width: int = 100) -> Console:
    """Route every view module's console to one recording console."""
    test_console = Console(record=True, width=width, color_system=None)
    for module in _CONSOLE_MODULES:
        monkeypatch.setattr(module, "console", test_console, raising=False)
    return test_console
