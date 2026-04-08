from __future__ import annotations

import json
from pathlib import Path

import typer

from homesrvctl.config import default_config_path, init_config
from homesrvctl.utils import success, with_json_schema

config_cli = typer.Typer(help="Manage homesrvctl configuration.")


@config_cli.command("init")
def config_init(
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Write config to a custom path instead of the default user config location.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    """Write a starter config file."""
    target_path = path or default_config_path()
    existed = target_path.exists()
    try:
        written = init_config(path=target_path, force=force)
    except typer.BadParameter as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    with_json_schema(
                        {
                            "action": "config_init",
                            "config_path": str(target_path),
                            "ok": False,
                            "created": False,
                            "overwrote": False,
                            "error": str(exc),
                        }
                    ),
                    indent=2,
                )
            )
            raise typer.Exit(code=1) from exc
        raise

    if json_output:
        typer.echo(
            json.dumps(
                with_json_schema(
                    {
                        "action": "config_init",
                        "config_path": str(written),
                        "ok": True,
                        "created": not existed,
                        "overwrote": existed,
                    }
                ),
                indent=2,
            )
        )
        return

    success(f"Wrote config to {written}")
