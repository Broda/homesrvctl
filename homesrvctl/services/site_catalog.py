from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import typer
import yaml

from homesrvctl.models import HomesrvctlConfig
from homesrvctl.utils import validate_hostname


COMPOSE_FILE_CANDIDATES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)
DEFAULT_EXPECTED_STATUSES = [200, 301, 302, 403]
SUPPORTED_PLAN_OPERATIONS = {"restart", "compose-up", "compose-pull"}
SAFE_ANNOTATION_FIELDS = {
    "display_name",
    "owner",
    "repo",
    "tags",
    "notes",
    "source_project_paths",
    "health_url",
    "expected_statuses",
    "deployment_kind",
    "app",
    "component",
    "stack_dir",
    "hostnames",
    "volume_paths",
}
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
DATA_DIR_NAMES = {"data", "db", "database", "sqlite", "storage", "var"}


@dataclass(slots=True)
class CatalogValidationIssue:
    check: str
    ok: bool
    severity: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass(slots=True)
class SiteCatalogResult:
    ok: bool
    sites_root: Path
    apps_root: Path
    volumes_root: Path
    sites: list[dict[str, object]]
    annotations_path: Path
    annotations_loaded: bool
    issues: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "sites_root": str(self.sites_root),
            "apps_root": str(self.apps_root),
            "volumes_root": str(self.volumes_root),
            "deployment_roots": {
                "sites": str(self.sites_root),
                "apps": str(self.apps_root),
                "volumes": str(self.volumes_root),
            },
            "annotations_path": str(self.annotations_path),
            "annotations_loaded": self.annotations_loaded,
            "sites": self.sites,
            "issues": self.issues,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(slots=True)
class SiteOperationPlan:
    ok: bool
    site: str
    domain: str
    operation: str
    allowed: bool
    reasons: list[str]
    prechecks: list[dict[str, object]]
    steps: list[str]
    expected_health_statuses: list[int]
    database_risk: dict[str, object]
    rollback_hint: str
    runbook_hint: str
    compose_path: str | None
    services: list[str]

    def to_dict(self) -> dict[str, object]:
        reason = self.reasons[0] if self.reasons else None
        payload: dict[str, object] = {
            "ok": self.ok,
            "site": self.site,
            "domain": self.domain,
            "operation": self.operation,
            "allowed": self.allowed,
            "reasons": self.reasons,
            "prechecks": self.prechecks,
            "steps": self.steps,
            "expected_health_statuses": self.expected_health_statuses,
            "database_risk": self.database_risk,
            "rollback_hint": self.rollback_hint,
            "runbook_hint": self.runbook_hint,
            "compose_path": self.compose_path,
            "services": self.services,
        }
        if reason:
            payload["reason"] = reason
        return payload


def default_site_annotations_path() -> Path:
    return Path.home() / ".config" / "homesrvctl" / "sites.yaml"


def discover_site_catalog(
    config: HomesrvctlConfig,
    *,
    annotations_path: Path | None = None,
) -> SiteCatalogResult:
    target_annotations_path = annotations_path or default_site_annotations_path()
    annotations, annotation_issues, annotations_loaded = load_site_annotations(
        target_annotations_path
    )
    sites: list[dict[str, object]] = []
    issues = list(annotation_issues)
    missing_roots: list[str] = []

    if config.sites_root.exists():
        try:
            children = sorted(config.sites_root.iterdir(), key=lambda path: path.name)
            sites.extend(
                discover_site(
                    config, child.name, annotations=annotations, deployment_kind="site"
                )
                for child in children
                if child.is_dir()
            )
        except OSError as exc:
            issues.append(f"Could not read sites root {config.sites_root}: {exc}")
    else:
        missing_roots.append(f"sites root does not exist: {config.sites_root}")

    if config.apps_root.exists():
        try:
            children = sorted(config.apps_root.iterdir(), key=lambda path: path.name)
            sites.extend(
                discover_site(
                    config,
                    child.name,
                    annotations=annotations,
                    deployment_kind="app",
                    stack_dir=child,
                )
                for child in children
                if child.is_dir()
            )
        except OSError as exc:
            issues.append(f"Could not read apps root {config.apps_root}: {exc}")

    discovered_keys = {str(site.get("site")) for site in sites}
    discovered_paths = {str(site.get("stack_dir")) for site in sites}
    for key, annotation in sorted(annotations.items()):
        raw_stack_dir = annotation.get("stack_dir")
        if not raw_stack_dir:
            continue
        stack_dir = Path(str(raw_stack_dir))
        if key in discovered_keys or str(stack_dir) in discovered_paths:
            continue
        sites.append(
            discover_site(
                config,
                key,
                annotations=annotations,
                deployment_kind=str(annotation.get("deployment_kind") or "app"),
                stack_dir=stack_dir,
            )
        )

    return SiteCatalogResult(
        ok=bool(sites) or not missing_roots,
        sites_root=config.sites_root,
        apps_root=config.apps_root,
        volumes_root=config.volumes_root,
        sites=sites,
        annotations_path=target_annotations_path,
        annotations_loaded=annotations_loaded,
        issues=[*issues, *missing_roots],
        error="; ".join(missing_roots) if missing_roots and not sites else None,
    )


