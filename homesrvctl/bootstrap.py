from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

import typer

from homesrvctl import __version__
from homesrvctl.cloudflare import (
    CloudflareApiClient,
    CloudflareApiError,
    account_id_from_cloudflared_config,
    generate_local_tunnel_secret,
)
from homesrvctl.cloudflared import (
    CloudflaredConfigError,
    cloudflared_credentials_path,
    write_bootstrap_cloudflared_config,
)
from homesrvctl.cloudflared_service import detect_cloudflared_runtime
from homesrvctl.config import default_config_path, load_config_details, update_config
from homesrvctl.models import HomesrvctlConfig
from homesrvctl.shell import command_exists, run_command


@dataclass(slots=True)
class BootstrapAssessment:
    ok: bool
    bootstrap_state: str
    bootstrap_ready: bool
    host_supported: bool
    detail: str
    config_path: str
    os: dict[str, object]
    systemd: dict[str, object]
    packages: dict[str, object]
    services: dict[str, object]
    config: dict[str, object]
    network: dict[str, object]
    cloudflare: dict[str, object]
    issues: list[str]
    next_steps: list[str]


@dataclass(slots=True)
class BootstrapTunnelProvisioning:
    ok: bool
    created: bool
    reused: bool
    detail: str
    config_path: str
    account_id: str
    requested_tunnel: str
    tunnel_id: str
    tunnel_name: str
    config_src: str
    status: str
    credentials_path: str
    cloudflared_config_path: str
    config_updated: bool
    credentials_written: bool
    cloudflared_config_written: bool
    next_steps: list[str]


def provision_bootstrap_tunnel(
    config_path: Path | None = None,
    *,
    account_id: str | None = None,
    tunnel_name: str | None = None,
    force: bool = False,
) -> BootstrapTunnelProvisioning:
    target_path = config_path or default_config_path()
    config, _ = load_config_details(target_path)
    resolved_account_id = _resolve_bootstrap_account_id(config, explicit_account_id=account_id)
    requested_tunnel = tunnel_name.strip() if tunnel_name and tunnel_name.strip() else config.tunnel_name.strip()
    if not requested_tunnel:
        raise typer.BadParameter("missing tunnel reference in config; set `tunnel_name` or pass --name")

    client = CloudflareApiClient(config.cloudflare_api_token)
    existing_tunnel = None
    try:
        existing_tunnel = client.get_tunnel(resolved_account_id, requested_tunnel)
    except CloudflareApiError as exc:
        if "not found in account" not in str(exc):
            raise typer.BadParameter(str(exc)) from exc

    credentials_path = config.cloudflared_config.parent / (
        f"{existing_tunnel.id if existing_tunnel is not None else 'pending'}.json"
    )
    credentials_written = False
    cloudflared_config_written = False
    created = existing_tunnel is None
    reused = existing_tunnel is not None

    if existing_tunnel is not None:
        tunnel_id = existing_tunnel.id.lower()
        existing_credentials_path = _existing_tunnel_credentials_path(config, tunnel_id)
        if existing_credentials_path is None:
            raise typer.BadParameter(
                "Cloudflare tunnel already exists in the target account, but local tunnel credentials are not "
                "available from the current config. Choose a new tunnel name or restore the local credentials "
                "before reusing this tunnel."
            )
        credentials_path = existing_credentials_path
        cloudflared_config_written = write_bootstrap_cloudflared_config(
            config.cloudflared_config,
            tunnel_id=tunnel_id,
            credentials_path=credentials_path,
            force=force,
        )
        tunnel_id_value = tunnel_id
        tunnel_name_value = existing_tunnel.name
        config_src = "local"
        tunnel_status = existing_tunnel.status or "inactive"
        detail = f"reused existing Cloudflare tunnel {tunnel_name_value} ({tunnel_id_value})"
    else:
        tunnel_secret = generate_local_tunnel_secret()
        provisioned = client.create_tunnel(
            resolved_account_id,
            requested_tunnel,
            config_src="local",
            tunnel_secret=tunnel_secret,
        )
        tunnel_id_value = provisioned.id.lower()
        tunnel_name_value = provisioned.name
        config_src = provisioned.config_src
        tunnel_status = provisioned.status
        credentials_path = config.cloudflared_config.parent / f"{tunnel_id_value}.json"
        _write_tunnel_credentials(credentials_path, provisioned.credentials_file, force=force)
        credentials_written = True
        cloudflared_config_written = write_bootstrap_cloudflared_config(
            config.cloudflared_config,
            tunnel_id=tunnel_id_value,
            credentials_path=credentials_path,
            force=force,
        )
        detail = f"created Cloudflare tunnel {tunnel_name_value} ({tunnel_id_value})"

    config_updated = config.tunnel_name != tunnel_id_value
    update_config(target_path, tunnel_name=tunnel_id_value)
    next_steps = [
        f"Run `homesrvctl tunnel status --json` to confirm the configured tunnel resolves to {tunnel_id_value}.",
        "Host runtime/service bootstrap is still a later slice; install or wire cloudflared before expecting traffic.",
    ]
    return BootstrapTunnelProvisioning(
        ok=True,
        created=created,
        reused=reused,
        detail=detail,
        config_path=str(target_path),
        account_id=resolved_account_id,
        requested_tunnel=requested_tunnel,
        tunnel_id=tunnel_id_value,
        tunnel_name=tunnel_name_value,
        config_src=config_src,
        status=tunnel_status,
        credentials_path=str(credentials_path),
        cloudflared_config_path=str(config.cloudflared_config),
        config_updated=config_updated,
        credentials_written=credentials_written,
        cloudflared_config_written=cloudflared_config_written,
        next_steps=next_steps,
    )


