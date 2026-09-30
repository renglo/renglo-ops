"""Resolve deploy_targets.yml for peer CDK (no aws_cdk dependency)."""

from __future__ import annotations

import os
from pathlib import Path


def ctx_get(source, key: str, env_key: str = "") -> str:
    val = None
    node = getattr(source, "node", None)
    if node is not None:
        val = node.try_get_context(key)
    elif hasattr(source, "get"):
        val = source.get(key, "")
    if val is not None and str(val).strip():
        return str(val).strip()
    return os.environ.get(env_key or key.upper(), "").strip()


def resolve_targets_path(
    source,
    *,
    helper_root: Path,
    tenant_key: str,
    bom_checkout: str,
    cwd: Path | None = None,
) -> Path:
    """Resolve deploy_targets.yml.

    Priority:
    1. ``--context targets=…`` / ``PEER_TARGETS``
    2. ``../<bom_checkout>/deploy_targets.yml`` under bom-helper parent (sibling layout)
    3. ``deploy_targets.yml`` in cwd
    """
    explicit = ctx_get(source, "targets", "PEER_TARGETS")
    base = cwd or Path.cwd()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    helper_parent = helper_root.parent
    repo_name = (bom_checkout or "").strip() or (f"{tenant_key}-bom" if tenant_key else "")
    if repo_name:
        candidate = (helper_parent / repo_name / "deploy_targets.yml").resolve()
        if candidate.is_file():
            return candidate

    sibling_matches = sorted(
        p for p in helper_parent.glob("*-bom/deploy_targets.yml") if p.is_file()
    )
    if len(sibling_matches) == 1:
        return sibling_matches[0].resolve()
    if len(sibling_matches) > 1 and not tenant_key:
        names = ", ".join(p.parent.name for p in sibling_matches)
        raise SystemExit(
            f"Multiple *-bom repos under {helper_parent}: {names}. "
            "Set --context tenant=<key> or --context targets=…"
        )

    return (base / "deploy_targets.yml").resolve()