def get_site_info(
    config: HomesrvctlConfig,
    site: str,
    *,
    annotations_path: Path | None = None,
) -> dict[str, object]:
    identifier = validate_catalog_identifier(site)
    target_annotations_path = annotations_path or default_site_annotations_path()
    annotations, annotation_issues, annotations_loaded = load_site_annotations(
        target_annotations_path
    )
    annotation = annotations.get(identifier, {})
    stack_dir = (
        Path(str(annotation["stack_dir"]))
        if annotation.get("stack_dir")
        else config.hostname_dir(identifier)
    )
    deployment_kind = str(
        annotation.get("deployment_kind") or infer_deployment_kind(config, stack_dir)
    )
    if not stack_dir.exists() or not stack_dir.is_dir():
        raise typer.BadParameter(f"deployment directory does not exist: {stack_dir}")
    payload = discover_site(
        config,
        identifier,
        annotations=annotations,
        deployment_kind=deployment_kind,
        stack_dir=stack_dir,
    )
    payload["annotations_path"] = str(target_annotations_path)
    payload["annotations_loaded"] = annotations_loaded
    if annotation_issues:
        payload.setdefault("issues", [])
        payload["issues"] = [*payload["issues"], *annotation_issues]  # type: ignore[index]
    return payload


def plan_site_operation(
    config: HomesrvctlConfig,
    operation: str,
    site: str,
    *,
    annotations_path: Path | None = None,
) -> SiteOperationPlan:
    normalized_operation = operation.strip().lower()
    if normalized_operation not in SUPPORTED_PLAN_OPERATIONS:
        valid = ", ".join(sorted(SUPPORTED_PLAN_OPERATIONS))
        raise typer.BadParameter(
            f"unsupported operation {operation!r}; expected one of: {valid}"
        )

    site_payload = get_site_info(config, site, annotations_path=annotations_path)
    checks = validate_site_metadata(site_payload)
    blocking_failures = [
        check for check in checks if not check.ok and check.severity == "blocking"
    ]
    advisory_failures = [
        check for check in checks if not check.ok and check.severity == "advisory"
    ]
    services = [
        service
        for service in site_payload.get("services", [])
        if isinstance(service, dict)
    ]
    reasons: list[str] = []
    prechecks = [check.to_dict() for check in checks]

    if blocking_failures:
        reasons.extend(check.detail for check in blocking_failures)

    if normalized_operation in {"restart", "compose-up"}:
        if not blocking_failures:
            reasons.append("catalog validation passed and compose services are present")

    if normalized_operation == "compose-pull":
        image_only = [
            service_name(service)
            for service in services
            if service.get("image") and not service.get("build")
        ]
        build_services = [
            service_name(service) for service in services if service.get("build")
        ]
        no_image_services = [
            service_name(service) for service in services if not service.get("image")
        ]
        if blocking_failures:
            pass
        elif build_services:
            reasons.append(
                "compose-pull denied because the site includes build/local-source services: "
                + ", ".join(build_services)
            )
        elif no_image_services:
            reasons.append(
                "compose-pull denied because these services have no image to pull: "
                + ", ".join(no_image_services)
            )
        elif not image_only:
            reasons.append(
                "compose-pull denied because no image-based services were found"
            )
        else:
            reasons.append(
                "compose-pull allowed for image-based services: "
                + ", ".join(image_only)
            )
        prechecks.append(
            {
                "check": "compose_pull_image_policy",
                "ok": (
                    not blocking_failures
                    and bool(image_only)
                    and not build_services
                    and not no_image_services
                ),
                "severity": "blocking",
                "detail": compose_pull_policy_detail(
                    image_only, build_services, no_image_services
                ),
            }
        )

    allowed = not blocking_failures
    if normalized_operation == "compose-pull":
        allowed = allowed and any(
            service.get("image") and not service.get("build") for service in services
        )
        allowed = allowed and not any(service.get("build") for service in services)
        allowed = allowed and not any(not service.get("image") for service in services)

    if advisory_failures and normalized_operation in {"restart", "compose-up"}:
        reasons.extend(f"advisory: {check.detail}" for check in advisory_failures)

    return SiteOperationPlan(
        ok=allowed,
        site=str(site_payload["site"]),
        domain=str(site_payload["domain"]),
        operation=normalized_operation,
        allowed=allowed,
        reasons=reasons or ["operation denied by policy"],
        prechecks=prechecks,
        steps=operation_steps(normalized_operation, allowed),
        expected_health_statuses=[
            int(status) for status in site_payload.get("expected_statuses", [])
        ],
        database_risk=build_database_risk_summary(site_payload),
        rollback_hint=rollback_hint(normalized_operation, site_payload),
        runbook_hint=runbook_hint(normalized_operation, allowed),
        compose_path=string_or_none(site_payload.get("compose_path")),
        services=[service_name(service) for service in services],
    )


