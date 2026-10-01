"""Registry desired state: registry.yaml for the CodeArtifact stack this org hosts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from renglo_ops.model.errors import RengloOpsError


@dataclass
class Registry:
    name: str
    github_org: str
    publish_repos: list[str] = field(default_factory=list)
    reader_accounts: list[str] = field(default_factory=list)
    python: str = "python-store"
    npm: str = "npm-store"
    path: Path | None = None


def registry_from_dict(data: dict[str, Any], *, path: Path | None = None) -> Registry:
    if not isinstance(data, dict):
        raise RengloOpsError("registry.yaml must be a mapping")
    name = str(data.get("name") or "").strip()
    org = str(data.get("github_org") or "").strip()
    if not name or not org:
        raise RengloOpsError("registry.yaml requires name and github_org")
    repos = data.get("publish_repos") or []
    readers = data.get("reader_accounts") or []
    if not isinstance(repos, list) or not isinstance(readers, list):
        raise RengloOpsError("publish_repos and reader_accounts must be lists")
    return Registry(
        name=name,
        github_org=org,
        publish_repos=[str(item).strip() for item in repos if str(item).strip()],
        reader_accounts=[str(item).strip() for item in readers if str(item).strip()],
        python=str(data.get("python") or "python-store").strip() or "python-store",
        npm=str(data.get("npm") or "npm-store").strip() or "npm-store",
        path=path,
    )


def registry_to_dict(registry: Registry) -> dict[str, Any]:
    return {
        "name": registry.name,
        "github_org": registry.github_org,
        "publish_repos": list(registry.publish_repos),
        "reader_accounts": list(registry.reader_accounts),
        "python": registry.python,
        "npm": registry.npm,
    }


def dump_registry(registry: Registry) -> str:
    return yaml.safe_dump(registry_to_dict(registry), sort_keys=False)


def sanitize_domain_name(publisher_name: str) -> str:
    """CodeArtifact domain for a publisher name. Same rule the publisher stack uses."""
    raw = publisher_name.strip().lower().replace("_", "-")
    cleaned = re.sub(r"[^a-z0-9-]", "", raw)
    if not cleaned:
        raise RengloOpsError("publisher name must contain letters or digits")
    if cleaned[0].isdigit():
        cleaned = f"pkg-{cleaned}"
    return cleaned[:50]


def load_registry(path: Path) -> Registry:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise RengloOpsError(f"registry.yaml not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return registry_from_dict(data, path=path)
