from __future__ import annotations

import sys

import typer

from homesrvctl.tui.dashboard import run_dashboard
from homesrvctl.tui.data import build_dashboard_snapshot


def tui(
    refresh_seconds: float = typer.Option(
        0.0,
        "--refresh-seconds",
        min=0.0,
        help="Automatically refresh the dashboard every N seconds. Use 0 to refresh manually with r.",
    ),
) -> None:
    """Launch a read-only terminal dashboard backed by existing JSON commands."""
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        raise typer.BadParameter("tui requires an interactive terminal")

    snapshot = build_dashboard_snapshot()
    run_dashboard(snapshot, refresh_seconds)
