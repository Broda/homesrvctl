from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from homesrvctl.cloudflare import CloudflareApiClient, CloudflareApiError, tunnel_cname_target
from homesrvctl.models import HomesrvctlConfig
from homesrvctl.shell import CommandResult


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def test_apply_dns_record_updates_existing_record(monkeypatch) -> None:
    client = CloudflareApiClient("test-token")
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request_json(method: str, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
        seen.append((method, path, body))
        if method == "GET":
            return {
                "success": True,
                "result": [{"id": "record-123", "name": "example.com", "type": "A", "content": "1.2.3.4", "proxied": True}],
            }
        return {"success": True, "result": {"id": "record-123"}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    result = client.apply_dns_record("zone-123", "example.com", "uuid.cfargotunnel.com")

    assert result.action == "update"
    assert seen[1][0] == "PUT"
    assert seen[1][1] == "/zones/zone-123/dns_records/record-123"
    assert seen[1][2] == {
        "type": "CNAME",
        "name": "example.com",
        "content": "uuid.cfargotunnel.com",
        "proxied": True,
        "ttl": 1,
    }


def test_apply_dns_record_removal_deletes_existing_record(monkeypatch) -> None:
    client = CloudflareApiClient("test-token")
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request_json(method: str, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
        seen.append((method, path, body))
        if method == "GET":
            return {
                "success": True,
                "result": [
                    {
                        "id": "record-123",
                        "name": "example.com",
                        "type": "CNAME",
                        "content": "uuid.cfargotunnel.com",
                        "proxied": True,
                    }
                ],
            }
        return {"success": True, "result": {"id": "record-123"}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    result = client.apply_dns_record_removal("zone-123", "example.com")

    assert result.action == "delete"
    assert seen[1][0] == "DELETE"
    assert seen[1][1] == "/zones/zone-123/dns_records/record-123"


def test_plan_dns_record_removal_noop(monkeypatch) -> None:
    client = CloudflareApiClient("test-token")

    monkeypatch.setattr(client, "_request_json", lambda method, path, body=None: {"success": True, "result": []})

    result = client.plan_dns_record_removal("zone-123", "example.com")

    assert result.action == "noop"


def test_plan_dns_record_noop(monkeypatch) -> None:
    client = CloudflareApiClient("test-token")

    monkeypatch.setattr(
        client,
        "_request_json",
        lambda method, path, body=None: {
            "success": True,
            "result": [{"id": "record-123", "name": "*.example.com", "type": "CNAME", "content": "uuid.cfargotunnel.com", "proxied": True}],
        },
    )

    result = client.plan_dns_record("zone-123", "*.example.com", "uuid.cfargotunnel.com")

    assert result.action == "noop"


def test_get_zone_raises_for_missing_zone(monkeypatch) -> None:
    client = CloudflareApiClient("test-token")
    monkeypatch.setattr(client, "_request_json", lambda method, path, body=None: {"success": True, "result": []})

    with pytest.raises(CloudflareApiError):
        client.get_zone("missing.example.com")


def test_tunnel_cname_target_uses_cloudflared_config_uuid(tmp_path: Path) -> None:
    cloudflared_config = tmp_path / "cloudflared.yml"
    cloudflared_config.write_text("tunnel: 11111111-2222-4333-8444-555555555555\n", encoding="utf-8")
    config = HomesrvctlConfig(
        tunnel_name="homesrvctl-tunnel",
        sites_root=tmp_path / "sites",
        docker_network="web",
        traefik_url="http://localhost:8081",
        cloudflared_config=cloudflared_config,
        cloudflare_api_token="token",
    )

    assert tunnel_cname_target(config) == "11111111-2222-4333-8444-555555555555.cfargotunnel.com"


def test_tunnel_cname_target_falls_back_to_cloudflared_info(monkeypatch, tmp_path: Path) -> None:
    from homesrvctl import cloudflare

    config = HomesrvctlConfig(
        tunnel_name="homesrvctl-tunnel",
        sites_root=tmp_path / "sites",
        docker_network="web",
        traefik_url="http://localhost:8081",
        cloudflared_config=tmp_path / "missing.yml",
        cloudflare_api_token="token",
    )

    monkeypatch.setattr(
        cloudflare,
        "run_command",
        lambda command: CommandResult(command, 0, "NAME: homesrvctl-tunnel\nID: 11111111-2222-4333-8444-555555555555\n", ""),
    )

    assert tunnel_cname_target(config) == "11111111-2222-4333-8444-555555555555.cfargotunnel.com"


def test_client_requires_api_token() -> None:
    with pytest.raises(typer.BadParameter):
        CloudflareApiClient("")
