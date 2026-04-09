# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Compile check
python3 -m compileall homesrvctl tests

# Run tests
.venv/bin/python -m pytest -q

# Run a single test file
.venv/bin/python -m pytest tests/test_config.py -q

# Run a single test by name
.venv/bin/python -m pytest -k "test_name" -q

# Build distribution
.venv/bin/python -m build
```

Run compile + tests for all code changes. Add the build step only when packaging or project metadata is involved.

## Architecture

`homesrvctl` is a Typer-based CLI for managing a home-server platform (Cloudflare Tunnel + Traefik + Docker Compose). The package is installed in editable mode from `.venv/`.

### Layer boundaries

| Layer | Files | Owns |
|---|---|---|
| CLI surface | `homesrvctl/main.py`, `homesrvctl/commands/` | Command wiring, flag parsing, output formatting |
| Config/models | `homesrvctl/config.py`, `homesrvctl/models.py` | Config loading, stack-local override resolution |
| Cloudflare | `homesrvctl/cloudflare.py` | DNS API calls, zone lookup, record upsert/remove |
| cloudflared | `homesrvctl/cloudflared.py`, `homesrvctl/cloudflared_service.py` | Ingress config parsing/reconciliation, runtime detection |
| Templates | `homesrvctl/templates.py`, `homesrvctl/templates/` | Jinja2 scaffold rendering for `site init` / `app init` |
| TUI | `homesrvctl/tui/` | Textual app, dashboard, prompts, JSON-backed data loading |
| Utilities | `homesrvctl/shell.py`, `homesrvctl/utils.py` | Subprocess execution, filesystem helpers |

Command modules orchestrate — they call helpers, they don't reimplement them. Cloudflare API logic stays in `cloudflare.py`. `cloudflared` config logic stays in `cloudflared.py`. The TUI loads data through the existing JSON command surface rather than reaching into unrelated modules.

### TUI

`homesrvctl/tui/app.py` is the Textual entrypoint. `tui/data.py` handles JSON data loading from CLI commands. `tui/prompts.py` hosts small modal screens. The `tui_cmd.py` wrapper imports the Textual app lazily so the rest of the CLI remains functional without the dependency.

### Templates

Jinja2 templates live under `homesrvctl/templates/`. App templates are organized by family under `templates/app/<name>/`. Site templates live under `templates/static/`. Template files use `.j2` extensions and are included in the package via `pyproject.toml` package-data globs.

## Before Making Changes

Check `ROADMAP.md` for active milestones and `ARCHITECTURE.md` for module ownership. Keep changes scoped to a single milestone slice.

## Public Contracts

These must not drift accidentally — update tests, docs, and `CHANGELOG.md` when they change:

- CLI command names and major flags
- JSON output shapes (all include `schema_version`)
- `~/.config/homesrvctl/config.yml` format
- Stack-local `<stack>/homesrvctl.yml` format
- Documented scaffold output layout

## Docs to Update With Changes

| Doc | When |
|---|---|
| `README.md` | Public usage changes |
| `CHANGELOG.md` | Any user-visible change |
| `ARCHITECTURE.md` | Module structure or ownership changes |
| `FILE_MAP.md` | New top-level docs, modules, or template families |
| `ROADMAP.md` | Milestone scope changes |
