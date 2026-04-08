from __future__ import annotations

import json
import sys
import time

from homesrvctl.shell import run_command


def build_dashboard_snapshot(run_json_command=None) -> dict[str, object]:  # noqa: ANN001
    if run_json_command is None:
        run_json_command = run_json_subcommand
    list_payload = run_json_command(["list"])
    cloudflared_payload = run_json_command(["cloudflared", "status"])
    validate_payload = run_json_command(["validate"])
    return {
        "list": list_payload,
        "cloudflared": cloudflared_payload,
        "validate": validate_payload,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_json_subcommand(args: list[str]) -> dict[str, object]:
    command = [sys.executable, "-m", "homesrvctl.main", *args, "--json"]
    result = run_command(command, quiet=True)
    if not result.ok:
        return {
            "ok": False,
            "error": result.stderr or result.stdout or "command failed",
            "command": command,
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid JSON output",
            "stdout": result.stdout,
            "command": command,
        }
    payload.setdefault("ok", result.ok)
    payload["command"] = command
    return payload
