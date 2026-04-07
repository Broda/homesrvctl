from __future__ import annotations

import typer

from homectl.cloudflare import CloudflareApiClient, CloudflareApiError, tunnel_cname_target
from homectl.config import load_config
from homectl.utils import info, success, validate_bare_domain

domain_cli = typer.Typer(help="Manage domain-level Cloudflare Tunnel DNS routing.")


@domain_cli.command("add")
def domain_add(
    domain: str = typer.Argument(..., help="Bare domain to route through the existing Cloudflare Tunnel."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print commands without making changes."),
) -> None:
    """Create apex and wildcard tunnel DNS routes for a domain."""
    config = load_config()
    bare_domain = validate_bare_domain(domain)
    client = CloudflareApiClient(config.cloudflare_api_token)

    try:
        zone = client.get_zone(bare_domain)
        zone_id = str(zone["id"])
        target = tunnel_cname_target(config)
        records = [bare_domain, f"*.{bare_domain}"]

        for record_name in records:
            if dry_run:
                plan = client.plan_dns_record(zone_id, record_name, target)
                info(
                    f"[dry-run] {plan.action} DNS {plan.record_type} {plan.record_name} -> {plan.content}"
                )
                continue

            result = client.apply_dns_record(zone_id, record_name, target)
            action_label = {
                "create": "created",
                "update": "updated",
                "noop": "verified",
            }.get(result.action, result.action)
            info(f"{action_label} DNS {result.record_type} {result.record_name} -> {result.content}")
    except (CloudflareApiError, typer.BadParameter) as exc:
        raise typer.Exit(code=_exit_with_error(str(exc))) from exc

    if dry_run:
        success(f"Dry-run complete for domain {bare_domain}")
    else:
        success(f"Added apex and wildcard tunnel DNS routes for {bare_domain}")


def _exit_with_error(message: str) -> int:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return 1
