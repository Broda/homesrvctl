from __future__ import annotations

from pathlib import Path
import pytest
import typer

from homesrvctl.config import init_config, load_config


def test_init_and_load_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    written = init_config(config_path)

    assert written == config_path
    assert config_path.exists()

    config = load_config(config_path)
    assert config.tunnel_name == "homesrvctl-tunnel"
    assert str(config.sites_root) == "/srv/homesrvctl/sites"
    assert config.cloudflare_api_token == ""


def test_init_config_refuses_overwrite_without_force(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    init_config(config_path)

    with pytest.raises(typer.BadParameter):
        init_config(config_path)


def test_load_config_uses_cloudflare_api_token_env_fallback(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    init_config(config_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "env-token")

    config = load_config(config_path)

    assert config.cloudflare_api_token == "env-token"