def load_site_annotations(
    path: Path,
) -> tuple[dict[str, dict[str, object]], list[str], bool]:
    if not path.exists():
        return {}, [], False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        return {}, [f"could not read annotations file {path}: {exc}"], False
    if not isinstance(raw, dict):
        return {}, [f"annotations file must be a mapping: {path}"], False

    site_mapping = raw.get("sites", raw)
    if not isinstance(site_mapping, dict):
        return {}, [f"annotations field `sites` must be a mapping: {path}"], False

    annotations: dict[str, dict[str, object]] = {}
    issues: list[str] = []
    for hostname, values in site_mapping.items():
        try:
            valid_hostname = validate_catalog_identifier(str(hostname))
        except typer.BadParameter:
            issues.append(
                f"ignoring annotation for invalid site/app identifier: {hostname}"
            )
            continue
        if not isinstance(values, dict):
            issues.append(
                f"ignoring annotation for {valid_hostname}: value must be a mapping"
            )
            continue
        safe_values = {
            key: value for key, value in values.items() if key in SAFE_ANNOTATION_FIELDS
        }
        ignored = sorted(
            str(key) for key in values if key not in SAFE_ANNOTATION_FIELDS
        )
        if ignored:
            issues.append(
                "ignoring unsupported annotation fields for "
                f"{valid_hostname}: {', '.join(ignored)}"
            )
        annotations[valid_hostname] = safe_values
    return annotations, issues, True


