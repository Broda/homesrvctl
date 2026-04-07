from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class HomectlConfig:
    tunnel_name: str = "homectl-tunnel"
    sites_root: Path = Path("/srv/homectl/sites")
    docker_network: str = "web"
    traefik_url: str = "http://localhost:8081"
    cloudflared_config: Path = Path("/etc/cloudflared/config.yml")
    cloudflare_api_token: str = ""

    @property
    def config_path(self) -> Path:
        return Path.home() / ".config" / "homectl" / "config.yml"

    def hostname_dir(self, hostname: str) -> Path:
        return self.sites_root / hostname


@dataclass(slots=True)
class RenderContext:
    hostname: str
    safe_name: str
    docker_network: str
    service_name: str = "web"


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
