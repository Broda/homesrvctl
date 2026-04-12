from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import shlex

from homesrvctl.shell import run_command


@dataclass(slots=True)
class CloudflaredRuntime:
    mode: str
    active: bool
    detail: str
    restart_command: list[str] | None = None
    reload_command: list[str] | None = None
    logs_command: list[str] | None = None


@dataclass(slots=True)
class CloudflaredSystemdUnit:
    present: bool
    exec_start: str | None
    config_path: str | None
    user: str | None
    group: str | None


@dataclass(slots=True)
class CloudflaredSetupReport:
    ok: bool
    mode: str
    systemd_managed: bool
    active: bool
    configured_path: str
    configured_exists: bool
    configured_writable: bool
    runtime_path: str | None
    runtime_exists: bool | None
    runtime_readable: bool | None
    paths_aligned: bool | None
    ingress_mutation_available: bool
    detail: str
    issues: list[str]
    next_commands: list[str]
    override_path: str | None = None
    override_content: str | None = None
    notes: list[str] | None = None


class CloudflaredServiceError(RuntimeError):
    pass


def detect_cloudflared_runtime(*, quiet: bool = False) -> CloudflaredRuntime:
    systemctl = run_command(["systemctl", "is-active", "cloudflared"], quiet=quiet)
    if systemctl.ok and systemctl.stdout == "active":
        can_reload = run_command(
            ["systemctl", "show", "cloudflared", "--property", "CanReload", "--value"],
            quiet=quiet,
        )
        reload_command = ["systemctl", "reload", "cloudflared"] if can_reload.ok and can_reload.stdout.lower() == "yes" else None
        return CloudflaredRuntime(
            mode="systemd",
            active=True,
            detail="systemd service is active",
            restart_command=["systemctl", "restart", "cloudflared"],
            reload_command=reload_command,
            logs_command=["journalctl", "-u", "cloudflared", "-n", "100", "--no-pager"],
        )

    docker_ps = run_command(
        ["docker", "ps", "--filter", "name=cloudflared", "--filter", "status=running", "--format", "{{.Names}}"],
        quiet=quiet,
    )
    container_names = [line.strip() for line in docker_ps.stdout.splitlines() if line.strip()]
    if container_names:
        container_name = container_names[0]
        detail = f"running container(s): {', '.join(container_names)}"
        return CloudflaredRuntime(
            mode="docker",
            active=True,
            detail=detail,
            restart_command=["docker", "restart", container_name],
            reload_command=None,
            logs_command=["docker", "logs", "--tail", "100", container_name],
        )

    pgrep = run_command(["pgrep", "-fa", "cloudflared"], quiet=quiet)
    if pgrep.ok and pgrep.stdout.strip():
        return CloudflaredRuntime(
            mode="process",
            active=True,
            detail=f"process present: {pgrep.stdout}",
            restart_command=None,
            reload_command=None,
            logs_command=None,
        )

    detail = systemctl.stderr or systemctl.stdout or "cloudflared not detected via systemd, docker, or process scan"
    return CloudflaredRuntime(mode="absent", active=False, detail=detail, restart_command=None, reload_command=None, logs_command=None)


