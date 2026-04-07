# Roadmap

`homectl` is intentionally small and operationally focused. This roadmap is a lightweight backlog for the next useful upgrades.

## Now

- Add machine-readable `--json` output for `domain status`, `validate`, and `doctor`.
- Keep the test suite and CI green as the CLI surface grows.
- Preserve a simple operator model: one command should do the obvious thing, with `--dry-run` available for preview.

## Next

- Tighten validation and error messages around partial or conflicting domain state.
- Add more domain-level diagnostics and repair hints for unsafe or ambiguous `cloudflared` config states.
- Consider a dedicated service-management command surface for inspecting or controlling `cloudflared`.

## Recently Completed

- Added `homectl domain add` support for Cloudflare DNS upserts plus `cloudflared` ingress reconciliation.
- Added optional `--restart-cloudflared` support for domain-changing commands.
- Added `homectl domain remove` for DNS and ingress teardown.
- Added `homectl domain status` with `ok`, `partial`, and `misconfigured` reporting.
- Added `homectl domain repair` to converge stale or partial domain state.
- Added a shared `cloudflared` service-management abstraction for runtime detection and restart handling.
- Added CI via GitHub Actions and updated it to Node 24-compatible action versions.
- Cleaned the public repository for release with generic examples, neutral defaults, and MIT licensing metadata.

## Later

- Expand `app init` templates beyond the current placeholder and minimal scaffolds.
- Add packaging and release automation for tagged versions.
- Add richer configuration options for more than one local ingress target or routing profile.
- Consider broader Cloudflare API coverage where it meaningfully improves reliability over CLI-based flows.
