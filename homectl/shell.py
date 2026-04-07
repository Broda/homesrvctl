from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer

from homectl.utils import info


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def command_exists(binary: str) -> bool:
    return shutil.which(binary) is not None


def run_command(
    command: list[str],
    cwd: Path | None = None,
    dry_run: bool = False,
) -> CommandResult:
    info(f"$ {' '.join(command)}")
    if dry_run:
        return CommandResult(command=command, returncode=0, stdout="", stderr="")

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def require_success(result: CommandResult, action: str) -> None:
    if result.ok:
        return
    detail = result.stderr or result.stdout or "no output"
    raise typer.Exit(code=_fail_with_message(f"{action} failed: {detail}"))


def _fail_with_message(message: str) -> int:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return 1