def discover_site(
    config: HomesrvctlConfig,
    hostname: str,
    *,
    annotations: dict[str, dict[str, object]] | None = None,
    deployment_kind: str = "site",
    stack_dir: Path | None = None,
) -> dict[str, object]:
    valid_hostname = validate_catalog_identifier(hostname)
    annotation = (annotations or {}).get(valid_hostname, {})
    if annotation.get("stack_dir"):
        stack_dir = Path(str(annotation["stack_dir"]))
    else:
        stack_dir = stack_dir or config.hostname_dir(valid_hostname)
    compose_path, compose_issues = find_compose_file(stack_dir)
    compose_data: dict[str, object] = {}
    parse_issues: list[str] = []
    if compose_path:
        try:
            loaded = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                compose_data = loaded
            else:
                parse_issues.append(f"compose file must be a mapping: {compose_path}")
        except (OSError, yaml.YAMLError) as exc:
            parse_issues.append(f"could not parse compose file {compose_path}: {exc}")

    services = parse_services(compose_data, stack_dir)
    top_level_volumes = parse_top_level_volumes(compose_data)
    named_volumes = sorted({*top_level_volumes, *service_named_volume_names(services)})
    source_paths = sorted(source_project_paths(services, stack_dir))
    database_hints = build_database_hints(services, stack_dir)

    hostnames = normalize_string_list(annotation.get("hostnames"))
    domain = (
        hostnames[0]
        if hostnames
        else (valid_hostname if looks_like_hostname(valid_hostname) else valid_hostname)
    )
    health_url = str(
        annotation.get("health_url")
        or (f"https://{domain}/" if looks_like_hostname(domain) else "")
    )
    expected_statuses = normalize_expected_statuses(annotation.get("expected_statuses"))
    annotation_source_paths = normalize_string_list(
        annotation.get("source_project_paths")
    )
    volume_paths = sorted(
        {
            *paths_under_root(services, config.volumes_root),
            *normalize_string_list(annotation.get("volume_paths")),
        }
    )
    if annotation_source_paths:
        source_paths = sorted({*source_paths, *annotation_source_paths})

    return {
        "site": valid_hostname,
        "domain": domain,
        "deployment_kind": str(annotation.get("deployment_kind") or deployment_kind),
        "app": string_or_none(annotation.get("app")),
        "component": string_or_none(annotation.get("component")),
        "hostnames": hostnames,
        "stack_dir": str(stack_dir),
        "compose_path": str(compose_path) if compose_path else None,
        "compose_file": compose_path.name if compose_path else None,
        "services": services,
        "service_names": [str(service["name"]) for service in services],
        "named_volumes": named_volumes,
        "source_project_paths": source_paths,
        "volume_paths": volume_paths,
        "database_hints": database_hints,
        "health_url": health_url,
        "expected_statuses": expected_statuses,
        "annotations": safe_annotation_payload(annotation),
        "issues": [*compose_issues, *parse_issues],
    }


def validate_catalog_identifier(value: str) -> str:
    text = value.strip()
    if not text or "/" in text or text in {".", ".."}:
        raise typer.BadParameter(f"invalid site/app identifier: {value}")
    if looks_like_hostname(text):
        return validate_hostname(text)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", text):
        raise typer.BadParameter(f"invalid site/app identifier: {value}")
    return text


def looks_like_hostname(value: str) -> bool:
    return "." in value


def infer_deployment_kind(config: HomesrvctlConfig, stack_dir: Path) -> str:
    try:
        stack_dir.resolve().relative_to(config.apps_root.resolve())
        return "app"
    except (OSError, ValueError):
        return "site"


def paths_under_root(services: list[dict[str, object]], root: Path) -> set[str]:
    paths: set[str] = set()
    try:
        resolved_root = root.resolve()
    except OSError:
        resolved_root = root
    for service in services:
        for volume in service.get("volumes", []):
            if not isinstance(volume, dict):
                continue
            resolved = volume.get("resolved_source")
            if not resolved:
                continue
            path = Path(str(resolved))
            try:
                path.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                continue
            paths.add(str(path))
    return paths


def find_compose_file(stack_dir: Path) -> tuple[Path | None, list[str]]:
    existing = [
        stack_dir / name
        for name in COMPOSE_FILE_CANDIDATES
        if (stack_dir / name).exists()
    ]
    if not existing:
        return None, [f"missing compose file under {stack_dir}"]
    issues = []
    if len(existing) > 1:
        issues.append(
            "multiple compose files found; using "
            f"{existing[0].name}: {', '.join(path.name for path in existing)}"
        )
    return existing[0], issues


def parse_services(
    compose_data: dict[str, object], stack_dir: Path
) -> list[dict[str, object]]:
    raw_services = compose_data.get("services", {})
    if not isinstance(raw_services, dict):
        return []
    services: list[dict[str, object]] = []
    for name, raw_service in sorted(
        raw_services.items(), key=lambda item: str(item[0])
    ):
        if not isinstance(raw_service, dict):
            services.append(
                {
                    "name": str(name),
                    "image": None,
                    "build": None,
                    "container_name": None,
                    "restart": None,
                    "ports": [],
                    "volumes": [],
                }
            )
            continue
        services.append(
            {
                "name": str(name),
                "image": string_or_none(raw_service.get("image")),
                "build": normalize_build(raw_service.get("build"), stack_dir),
                "container_name": string_or_none(raw_service.get("container_name")),
                "restart": string_or_none(raw_service.get("restart")),
                "ports": normalize_ports(raw_service.get("ports")),
                "volumes": normalize_volumes(raw_service.get("volumes"), stack_dir),
            }
        )
    return services


