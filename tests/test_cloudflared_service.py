from __future__ import annotations

from homesrvctl import cloudflared_service
from homesrvctl.cloudflared_service import CloudflaredServiceError
from homesrvctl.shell import CommandResult


def test_detect_cloudflared_runtime_prefers_systemd(monkeypatch) -> None:
    def fake_run_command(command: list[str], cwd=None, dry_run: bool = False):  # noqa: ANN001, ANN202
        if command[:2] == ["systemctl", "is-active"]:
            return CommandResult(command, 0, "active", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(cloudflared_service, "run_command", fake_run_command)

    runtime = cloudflared_service.detect_cloudflared_runtime()

    assert runtime.mode == "systemd"
    assert runtime.active
    assert runtime.restart_command == ["systemctl", "restart", "cloudflared"]


def test_detect_cloudflared_runtime_uses_docker_when_systemd_inactive(monkeypatch) -> None:
    def fake_run_command(command: list[str], cwd=None, dry_run: bool = False):  # noqa: ANN001, ANN202
        if command[:2] == ["systemctl", "is-active"]:
            return CommandResult(command, 3, "inactive", "")
        if command[:2] == ["docker", "ps"]:
            return CommandResult(command, 0, "cloudflared\ncloudflared-sidecar", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(cloudflared_service, "run_command", fake_run_command)

    runtime = cloudflared_service.detect_cloudflared_runtime()

    assert runtime.mode == "docker"
    assert runtime.active
    assert runtime.restart_command == ["docker", "restart", "cloudflared"]


def test_restart_cloudflared_service_errors_for_unmanaged_process(monkeypatch) -> None:
    monkeypatch.setattr(
        cloudflared_service,
        "detect_cloudflared_runtime",
        lambda: cloudflared_service.CloudflaredRuntime(
            mode="process",
            active=True,
            detail="process present: 123 cloudflared",
            restart_command=None,
        ),
    )

    try:
        cloudflared_service.restart_cloudflared_service()
    except CloudflaredServiceError as exc:
        assert "restart cloudflared manually" in str(exc)
    else:
        raise AssertionError("expected CloudflaredServiceError")