def inspect_cloudflared_setup(config_path: Path, *, runtime: CloudflaredRuntime | None = None, quiet: bool = False) -> CloudflaredSetupReport:
    resolved_runtime = runtime or detect_cloudflared_runtime(quiet=quiet)
    unit = inspect_cloudflared_systemd_unit(quiet=quiet)
    configured_exists = config_path.exists()
    configured_writable = _path_is_writable(config_path)
    runtime_path = unit.config_path if unit.present else None
    runtime_exists = Path(runtime_path).exists() if runtime_path else None
    runtime_readable = _path_is_readable(Path(runtime_path)) if runtime_path else None
    paths_aligned = str(config_path) == runtime_path if runtime_path else None

    issues: list[str] = []
    notes: list[str] = []
    next_commands: list[str] = []
    override_path = "/etc/systemd/system/cloudflared.service.d/override.conf" if unit.present else None
    override_content = _systemd_override_content(config_path) if unit.present else None
    current_user = getpass.getuser()

    if not configured_exists:
        issues.append(f"configured cloudflared config is missing: {config_path}")
    if not configured_writable:
        issues.append(f"configured cloudflared config is not writable by the current user: {config_path}")
    if unit.present and runtime_path and not paths_aligned:
        issues.append(
            f"systemd cloudflared service uses {runtime_path}, but homesrvctl is configured for {config_path}"
        )
    if unit.present and runtime_path and runtime_exists is False:
        issues.append(f"systemd cloudflared config path is missing: {runtime_path}")
    if unit.present and runtime_path and runtime_exists and runtime_readable is False:
        issues.append(f"systemd cloudflared config path is not readable by the current user: {runtime_path}")

    if resolved_runtime.mode in {"docker", "process"}:
        notes.append(
            f"{resolved_runtime.mode} runtime detected; automatic setup repair is only modeled for systemd in this slice"
        )
    if resolved_runtime.mode == "absent" and not unit.present:
        notes.append("cloudflared runtime not detected; setup guidance is based on the configured path only")

    ingress_mutation_available = configured_exists and configured_writable and (paths_aligned is not False)

    if unit.present and runtime_path and not paths_aligned:
        next_commands.extend(
            [
                f"sudo install -d -o {current_user} -g {current_user} -m 755 {config_path.parent}",
                *(
                    [f"sudo cp {shlex.quote(runtime_path)} {shlex.quote(str(config_path))}"]
                    if runtime_exists
                    else []
                ),
                f"sudo chown {current_user}:{current_user} {shlex.quote(str(config_path))}",
                f"sudo install -d -m 755 /etc/systemd/system/cloudflared.service.d",
                f"sudo tee {override_path} >/dev/null <<'EOF'\n{override_content}\nEOF",
                "sudo systemctl daemon-reload",
                "sudo systemctl restart cloudflared",
            ]
        )
    elif not configured_exists:
        next_commands.extend(
            [
                f"sudo install -d -o {current_user} -g {current_user} -m 755 {config_path.parent}",
                (
                    f"sudo cp {shlex.quote(runtime_path)} {shlex.quote(str(config_path))}"
                    if runtime_path and runtime_exists
                    else f"sudoedit {shlex.quote(str(config_path))}"
                ),
                f"sudo chown {current_user}:{current_user} {shlex.quote(str(config_path))}",
            ]
        )
    elif not configured_writable:
        next_commands.extend(
            [
                f"sudo chown {current_user}:{current_user} {shlex.quote(str(config_path))}",
                f"sudo chmod 644 {shlex.quote(str(config_path))}",
            ]
        )

    if not issues:
        detail = f"configured cloudflared path is ready for homesrvctl mutations: {config_path}"
    else:
        detail = issues[0]

    return CloudflaredSetupReport(
        ok=not issues,
        mode=resolved_runtime.mode,
        systemd_managed=unit.present,
        active=resolved_runtime.active,
        configured_path=str(config_path),
        configured_exists=configured_exists,
        configured_writable=configured_writable,
        runtime_path=runtime_path,
        runtime_exists=runtime_exists,
        runtime_readable=runtime_readable,
        paths_aligned=paths_aligned,
        ingress_mutation_available=ingress_mutation_available,
        detail=detail,
        issues=issues,
        next_commands=next_commands,
        override_path=override_path,
        override_content=override_content,
        notes=notes,
    )


def inspect_cloudflared_systemd_unit(*, quiet: bool = False) -> CloudflaredSystemdUnit:
    result = run_command(
        ["systemctl", "show", "cloudflared", "--property", "ExecStart", "--property", "User", "--property", "Group"],
        quiet=quiet,
    )
    if not result.ok:
        return CloudflaredSystemdUnit(present=False, exec_start=None, config_path=None, user=None, group=None)

    lines = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        lines[key.strip()] = value.strip()

    exec_start = lines.get("ExecStart")
    if not exec_start:
        return CloudflaredSystemdUnit(present=False, exec_start=None, config_path=None, user=None, group=None)

    return CloudflaredSystemdUnit(
        present=True,
        exec_start=exec_start,
        config_path=_config_path_from_exec_start(exec_start),
        user=lines.get("User") or None,
        group=lines.get("Group") or None,
    )


def restart_cloudflared_service() -> CloudflaredRuntime:
    runtime = detect_cloudflared_runtime()
    if not runtime.active:
        raise CloudflaredServiceError(runtime.detail)
    if runtime.restart_command is None:
        raise CloudflaredServiceError(f"{runtime.detail}; restart cloudflared manually")

    result = run_command(runtime.restart_command)
    if not result.ok:
        detail = result.stderr or result.stdout or "command failed"
        raise CloudflaredServiceError(f"{runtime.mode} restart failed: {detail}")
    return runtime


def reload_cloudflared_service() -> CloudflaredRuntime:
    runtime = detect_cloudflared_runtime()
    if not runtime.active:
        raise CloudflaredServiceError(runtime.detail)
    if runtime.reload_command is None:
        raise CloudflaredServiceError(f"{runtime.detail}; reload is not supported for this runtime")

    result = run_command(runtime.reload_command)
    if not result.ok:
        detail = result.stderr or result.stdout or "command failed"
        raise CloudflaredServiceError(f"{runtime.mode} reload failed: {detail}")
    return runtime


def _config_path_from_exec_start(exec_start: str) -> str | None:
    marker = "argv[]="
    if marker not in exec_start:
        return None
    argv = exec_start.split(marker, 1)[1].split(" ;", 1)[0].strip()
    parts = shlex.split(argv)
    for index, part in enumerate(parts):
        if part == "--config" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _path_is_readable(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except OSError:
        return False
    return True


def _path_is_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    target = path.parent
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return target.exists() and os.access(target, os.W_OK)


def _systemd_override_content(config_path: Path) -> str:
    return "\n".join(
        [
            "[Service]",
            "ExecStart=",
            f"ExecStart=/usr/bin/cloudflared --no-autoupdate --config {config_path} tunnel run",
        ]
    )