def assess_bootstrap(config_path: Path | None = None, *, quiet: bool = False) -> BootstrapAssessment:
    target_path = config_path or default_config_path()
    os_info = _os_assessment()
    systemd_info = _systemd_assessment()
    host_supported = bool(os_info["supported"]) and bool(systemd_info["present"])

    config_info, config = _config_assessment(target_path)
    packages_info = _packages_assessment(quiet=quiet)
    services_info = _services_assessment(packages_info=packages_info, quiet=quiet)
    network_info = _network_assessment(config.docker_network, packages_info=packages_info, quiet=quiet)
    cloudflare_info = _cloudflare_assessment(
        api_token=config.cloudflare_api_token,
        token_source=str(config_info["token_source"]),
    )

    issues: list[str] = []
    if not host_supported:
        issues.append("host is not in the first supported bootstrap target: Debian-family Linux with systemd")
    if not packages_info["docker"]:
        issues.append("docker binary is missing")
    if not packages_info["docker_compose"]:
        issues.append("docker compose is unavailable")
    if not packages_info["cloudflared"]:
        issues.append("cloudflared binary is missing")
    if not bool(services_info["traefik_running"]):
        issues.append("Traefik is not running")
    if not bool(services_info["cloudflared_active"]):
        issues.append("cloudflared is not active")
    if network_info["exists"] is False:
        issues.append(f"docker network `{config.docker_network}` is missing")
    if not bool(config_info["exists"]):
        issues.append(f"homesrvctl config is missing: {target_path}")
    elif not bool(config_info["valid"]):
        issues.append(str(config_info["detail"]))
    if not bool(cloudflare_info["token_present"]):
        issues.append("Cloudflare API token is missing")
    elif cloudflare_info["api_reachable"] is False:
        issues.append(str(cloudflare_info["detail"]))

    bootstrap_ready = host_supported and not issues
    if not host_supported:
        bootstrap_state = "unsupported"
    elif (
        not bool(config_info["exists"])
        and not packages_info["docker"]
        and not packages_info["docker_compose"]
        and not packages_info["cloudflared"]
        and not bool(services_info["traefik_running"])
        and not bool(services_info["cloudflared_active"])
    ):
        bootstrap_state = "fresh"
    elif bootstrap_ready:
        bootstrap_state = "ready"
    else:
        bootstrap_state = "partial"

    next_steps = _next_steps(
        bootstrap_state=bootstrap_state,
        host_supported=host_supported,
        config_info=config_info,
        packages_info=packages_info,
        services_info=services_info,
        network_info=network_info,
        cloudflare_info=cloudflare_info,
        docker_network=config.docker_network,
    )

    if bootstrap_state == "ready":
        detail = "host matches the current bootstrap target and appears ready for the next bootstrap slice"
    elif bootstrap_state == "fresh":
        detail = "host looks mostly fresh relative to the current bootstrap target"
    elif bootstrap_state == "unsupported":
        detail = "host is outside the current bootstrap target"
    else:
        detail = "host is partially provisioned relative to the current bootstrap target"

    return BootstrapAssessment(
        ok=host_supported,
        bootstrap_state=bootstrap_state,
        bootstrap_ready=bootstrap_ready,
        host_supported=host_supported,
        detail=detail,
        config_path=str(target_path),
        os=os_info,
        systemd=systemd_info,
        packages=packages_info,
        services=services_info,
        config=config_info,
        network=network_info,
        cloudflare=cloudflare_info,
        issues=issues,
        next_steps=next_steps,
    )


