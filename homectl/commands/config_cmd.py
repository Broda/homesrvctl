from __future__ import annotations

from pathlib import Path

import typer

from homectl.config import default_config_path, init_config
from homectl.utils import success

config_cli = typer.Typer(help="Manage homectl configuration.")


@config_cli.command("init")
def config_init(
    path: Path = typer.Option(
        default_config_path(),
        "--path",
        help="Write config to a custom path instead of the default user config location.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
) -> None:
    """Write a starter config file."""
    written = init_config(path=path, force=force)
    success(f"Wrote config to {written}")