def normalize_build(raw_build: object, stack_dir: Path) -> dict[str, object] | None:
    if raw_build is None:
        return None
    if isinstance(raw_build, str):
        return {
            "context": raw_build,
            "resolved_context": str(resolve_compose_path(raw_build, stack_dir)),
        }
    if isinstance(raw_build, dict):
        context = raw_build.get("context")
        dockerfile = raw_build.get("dockerfile")
        payload: dict[str, object] = {
            "context": str(context) if context is not None else None,
            "dockerfile": str(dockerfile) if dockerfile is not None else None,
        }
        if context is not None:
            payload["resolved_context"] = str(
                resolve_compose_path(str(context), stack_dir)
            )
        return payload
    return {"raw_type": type(raw_build).__name__}


def normalize_ports(raw_ports: object) -> list[dict[str, object]]:
    if not isinstance(raw_ports, list):
        return []
    ports = []
    for raw_port in raw_ports:
        if isinstance(raw_port, str):
            ports.append({"raw": raw_port})
        elif isinstance(raw_port, dict):
            ports.append(
                {
                    "target": raw_port.get("target"),
                    "published": raw_port.get("published"),
                    "protocol": raw_port.get("protocol"),
                    "mode": raw_port.get("mode"),
                }
            )
        else:
            ports.append({"raw": str(raw_port)})
    return ports


def normalize_volumes(raw_volumes: object, stack_dir: Path) -> list[dict[str, object]]:
    if not isinstance(raw_volumes, list):
        return []
    volumes = []
    for raw_volume in raw_volumes:
        if isinstance(raw_volume, str):
            volumes.append(parse_short_volume(raw_volume, stack_dir))
        elif isinstance(raw_volume, dict):
            source = raw_volume.get("source") or raw_volume.get("src")
            target = (
                raw_volume.get("target")
                or raw_volume.get("dst")
                or raw_volume.get("destination")
            )
            volume_type = (
                str(raw_volume.get("type"))
                if raw_volume.get("type")
                else infer_volume_type(source)
            )
            payload: dict[str, object] = {
                "type": volume_type,
                "source": str(source) if source is not None else None,
                "target": str(target) if target is not None else None,
                "read_only": (
                    bool(raw_volume.get("read_only"))
                    if "read_only" in raw_volume
                    else None
                ),
            }
            if source is not None and volume_type == "bind":
                payload["resolved_source"] = str(
                    resolve_compose_path(str(source), stack_dir)
                )
            volumes.append(payload)
        else:
            volumes.append({"type": "unknown", "raw": str(raw_volume)})
    return volumes


def parse_short_volume(raw_volume: str, stack_dir: Path) -> dict[str, object]:
    parts = raw_volume.split(":")
    source = parts[0] if len(parts) >= 2 else None
    target = parts[1] if len(parts) >= 2 else parts[0]
    mode = ":".join(parts[2:]) if len(parts) > 2 else None
    volume_type = infer_volume_type(source)
    payload: dict[str, object] = {
        "type": volume_type,
        "source": source,
        "target": target,
        "mode": mode,
        "raw": raw_volume,
    }
    if source and volume_type == "bind":
        payload["resolved_source"] = str(resolve_compose_path(source, stack_dir))
    return payload


def infer_volume_type(source: object) -> str:
    if source is None:
        return "anonymous"
    source_text = str(source)
    if source_text.startswith(("/", "./", "../", "~")) or "/" in source_text:
        return "bind"
    return "volume"


def parse_top_level_volumes(compose_data: dict[str, object]) -> set[str]:
    raw_volumes = compose_data.get("volumes", {})
    if not isinstance(raw_volumes, dict):
        return set()
    return {str(name) for name in raw_volumes}