def _os_assessment() -> dict[str, object]:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return {
            "id": "unknown",
            "version_id": "",
            "pretty_name": "unknown",
            "supported": False,
            "detail": "missing /etc/os-release",
        }

    parsed: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"')

    os_id = parsed.get("ID", "unknown").strip().lower()
    id_like = {item.strip().lower() for item in parsed.get("ID_LIKE", "").split() if item.strip()}
    supported = os_id in {"debian", "raspbian"} or "debian" in id_like
    detail = "Debian-family host detected" if supported else f"unsupported OS family: {os_id or 'unknown'}"
    return {
        "id": os_id or "unknown",
        "version_id": parsed.get("VERSION_ID", "").strip(),
        "pretty_name": parsed.get("PRETTY_NAME", "unknown").strip(),
        "supported": supported,
        "detail": detail,
    }


def _systemd_assessment() -> dict[str, object]:
    present = command_exists("systemctl") and Path("/run/systemd/system").exists()
    return {
        "present": present,
        "detail": "systemd detected" if present else "systemd not detected",
    }


def _config_assessment(config_path: Path) -> tuple[dict[str, object], HomesrvctlConfig]:
    env_api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    fallback_config = HomesrvctlConfig(cloudflare_api_token=env_api_token)
    if not config_path.exists():
        return (
            {
                "path": str(config_path),
                "exists": False,
                "valid": False,
                "detail": f"config file not found: {config_path}",
                "docker_network": fallback_config.docker_network,
                "cloudflared_config": str(fallback_config.cloudflared_config),
                "token_present": bool(env_api_token),
                "token_source": "environment" if env_api_token else "missing",
            },
            fallback_config,
        )

    try:
        config, sources = load_config_details(config_path)
    except typer.BadParameter as exc:
        return (
            {
                "path": str(config_path),
                "exists": True,
                "valid": False,
                "detail": str(exc),
                "docker_network": fallback_config.docker_network,
                "cloudflared_config": str(fallback_config.cloudflared_config),
                "token_present": bool(env_api_token),
                "token_source": "environment" if env_api_token else "missing",
            },
            fallback_config,
        )

    return (
        {
            "path": str(config_path),
            "exists": True,
            "valid": True,
            "detail": "config file loaded successfully",
            "docker_network": config.docker_network,
            "cloudflared_config": str(config.cloudflared_config),
            "token_present": bool(config.cloudflare_api_token),
            "token_source": sources["cloudflare_api_token"],
        },
        config,
    )


def _packages_assessment(*, quiet: bool) -> dict[str, object]:
    docker_binary = command_exists("docker")
    cloudflared_binary = command_exists("cloudflared")
    compose_available = False
    compose_detail = "docker binary missing"
    if docker_binary:
        compose_result = run_command(["docker", "compose", "version"], quiet=quiet)
        compose_available = compose_result.ok
        compose_detail = compose_result.stdout or compose_result.stderr or "docker compose unavailable"
    return {
        "docker": docker_binary,
        "docker_detail": "found in PATH" if docker_binary else "missing from PATH",
        "docker_compose": compose_available,
        "docker_compose_detail": compose_detail,
        "cloudflared": cloudflared_binary,
        "cloudflared_detail": "found in PATH" if cloudflared_binary else "missing from PATH",
    }


def _services_assessment(*, packages_info: dict[str, object], quiet: bool) -> dict[str, object]:
    traefik_running = False
    traefik_detail = "docker binary missing"
    if packages_info["docker"]:
        traefik_result = run_command(
            ["docker", "ps", "--filter", "name=traefik", "--filter", "status=running", "--format", "{{.Names}}"],
            quiet=quiet,
        )
        traefik_running = bool(traefik_result.stdout.strip())
        traefik_detail = (
            traefik_result.stdout
            or traefik_result.stderr
            or "no running container matched filter name=traefik"
        )

    runtime = detect_cloudflared_runtime(quiet=quiet)
    return {
        "traefik_running": traefik_running,
        "traefik_detail": traefik_detail,
        "cloudflared_active": runtime.active,
        "cloudflared_mode": runtime.mode,
        "cloudflared_detail": runtime.detail,
    }


def _network_assessment(docker_network: str, *, packages_info: dict[str, object], quiet: bool) -> dict[str, object]:
    if not packages_info["docker"]:
        return {
            "name": docker_network,
            "exists": None,
            "detail": "docker binary missing",
        }
    result = run_command(
        ["docker", "network", "inspect", docker_network, "--format", "{{json .Name}}"],
        quiet=quiet,
    )
    return {
        "name": docker_network,
        "exists": result.ok,
        "detail": result.stdout or result.stderr or "docker network not found",
    }


