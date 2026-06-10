from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from homesrvctl.config import load_config
from homesrvctl.main import app
from homesrvctl.services.site_catalog import discover_site_catalog, get_site_info, validate_site_metadata


def _write_config(home: Path, sites_root: Path) -> Path:
    config_dir = home / ".config" / "homesrvctl"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "tunnel_name": "homesrvctl-tunnel",
                "sites_root": str(sites_root),
                "docker_network": "web",
                "traefik_url": "http://localhost:8081",
                "cloudflared_config": "/etc/cloudflared/config.yml",
                "cloudflare_api_token": "",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config_path


def _write_catalog_stack(sites_root: Path) -> Path:
    stack_dir = sites_root / "app.example.com"
    stack_dir.mkdir(parents=True)
    (stack_dir / "data").mkdir()
    (stack_dir / "data" / "app.sqlite3").write_text("", encoding="utf-8")
    (stack_dir / "docker-compose.yml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "web": {
                        "build": {"context": "../source-app", "dockerfile": "Dockerfile"},
                        "container_name": "app-web",
                        "restart": "unless-stopped",
                        "ports": ["127.0.0.1:8080:80"],
                        "volumes": [
                            "../source-app/public:/usr/share/nginx/html:ro",
                            "web-data:/var/lib/app",
                        ],
                        "environment": [
                            "DATABASE_URL=postgres://user:password@example.invalid/app",
                            "API_SECRET=do-not-print",
                        ],
                    },
                    "postgres": {
                        "image": "postgres:16",
                        "restart": "unless-stopped",
                        "volumes": ["pg-data:/var/lib/postgresql/data"],
                    },
                },
                "volumes": {
                    "pg-data": None,
                    "web-data": None,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return stack_dir


def test_site_catalog_discovers_compose_metadata_without_env_values(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sites_root = tmp_path / "sites"
    config_path = _write_config(home, sites_root)
    stack_dir = _write_catalog_stack(sites_root)
    config = load_config(config_path)

    result = discover_site_catalog(config, annotations_path=home / ".config" / "homesrvctl" / "missing-sites.yaml")

    assert result.ok is True
    assert len(result.sites) == 1
    site = result.sites[0]
    assert site["site"] == "app.example.com"
    assert site["compose_path"] == str(stack_dir / "docker-compose.yml")
    assert site["compose_file"] == "docker-compose.yml"
    assert site["service_names"] == ["postgres", "web"]
    assert site["named_volumes"] == ["pg-data", "web-data"]
    assert site["database_hints"] == {
        "postgres_services": ["postgres"],
        "has_postgres": True,
        "sqlite_paths": [str(stack_dir / "data" / "app.sqlite3")],
        "has_sqlite": True,
    }
    web = next(service for service in site["services"] if service["name"] == "web")
    assert web["build"]["context"] == "../source-app"
    assert web["build"]["resolved_context"] == str(sites_root / "source-app")
    assert web["container_name"] == "app-web"
    assert web["restart"] == "unless-stopped"
    assert web["ports"] == [{"raw": "127.0.0.1:8080:80"}]
    assert str(sites_root / "source-app") in site["source_project_paths"]
    serialized = json.dumps(site)
    assert "do-not-print" not in serialized
    assert "password" not in serialized
    assert "API_SECRET" not in serialized


def test_site_catalog_merges_safe_annotations(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sites_root = tmp_path / "sites"
    config_path = _write_config(home, sites_root)
    _write_catalog_stack(sites_root)
    annotations_path = home / ".config" / "homesrvctl" / "sites.yaml"
    annotations_path.write_text(
        yaml.safe_dump(
            {
                "sites": {
                    "app.example.com": {
                        "owner": "ops",
                        "repo": "git@example.invalid:ops/app.git",
                        "health_url": "https://app.example.com/healthz",
                        "expected_statuses": [200, "204"],
                        "source_project_paths": ["/srv/source/app"],
                        "secret_token": "do-not-print",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)

    site = get_site_info(config, "app.example.com", annotations_path=annotations_path)

    assert site["health_url"] == "https://app.example.com/healthz"
    assert site["expected_statuses"] == [200, 204]
    assert "/srv/source/app" in site["source_project_paths"]
    assert site["annotations"] == {
        "owner": "ops",
        "repo": "git@example.invalid:ops/app.git",
    }
    assert any("secret_token" in issue for issue in site["issues"])
    assert "do-not-print" not in json.dumps(site)


def test_sites_list_json_uses_compact_deterministic_shape(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    sites_root = tmp_path / "sites"
    _write_config(home, sites_root)
    _write_catalog_stack(sites_root)
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(app, ["sites", "list", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1"
    assert payload["action"] == "sites_list"
    assert payload["ok"] is True
    assert payload["sites_root"] == str(sites_root)
    assert payload["annotations_loaded"] is False
    assert payload["sites"] == [
        {
            "site": "app.example.com",
            "domain": "app.example.com",
            "stack_dir": str(sites_root / "app.example.com"),
            "compose_path": str(sites_root / "app.example.com" / "docker-compose.yml"),
            "compose_file": "docker-compose.yml",
            "service_names": ["postgres", "web"],
            "health_url": "https://app.example.com/",
            "expected_statuses": [200, 301, 302, 403],
            "issues": [],
        }
    ]


def test_sites_info_json_reports_full_site(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    sites_root = tmp_path / "sites"
    _write_config(home, sites_root)
    _write_catalog_stack(sites_root)
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(app, ["sites", "info", "app.example.com", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1"
    assert payload["action"] == "sites_info"
    assert payload["ok"] is True
    assert payload["site"]["site"] == "app.example.com"
    assert payload["site"]["services"][0]["name"] == "postgres"


def test_site_catalog_validation_flags_missing_compose(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sites_root = tmp_path / "sites"
    config_path = _write_config(home, sites_root)
    (sites_root / "empty.example.com").mkdir(parents=True)
    config = load_config(config_path)

    site = get_site_info(
        config,
        "empty.example.com",
        annotations_path=home / ".config" / "homesrvctl" / "missing-sites.yaml",
    )
    checks = validate_site_metadata(site)

    assert any(check.check == "compose_file_present" and not check.ok for check in checks)
    assert any(check.check == "services_present" and not check.ok for check in checks)


def test_sites_validate_json_exits_nonzero_for_missing_compose(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    sites_root = tmp_path / "sites"
    _write_config(home, sites_root)
    (sites_root / "empty.example.com").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(app, ["sites", "validate", "empty.example.com", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1"
    assert payload["action"] == "sites_validate"
    assert payload["ok"] is False
    assert payload["site"] == "empty.example.com"
    assert any(check["check"] == "compose_file_present" and not check["ok"] for check in payload["checks"])