def service_named_volume_names(services: list[dict[str, object]]) -> set[str]:
    names: set[str] = set()
    for service in services:
        for volume in service.get("volumes", []):
            if (
                isinstance(volume, dict)
                and volume.get("type") == "volume"
                and volume.get("source")
            ):
                names.add(str(volume["source"]))
    return names


def source_project_paths(
    services: list[dict[str, object]], stack_dir: Path
) -> set[str]:
    paths: set[str] = set()
    for service in services:
        build = service.get("build")
        if isinstance(build, dict) and build.get("resolved_context"):
            paths.add(str(build["resolved_context"]))
        for volume in service.get("volumes", []):
            if (
                isinstance(volume, dict)
                and volume.get("type") == "bind"
                and volume.get("resolved_source")
            ):
                resolved = Path(str(volume["resolved_source"]))
                if likely_source_path(resolved, stack_dir):
                    paths.add(str(resolved))
    return paths


def likely_source_path(path: Path, stack_dir: Path) -> bool:
    try:
        relative = path.resolve().relative_to(stack_dir.resolve())
    except (OSError, ValueError):
        return True
    if not relative.parts:
        return True
    return relative.parts[0] not in DATA_DIR_NAMES


def build_database_hints(
    services: list[dict[str, object]], stack_dir: Path
) -> dict[str, object]:
    postgres_services = []
    sqlite_paths: set[str] = set()
    for service in services:
        name = str(service.get("name") or "")
        image = str(service.get("image") or "")
        if "postgres" in name.lower() or image.lower().startswith("postgres"):
            postgres_services.append(name)
        for volume in service.get("volumes", []):
            if isinstance(volume, dict):
                for key in ("target", "source", "resolved_source"):
                    value = volume.get(key)
                    if value and Path(str(value)).suffix.lower() in SQLITE_SUFFIXES:
                        sqlite_paths.add(str(value))

    for data_dir in data_directories(stack_dir):
        try:
            for path in data_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in SQLITE_SUFFIXES:
                    sqlite_paths.add(str(path))
        except OSError:
            continue

    return {
        "postgres_services": sorted(postgres_services),
        "has_postgres": bool(postgres_services),
        "sqlite_paths": sorted(sqlite_paths),
        "has_sqlite": bool(sqlite_paths),
    }


def data_directories(stack_dir: Path) -> list[Path]:
    if not stack_dir.exists():
        return []
    try:
        return [
            child
            for child in stack_dir.iterdir()
            if child.is_dir() and child.name.lower() in DATA_DIR_NAMES
        ]
    except OSError:
        return []


def validate_site_metadata(site: dict[str, object]) -> list[CatalogValidationIssue]:
    issues = [
        CatalogValidationIssue(
            check="compose_file_present",
            ok=bool(site.get("compose_path")),
            severity="blocking",
            detail="compose file found"
            if site.get("compose_path")
            else "compose file is missing",
        ),
        CatalogValidationIssue(
            check="services_present",
            ok=bool(site.get("services")),
            severity="blocking",
            detail=(
                "services found"
                if site.get("services")
                else "no services found in compose file"
            ),
        ),
        CatalogValidationIssue(
            check="health_url_present",
            ok=bool(site.get("health_url")),
            severity="blocking",
            detail=str(site.get("health_url") or "health_url is missing"),
        ),
        CatalogValidationIssue(
            check="expected_statuses_present",
            ok=bool(site.get("expected_statuses")),
            severity="blocking",
            detail=str(site.get("expected_statuses") or "expected_statuses is empty"),
        ),
    ]
    for service in site.get("services", []):
        if isinstance(service, dict):
            name = str(service.get("name") or "unknown")
            has_runtime = bool(service.get("image") or service.get("build"))
            issues.append(
                CatalogValidationIssue(
                    check=f"service_runtime:{name}",
                    ok=has_runtime,
                    severity="advisory",
                    detail=(
                        "service has image or build"
                        if has_runtime
                        else "service has neither image nor build"
                    ),
                )
            )
    for detail in site.get("issues", []):
        issues.append(
            CatalogValidationIssue(
                check="discovery_issue",
                ok=False,
                severity="advisory",
                detail=str(detail),
            )
        )
    return issues


