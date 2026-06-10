from __future__ import annotations

import json
from pathlib import Path

import typer

from homesrvctl.config import load_config
from homesrvctl.services.site_catalog import (
    default_site_annotations_path,
    discover_site_catalog,
    get_site_info,
    plan_site_operation,
    validate_site_metadata,
)
from homesrvctl.utils import info, warn, with_json_schema

sites_cli = typer.Typer(help="Inspect read-only site operations metadata and catalog entries.")


@sites_cli.command("list")
def sites_list(
    config_path: Path | None = typer.Option(
        None,
        "--config-path",
        help="Read config from a custom path.",
    ),
    annotations_path: Path | None = typer.Option(
        None,
        "--annotations-path",
        help="Read optional site annotations from a custom YAML path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the site list as JSON."),
) -> None:
    """List sites with compact catalog metadata."""
    result = _load_catalog(config_path, annotations_path, json_output)
    sites = [_compact_site(site) for site in result.sites]
    payload = {
        "action": "sites_list",
        "ok": result.ok,
        "sites_root": str(result.sites_root),
        "annotations_path": str(result.annotations_path),
        "annotations_loaded": result.annotations_loaded,
        "sites": sites,
        "issues": result.issues,
    }
    if result.error:
        payload["error"] = result.error
    _emit_payload_or_list(payload, sites, json_output, result.ok)


@sites_cli.command("inventory")
def sites_inventory(
    config_path: Path | None = typer.Option(
        None,
        "--config-path",
        help="Read config from a custom path.",
    ),
    annotations_path: Path | None = typer.Option(
        None,
        "--annotations-path",
        help="Read optional site annotations from a custom YAML path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the site inventory as JSON."),
) -> None:
    """Show full discovered site metadata for all sites."""
    result = _load_catalog(config_path, annotations_path, json_output)
    payload = {"action": "sites_inventory", **result.to_dict()}
    _emit_payload_or_inventory(payload, json_output, result.ok)


@sites_cli.command("info")
def sites_info(
    site: str = typer.Argument(..., help="Hostname site to inspect."),
    config_path: Path | None = typer.Option(
        None,
        "--config-path",
        help="Read config from a custom path.",
    ),
    annotations_path: Path | None = typer.Option(
        None,
        "--annotations-path",
        help="Read optional site annotations from a custom YAML path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the site metadata as JSON."),
) -> None:
    """Show full discovered metadata for one site."""
    try:
        config = load_config(config_path)
        site_payload = get_site_info(config, site, annotations_path=annotations_path)
    except typer.BadParameter as exc:
        _emit_error("sites_info", str(exc), json_output)
        return
    payload = {
        "action": "sites_info",
        "ok": True,
        "site": site_payload,
    }
    if json_output:
        typer.echo(json.dumps(with_json_schema(payload), indent=2))
        return

    _print_site_detail(site_payload)


@sites_cli.command("validate")
def sites_validate(
    site: str = typer.Argument(..., help="Hostname site to validate."),
    config_path: Path | None = typer.Option(
        None,
        "--config-path",
        help="Read config from a custom path.",
    ),
    annotations_path: Path | None = typer.Option(
        None,
        "--annotations-path",
        help="Read optional site annotations from a custom YAML path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the validation result as JSON."),
) -> None:
    """Validate read-only site catalog metadata for one site."""
    try:
        config = load_config(config_path)
        site_payload = get_site_info(config, site, annotations_path=annotations_path)
    except typer.BadParameter as exc:
        _emit_error("sites_validate", str(exc), json_output)
        return
    checks = validate_site_metadata(site_payload)
    blocking_failures = [check for check in checks if not check.ok and check.severity == "blocking"]
    payload = {
        "action": "sites_validate",
        "ok": not blocking_failures,
        "site": site_payload["site"],
        "checks": [check.to_dict() for check in checks],
    }
    if json_output:
        typer.echo(json.dumps(with_json_schema(payload), indent=2))
        if blocking_failures:
            raise typer.Exit(code=1)
        return

    for check in checks:
        label = "PASS" if check.ok else ("WARN" if check.severity == "advisory" else "FAIL")
        info(f"{label} {check.check}: {check.detail}")
    if blocking_failures:
        warn(f"Site catalog validation failed for {site_payload['site']}")
        raise typer.Exit(code=1)


@sites_cli.command("plan")
def sites_plan(
    operation: str = typer.Argument(
        ...,
        help="Operation to plan: restart, compose-up, or compose-pull.",
    ),
    site: str = typer.Argument(..., help="Hostname site to plan for."),
    config_path: Path | None = typer.Option(
        None,
        "--config-path",
        help="Read config from a custom path.",
    ),
    annotations_path: Path | None = typer.Option(
        None,
        "--annotations-path",
        help="Read optional site annotations from a custom YAML path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the operation plan as JSON."),
) -> None:
    """Plan a read-only, policy-aware site operation."""
    try:
        config = load_config(config_path)
        plan = plan_site_operation(
            config,
            operation,
            site,
            annotations_path=annotations_path,
        )
    except typer.BadParameter as exc:
        _emit_error("sites_plan", str(exc), json_output)
        return
    payload = {"action": "sites_plan", **plan.to_dict()}
    if json_output:
        typer.echo(json.dumps(with_json_schema(payload), indent=2))
        if not plan.allowed:
            raise typer.Exit(code=1)
        return

    _print_operation_plan(payload)
    if not plan.allowed:
        raise typer.Exit(code=1)


