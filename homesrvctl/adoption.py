from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceDetection:
    family: str
    confidence: str
    evidence: tuple[str, ...]
    issues: tuple[str, ...]
    next_steps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WrapperPlan:
    family: str
    service_port: int
    source_path: Path
    template_name: str
    issues: tuple[str, ...]
    next_steps: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def detect_source(source_path: Path) -> SourceDetection:
    path = source_path.expanduser()
    issues: list[str] = []
    if not path.exists():
        return SourceDetection(
            family="unknown",
            confidence="none",
            evidence=(),
            issues=(f"source path does not exist: {path}",),
            next_steps=("Provide an existing application or site directory.",),
        )
    if not path.is_dir():
        return SourceDetection(
            family="unknown",
            confidence="none",
            evidence=(f"source path is not a directory: {path}",),
            issues=("source must be a directory",),
            next_steps=("Provide a directory containing the application source.",),
        )

    evidence = _source_evidence(path)
    family, confidence = _select_family(evidence)
    next_steps = _next_steps(family)
    if family == "unknown":
        issues.append("no supported source markers were found")

    return SourceDetection(
        family=family,
        confidence=confidence,
        evidence=tuple(evidence),
        issues=tuple(issues),
        next_steps=tuple(next_steps),
    )


def plan_wrapper(source_path: Path, requested_family: str | None, service_port: int | None) -> tuple[SourceDetection, WrapperPlan]:
    path = source_path.expanduser()
    detection = detect_source(path)
    resolved_family = _resolve_wrapper_family(detection.family, requested_family)
    resolved_source = path.resolve(strict=False)
    issues = list(detection.issues)
    next_steps: list[str] = []

    if requested_family and requested_family not in {"static", "dockerfile"}:
        issues.append("wrapper family must be one of: static, dockerfile")
    if detection.issues:
        next_steps.extend(detection.next_steps)
    elif resolved_family == "static":
        template_name = "app/wrap/static.compose.yml.j2"
        if not _has_static_index(path):
            issues.append("static wrapper requires an index.html at the source root or under public/, html/, or _site/")
            next_steps.append("Choose `--family dockerfile` when the app should be served by its own container image.")
    elif resolved_family == "dockerfile":
        template_name = "app/wrap/dockerfile.compose.yml.j2"
        if not (path / "Dockerfile").exists():
            issues.append("dockerfile wrapper requires an existing Dockerfile in the source directory")
            next_steps.append("Add a Dockerfile to the source or use a scaffold template that owns the runtime files.")
    else:
        template_name = "app/wrap/dockerfile.compose.yml.j2"
        issues.append("could not choose a wrapper family automatically")
        next_steps.append("Pass `--family static` or `--family dockerfile` explicitly.")

    if service_port is not None and (service_port < 1 or service_port > 65535):
        issues.append("service port must be between 1 and 65535")
    port = service_port or _default_service_port(resolved_family, detection.family)
    if not next_steps:
        next_steps.extend(_wrapper_next_steps(resolved_family))

    return (
        detection,
        WrapperPlan(
            family=resolved_family,
            service_port=port,
            source_path=resolved_source,
            template_name=template_name,
            issues=tuple(issues),
            next_steps=tuple(next_steps),
        ),
    )


def _source_evidence(path: Path) -> list[str]:
    evidence: list[str] = []
    if (path / "docker-compose.yml").exists() or (path / "compose.yml").exists() or (path / "compose.yaml").exists():
        evidence.append("compose-file")
    if (path / "Dockerfile").exists():
        evidence.append("dockerfile")
    if (path / "package.json").exists():
        evidence.append("package-json")
        package = _read_json(path / "package.json")
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        deps = _merged_dependencies(package)
        if "vite" in deps:
            evidence.append("node-vite")
        if "next" in deps:
            evidence.append("node-next")
        if "start" in scripts:
            evidence.append("node-start-script")
    if (path / "requirements.txt").exists():
        evidence.append("python-requirements")
    if (path / "pyproject.toml").exists():
        evidence.append("python-pyproject")
    if (path / "app.py").exists() or (path / "main.py").exists() or (path / "app" / "main.py").exists():
        evidence.append("python-entrypoint")
    if (path / "_config.yml").exists() or (path / "_config.yaml").exists():
        evidence.append("jekyll-config")
    if (path / "Gemfile").exists() and "jekyll" in _read_text(path / "Gemfile").lower():
        evidence.append("jekyll-gemfile")
    for candidate in ("index.html", "public/index.html", "html/index.html", "_site/index.html"):
        if (path / candidate).exists():
            evidence.append(f"static-index:{candidate}")
            break
    return evidence


def _select_family(evidence: list[str]) -> tuple[str, str]:
    evidence_set = set(evidence)
    if {"jekyll-config", "jekyll-gemfile"} & evidence_set:
        return ("jekyll", "high")
    if "package-json" in evidence_set:
        return ("node", "high")
    if {"python-requirements", "python-pyproject", "python-entrypoint"} & evidence_set:
        return ("python", "medium" if "dockerfile" not in evidence_set else "high")
    if any(item.startswith("static-index:") for item in evidence):
        return ("static", "high")
    if "dockerfile" in evidence_set:
        return ("dockerfile", "medium")
    if "compose-file" in evidence_set:
        return ("compose", "medium")
    return ("unknown", "none")


def _next_steps(family: str) -> list[str]:
    if family == "static":
        return ["Use `homesrvctl app wrap HOST --source PATH --family static` to generate an nginx hosting wrapper."]
    if family in {"node", "python", "dockerfile"}:
        return [
            "Use `homesrvctl app wrap HOST --source PATH --family dockerfile --service-port PORT` when the source already has a Dockerfile."
        ]
    if family == "jekyll":
        return [
            "Use `homesrvctl app init HOST --template jekyll` for a managed scaffold, then copy the existing Jekyll source into the generated `site/` directory."
        ]
    if family == "compose":
        return ["Existing Compose adoption is not mutating yet; inspect the file and add homesrvctl routing labels manually for now."]
    return ["Choose a wrapper family explicitly once you know how the app should be served."]


def _resolve_wrapper_family(detected_family: str, requested_family: str | None) -> str:
    if requested_family:
        return requested_family
    if detected_family == "static":
        return "static"
    if detected_family in {"node", "python", "dockerfile"}:
        return "dockerfile"
    return "unknown"


def _default_service_port(wrapper_family: str, detected_family: str) -> int:
    if wrapper_family == "static":
        return 80
    if detected_family == "node":
        return 3000
    if detected_family == "python":
        return 8000
    return 8000


def _has_static_index(path: Path) -> bool:
    return any((path / candidate).exists() for candidate in ("index.html", "public/index.html", "html/index.html", "_site/index.html"))


def _wrapper_next_steps(family: str) -> list[str]:
    if family == "static":
        return ["Run `homesrvctl up HOST` after reviewing the generated wrapper files."]
    if family == "dockerfile":
        return ["Run `homesrvctl up HOST` after confirming the service listens on the configured internal port."]
    return ["Choose a supported wrapper family and rerun the command."]


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _merged_dependencies(package: dict[str, object]) -> set[str]:
    dependencies: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        raw = package.get(key, {})
        if isinstance(raw, dict):
            dependencies.update(str(name) for name in raw)
    return dependencies