def _cloudflare_assessment(*, api_token: str, token_source: str) -> dict[str, object]:
    token = api_token.strip()
    if not token:
        return {
            "token_present": False,
            "token_source": token_source,
            "api_reachable": None,
            "detail": "Cloudflare API token is not configured",
        }

    try:
        request = urllib.request.Request(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": f"homesrvctl/{__version__}",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "token_present": True,
            "token_source": token_source,
            "api_reachable": False,
            "detail": f"Cloudflare token verification failed: HTTP {exc.code}: {detail}",
        }
    except urllib.error.URLError as exc:
        return {
            "token_present": True,
            "token_source": token_source,
            "api_reachable": False,
            "detail": f"Cloudflare API verification failed: {exc}",
        }

    if payload.get("success", False):
        status = payload.get("result", {})
        token_status = status.get("status", "unknown") if isinstance(status, dict) else "unknown"
        return {
            "token_present": True,
            "token_source": token_source,
            "api_reachable": True,
            "detail": f"Cloudflare token verified ({token_status})",
        }
    return {
        "token_present": True,
        "token_source": token_source,
        "api_reachable": False,
        "detail": f"Cloudflare token verification failed: {payload.get('errors', [])}",
    }


def _next_steps(
    *,
    bootstrap_state: str,
    host_supported: bool,
    config_info: dict[str, object],
    packages_info: dict[str, object],
    services_info: dict[str, object],
    network_info: dict[str, object],
    cloudflare_info: dict[str, object],
    docker_network: str,
) -> list[str]:
    if bootstrap_state == "ready":
        return ["Host baseline is ready for the next bootstrap slice."]

    steps: list[str] = []
    if not host_supported:
        steps.append("Use a Debian-family Raspberry Pi OS host with systemd for the first bootstrap target.")
    if not packages_info["docker"] or not packages_info["docker_compose"]:
        steps.append("Install Docker Engine plus the Docker Compose plugin.")
    if not packages_info["cloudflared"]:
        steps.append("Install cloudflared on the host.")
    if config_info["exists"] is False:
        steps.append("Run `homesrvctl config init` to create the starter config.")
    if not services_info["traefik_running"]:
        steps.append("Install or start the baseline Traefik runtime expected by homesrvctl.")
    if not services_info["cloudflared_active"]:
        steps.append("Install or start the cloudflared service for the shared host tunnel.")
    if network_info["exists"] is False:
        steps.append(f"Create the shared Docker network `{docker_network}`.")
    if not cloudflare_info["token_present"]:
        steps.append("Configure `cloudflare_api_token` or `CLOUDFLARE_API_TOKEN` for Cloudflare API access.")
    elif cloudflare_info["api_reachable"] is False:
        steps.append("Fix Cloudflare API token reachability before attempting future bootstrap flows.")
    elif config_info["exists"] and config_info["valid"]:
        steps.append(
            "Use `homesrvctl bootstrap tunnel --account-id <cloudflare-account-id>` to create or reuse the shared host tunnel."
        )
    steps.append("`homesrvctl bootstrap apply` is not shipped yet; use this assessment to prepare the host manually.")
    return steps


def _resolve_bootstrap_account_id(config: HomesrvctlConfig, *, explicit_account_id: str | None) -> str:
    if explicit_account_id and explicit_account_id.strip():
        return explicit_account_id.strip()
    try:
        return account_id_from_cloudflared_config(config.cloudflared_config)
    except CloudflareApiError as exc:
        raise typer.BadParameter(
            "missing Cloudflare account ID for tunnel provisioning. Pass --account-id or configure a readable "
            f"cloudflared credentials file first: {exc}"
        ) from exc


def _write_tunnel_credentials(credentials_path: Path, payload: dict[str, object], *, force: bool) -> bool:
    rendered = json.dumps(payload, indent=2) + "\n"
    existing = credentials_path.read_text(encoding="utf-8") if credentials_path.exists() else None
    if existing is not None:
        if existing == rendered:
            return False
        if not force:
            raise typer.BadParameter(
                f"tunnel credentials already exist at {credentials_path}; use --force to overwrite bootstrap material"
            )
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        credentials_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"unable to write tunnel credentials {credentials_path}: {exc}") from exc
    return True


def _existing_tunnel_credentials_path(config: HomesrvctlConfig, tunnel_id: str) -> Path | None:
    try:
        credentials_path = cloudflared_credentials_path(config.cloudflared_config)
    except (CloudflaredConfigError, typer.BadParameter):
        return None
    if not credentials_path.exists():
        return None
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(payload.get("TunnelID", "")).strip().lower() != tunnel_id.lower():
        return None
    return credentials_path
