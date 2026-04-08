from __future__ import annotations

import curses
import time

from homesrvctl.tui.data import build_dashboard_snapshot


def run_dashboard(initial_snapshot: dict[str, object], refresh_seconds: float) -> None:
    curses.wrapper(_run_dashboard, initial_snapshot, refresh_seconds)


def render_dashboard(snapshot: dict[str, object], width: int = 100) -> str:
    lines: list[str] = []
    lines.append("homesrvctl dashboard")
    lines.append("q quit | r refresh")
    lines.append("")
    lines.extend(_render_stack_section(snapshot.get("list"), width))
    lines.append("")
    lines.extend(_render_cloudflared_section(snapshot.get("cloudflared"), width))
    lines.append("")
    lines.extend(_render_validate_section(snapshot.get("validate"), width))
    lines.append("")
    lines.append(f"generated: {snapshot.get('generated_at', 'unknown')}")
    return "\n".join(lines)


def _run_dashboard(stdscr, snapshot: dict[str, object], refresh_seconds: float) -> None:  # noqa: ANN001
    curses.curs_set(0)
    stdscr.nodelay(refresh_seconds > 0)
    current = snapshot
    next_refresh = time.monotonic() + refresh_seconds if refresh_seconds > 0 else None

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        rendered = render_dashboard(current, width=max(width - 1, 40))
        for row, line in enumerate(rendered.splitlines()):
            if row >= height - 1:
                break
            stdscr.addnstr(row, 0, line, max(width - 1, 1))
        stdscr.refresh()

        timeout_ms = -1
        if refresh_seconds > 0 and next_refresh is not None:
            remaining = max(next_refresh - time.monotonic(), 0.0)
            timeout_ms = int(remaining * 1000)
        stdscr.timeout(timeout_ms)
        key = stdscr.getch()

        if key in {ord("q"), ord("Q")}:
            return
        if key in {ord("r"), ord("R")}:
            current = build_dashboard_snapshot()
            if refresh_seconds > 0:
                next_refresh = time.monotonic() + refresh_seconds
            continue
        if key == -1 and refresh_seconds > 0:
            current = build_dashboard_snapshot()
            next_refresh = time.monotonic() + refresh_seconds


def _render_stack_section(payload, width: int) -> list[str]:  # noqa: ANN001
    heading = _heading("Stacks", width)
    if not isinstance(payload, dict):
        return [heading, "unavailable"]
    if not payload.get("ok"):
        return [heading, f"error: {payload.get('error', 'unknown error')}"]

    sites = payload.get("sites", [])
    if not sites:
        return [heading, "no stacks found"]

    compose_ready = sum(1 for site in sites if site.get("compose"))
    lines = [heading, f"{len(sites)} stack(s), {compose_ready} with docker-compose.yml"]
    for site in sites[:8]:
        status = "compose=yes" if site.get("compose") else "compose=no"
        lines.append(f"- {site.get('hostname', '<unknown>')} [{status}]")
    if len(sites) > 8:
        lines.append(f"... {len(sites) - 8} more")
    return lines


def _render_cloudflared_section(payload, width: int) -> list[str]:  # noqa: ANN001
    heading = _heading("Cloudflared", width)
    if not isinstance(payload, dict):
        return [heading, "unavailable"]

    lines = [
        heading,
        f"runtime: {payload.get('mode', 'unknown')} ({'active' if payload.get('active') else 'inactive'})",
        f"detail: {payload.get('detail', 'unknown')}",
    ]

    config_validation = payload.get("config_validation")
    if isinstance(config_validation, dict):
        status = "ok" if config_validation.get("ok") else "error"
        lines.append(f"config: {status} - {config_validation.get('detail', 'unknown')}")
        warnings = config_validation.get("warnings", [])
        if warnings:
            lines.append(f"warnings: {len(warnings)}")
            for warning in warnings[:3]:
                lines.append(f"- {warning}")
            if len(warnings) > 3:
                lines.append(f"... {len(warnings) - 3} more")
    return lines


def _render_validate_section(payload, width: int) -> list[str]:  # noqa: ANN001
    heading = _heading("Validate", width)
    if not isinstance(payload, dict):
        return [heading, "unavailable"]
    if not payload.get("ok") and "checks" not in payload:
        return [heading, f"error: {payload.get('error', 'unknown error')}"]

    checks = payload.get("checks", [])
    failures = [check for check in checks if not check.get("ok")]
    lines = [heading, f"{len(checks)} check(s), {len(failures)} failing"]
    for check in failures[:5]:
        lines.append(f"- {check.get('name', '<unknown>')}: {check.get('detail', '')}")
    if not failures:
        lines.append("all validation checks passing")
    elif len(failures) > 5:
        lines.append(f"... {len(failures) - 5} more")
    return lines


def _heading(label: str, width: int) -> str:
    rule = "=" * max(min(width - len(label) - 3, 40), 8)
    return f"{label} {rule}"
