from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer
import yaml

from homectl.models import HomectlConfig


DEFAULT_CONFIG = HomectlConfig()


def default_config_path() -> Path:
    return Path.home() / ".config" / "homectl" / "config.yml"


def default_config_data() -> dict[str, Any]:
    return {
        "tunnel_name": DEFAULT_CONFIG.tunnel_name,
        "sites_root": str(DEFAULT_CONFIG.sites_root),
        "docker_network": DEFAULT_CONFIG.docker_network,
        "traefik_url": DEFAULT_CONFIG.traefik_url,
        "cloudflared_config": str(DEFAULT_CONFIG.cloudflared_config),
        "cloudflare_api_token": DEFAULT_CONFIG.cloudflare_api_token,
    }


def load_config(path: Path | None = None) -> HomectlConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        raise typer.BadParameter(
            f"config file not found: {config_path}. Run `homectl config init` first."
        )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    merged = {**default_config_data(), **data}
    api_token = str(merged["cloudflare_api_token"]).strip() or os.environ.get("CLOUDFLARE_API_TOKEN", "")
    return HomectlConfig(
        tunnel_name=str(merged["tunnel_name"]),
        sites_root=Path(str(merged["sites_root"])),
        docker_network=str(merged["docker_network"]),
        traefik_url=str(merged["traefik_url"]),
        cloudflared_config=Path(str(merged["cloudflared_config"])),
        cloudflare_api_token=api_token,
    )


def init_config(path: Path | None = None, force: bool = False) -> Path:
    config_path = path or default_config_path()
    if config_path.exists() and not force:
        raise typer.BadParameter(
            f"config already exists: {config_path}. Use --force to overwrite."
        )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(default_config_data(), sort_keys=False),
        encoding="utf-8",
    )
    return config_path