def service_name(service: dict[str, object]) -> str:
    return str(service.get("name") or "unknown")


def compose_pull_policy_detail(
    image_only: list[str],
    build_services: list[str],
    no_image_services: list[str],
) -> str:
    if build_services:
        return "build/local-source services present: " + ", ".join(build_services)
    if no_image_services:
        return "services without images present: " + ", ".join(no_image_services)
    if image_only:
        return "image-based services found: " + ", ".join(image_only)
    return "no image-based services found"


def operation_steps(operation: str, allowed: bool) -> list[str]:
    if not allowed:
        return [
            "Review the failed prechecks and repair catalog or Compose metadata first.",
            "Re-run homesrvctl sites validate before attempting a mutating operation.",
        ]
    common = [
        "Confirm the compose file and service list match the intended site.",
        "Review database_risk and take a fresh backup when stateful data is present.",
        "Run the mutating Docker Compose operation outside this read-only planner.",
        "Check the site health endpoint after the operation.",
    ]
    if operation == "compose-pull":
        return [
            "Confirm image tags are intentional and pullable from the host.",
            *common,
        ]
    return common


def build_database_risk_summary(site: dict[str, object]) -> dict[str, object]:
    hints = (
        site.get("database_hints")
        if isinstance(site.get("database_hints"), dict)
        else {}
    )
    postgres_services = (
        list(hints.get("postgres_services", [])) if isinstance(hints, dict) else []
    )
    sqlite_paths = (
        list(hints.get("sqlite_paths", [])) if isinstance(hints, dict) else []
    )
    has_database = bool(postgres_services or sqlite_paths)
    return {
        "has_database": has_database,
        "level": "elevated" if has_database else "none_detected",
        "postgres_services": postgres_services,
        "sqlite_paths": sqlite_paths,
        "note": (
            "Stateful data inferred; take or verify a backup before mutation."
            if has_database
            else "No database hints inferred from catalog metadata."
        ),
    }


def rollback_hint(operation: str, site: dict[str, object]) -> str:
    statuses = ", ".join(str(status) for status in site.get("expected_statuses", []))
    health_url = str(site.get("health_url") or "")
    if operation == "compose-pull":
        return (
            "If a later update fails, restore the prior image tag or known-good "
            "Compose file and redeploy."
        )
    if operation == "compose-up":
        return (
            "If startup fails, inspect Compose logs and restore the previous "
            "Compose file or data backup."
        )
    return (
        "If restart fails, inspect Compose logs and verify "
        f"{health_url} returns one of: {statuses}."
    )


def runbook_hint(operation: str, allowed: bool) -> str:
    if not allowed:
        return (
            "Planner denied this operation; do not run the matching mutation "
            "until prechecks pass."
        )
    if operation == "restart":
        return "Approved planner output is suitable as a preflight before docker compose restart."
    if operation == "compose-up":
        return "Approved planner output is suitable as a preflight before docker compose up -d."
    return (
        "Approved planner output is suitable as a preflight before docker compose pull."
    )


def normalize_expected_statuses(raw_statuses: object) -> list[int]:
    if raw_statuses is None:
        return [*DEFAULT_EXPECTED_STATUSES]
    if not isinstance(raw_statuses, list):
        return [*DEFAULT_EXPECTED_STATUSES]
    statuses = []
    for status in raw_statuses:
        try:
            statuses.append(int(status))
        except (TypeError, ValueError):
            continue
    return sorted(set(statuses)) or [*DEFAULT_EXPECTED_STATUSES]


def normalize_string_list(raw_value: object) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if not isinstance(raw_value, list):
        return []
    return sorted(str(value) for value in raw_value if value is not None)


def safe_annotation_payload(annotation: dict[str, object]) -> dict[str, object]:
    return {
        key: annotation[key]
        for key in sorted(annotation)
        if key in SAFE_ANNOTATION_FIELDS
        and key not in {"health_url", "expected_statuses", "source_project_paths"}
    }


def resolve_compose_path(path: str, stack_dir: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve(strict=False)
    return (stack_dir / expanded).resolve(strict=False)


def string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