def _load_catalog(
    config_path: Path | None,
    annotations_path: Path | None,
    json_output: bool,
):
    try:
        config = load_config(config_path)
    except typer.BadParameter as exc:
        _emit_error("sites_catalog", str(exc), json_output)
        raise typer.Exit(code=1) from exc
    return discover_site_catalog(
        config,
        annotations_path=annotations_path or default_site_annotations_path(),
    )


def _compact_site(site: dict[str, object]) -> dict[str, object]:
    return {
        "site": site["site"],
        "domain": site["domain"],
        "stack_dir": site["stack_dir"],
        "compose_path": site["compose_path"],
        "compose_file": site["compose_file"],
        "service_names": site["service_names"],
        "health_url": site["health_url"],
        "expected_statuses": site["expected_statuses"],
        "issues": site["issues"],
    }


def _emit_payload_or_list(
    payload: dict[str, object],
    sites: list[dict[str, object]],
    json_output: bool,
    ok: bool,
) -> None:
    if json_output:
        typer.echo(json.dumps(with_json_schema(payload), indent=2))
        if not ok:
            raise typer.Exit(code=1)
        return
    if not ok:
        warn(str(payload.get("error") or "site catalog list failed"))
        raise typer.Exit(code=1)
    if not sites:
        warn(f"No hostnames found under {payload['sites_root']}")
        return
    for site in sites:
        services = ", ".join(str(name) for name in site["service_names"]) or "no services"
        info(f"{site['site']}\tcompose={site['compose_file'] or 'missing'}\tservices={services}")


def _emit_payload_or_inventory(payload: dict[str, object], json_output: bool, ok: bool) -> None:
    if json_output:
        typer.echo(json.dumps(with_json_schema(payload), indent=2))
        if not ok:
            raise typer.Exit(code=1)
        return
    if not ok:
        warn(str(payload.get("error") or "site catalog inventory failed"))
        raise typer.Exit(code=1)
    sites = payload.get("sites", [])
    if not sites:
        warn(f"No hostnames found under {payload['sites_root']}")
        return
    for site in sites:
        if isinstance(site, dict):
            _print_site_detail(site)


def _print_site_detail(site: dict[str, object]) -> None:
    info(str(site["site"]))
    typer.echo(f"  stack_dir: {site['stack_dir']}")
    typer.echo(f"  compose_file: {site['compose_file'] or 'missing'}")
    typer.echo(f"  health_url: {site['health_url']}")
    typer.echo(
        "  expected_statuses: "
        + ", ".join(str(value) for value in site["expected_statuses"])
    )
    typer.echo(f"  services: {', '.join(str(name) for name in site['service_names']) or 'none'}")
    source_paths = site.get("source_project_paths") or []
    if source_paths:
        typer.echo(f"  source_project_paths: {', '.join(str(path) for path in source_paths)}")
    database_hints = site.get("database_hints") or {}
    if isinstance(database_hints, dict):
        if database_hints.get("postgres_services"):
            typer.echo(
                "  postgres_services: "
                + ", ".join(str(name) for name in database_hints["postgres_services"])
            )
        if database_hints.get("sqlite_paths"):
            typer.echo(
                "  sqlite_paths: "
                + ", ".join(str(path) for path in database_hints["sqlite_paths"])
            )
    for issue in site.get("issues", []):
        typer.echo(f"  issue: {issue}")


def _print_operation_plan(plan: dict[str, object]) -> None:
    label = "ALLOW" if plan["allowed"] else "DENY"
    info(f"{label} {plan['operation']} for {plan['site']}")
    typer.echo(f"  compose_path: {plan.get('compose_path') or 'missing'}")
    typer.echo(f"  services: {', '.join(str(name) for name in plan['services']) or 'none'}")
    typer.echo(
        "  expected_health_statuses: "
        + ", ".join(str(value) for value in plan["expected_health_statuses"])
    )
    database_risk = plan.get("database_risk") or {}
    if isinstance(database_risk, dict):
        typer.echo(f"  database_risk: {database_risk.get('level', 'unknown')}")
        if database_risk.get("postgres_services"):
            typer.echo(
                "  postgres_services: "
                + ", ".join(str(name) for name in database_risk["postgres_services"])
            )
        if database_risk.get("sqlite_paths"):
            typer.echo(
                "  sqlite_paths: "
                + ", ".join(str(path) for path in database_risk["sqlite_paths"])
            )
    for reason in plan.get("reasons", []):
        typer.echo(f"  reason: {reason}")
    typer.echo("  prechecks:")
    for precheck in plan.get("prechecks", []):
        if not isinstance(precheck, dict):
            continue
        status = "PASS" if precheck.get("ok") else "FAIL"
        if precheck.get("severity") == "advisory" and not precheck.get("ok"):
            status = "WARN"
        typer.echo(f"    {status} {precheck.get('check')}: {precheck.get('detail')}")
    typer.echo("  steps:")
    for step in plan.get("steps", []):
        typer.echo(f"    - {step}")
    typer.echo(f"  rollback_hint: {plan['rollback_hint']}")
    typer.echo(f"  runbook_hint: {plan['runbook_hint']}")


def _emit_error(action: str, message: str, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                with_json_schema(
                    {
                        "action": action,
                        "ok": False,
                        "error": message,
                    }
                ),
                indent=2,
            )
        )
        raise typer.Exit(code=1)
    raise typer.BadParameter(message)
