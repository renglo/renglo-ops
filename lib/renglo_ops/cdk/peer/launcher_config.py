"""Shared launcher customer-config (same source as bootstrap / Stack A/B)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def launcher_config_path(helper_root: Path) -> Path:
    override = os.environ.get("LAUNCHER_CUSTOMER_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (helper_root.parent / "launcher" / "cdk" / "customer-config.json").resolve()


def load_launcher_config(helper_root: Path) -> dict | None:
    path = launcher_config_path(helper_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def github_repo_checkout_name(github_repo: str) -> str:
    """``Org/acme-bom`` → ``acme-bom`` (sibling folder under ops/)."""
    repo = github_repo.strip().rstrip("/")
    if "/" in repo:
        return repo.split("/", 1)[1]
    return repo


def tenant_key_for_env(tenants: dict, env_name: str) -> str:
    """Return ``env_name`` when it is a ``tenants:`` key. The key is the AWS prefix."""
    want = env_name.strip()
    if not want:
        return ""
    cfg = tenants.get(want)
    if isinstance(cfg, dict):
        return want
    return ""


def resolve_from_launcher(helper_root: Path) -> dict[str, str]:
    """Best-effort shared config; empty strings for missing fields."""
    cfg = load_launcher_config(helper_root) or {}
    env_name = str(cfg.get("env_name", "")).strip()
    github_repo = str(cfg.get("github_repo", "")).strip()
    bom_checkout = github_repo_checkout_name(github_repo) if github_repo else ""
    package_registry = cfg.get("package_registry")
    package_registry_json = (
        json.dumps(package_registry) if isinstance(package_registry, dict) else ""
    )
    return {
        "env_name": env_name,
        "bom_repo": github_repo,
        "bom_checkout": bom_checkout,
        "github_owner_id": str(cfg.get("github_owner_id", "")).strip(),
        "github_repo_id": str(cfg.get("github_repo_id", "")).strip(),
        "package_registry_json": package_registry_json,
    }
