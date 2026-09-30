"""Resolve laptop peer CDK inputs from launcher + *-bom catalog."""

from __future__ import annotations

import os
from pathlib import Path

from launcher_config import resolve_from_launcher, tenant_key_for_env
from targets import resolve_targets_path


class _Ctx:
    """Minimal stand-in for CDK context + env (no aws_cdk import)."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, env_key: str = "") -> str:
        val = self._values.get(key, "")
        if val:
            return val
        return os.environ.get(env_key or key.upper(), "").strip()


def load_targets_yaml(path: Path) -> dict:
    import yaml

    if not path.is_file():
        raise SystemExit(f"deploy_targets.yml not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    return data


def resolve_peer_deploy_context(
    *,
    helper_root: Path,
    tenant: str = "",
    env_name: str = "",
    bom_repo: str = "",
    bom_checkout: str = "",
    targets: str = "",
) -> dict[str, str]:
    """Merge overrides with launcher/cdk/customer-config.json + deploy_targets.yml."""
    launcher = resolve_from_launcher(helper_root)
    ctx = _Ctx(
        {
            "tenant": tenant,
            "env_name": env_name,
            "bom_repo": bom_repo,
            "bom_checkout": bom_checkout,
            "targets": targets,
        }
    )

    tenant_key = ctx.get("tenant", "PEER_TENANT")
    bom_checkout = ctx.get("bom_checkout", "BOM_CHECKOUT") or launcher["bom_checkout"]
    env_name = (
        ctx.get("env_name", "PEER_ENV_NAME")
        or os.environ.get("ENV", "").strip()
        or launcher["env_name"]
    )
    bom_repo = ctx.get("bom_repo", "BOM_REPO") or launcher["bom_repo"]

    targets_path = resolve_targets_path(
        ctx,
        helper_root=helper_root,
        tenant_key=tenant_key,
        bom_checkout=bom_checkout,
    )
    if not targets_path.is_file():
        launcher_path = helper_root.parent / "launcher" / "cdk" / "customer-config.json"
        raise SystemExit(
            f"deploy_targets.yml not found: {targets_path}\n"
            f"Expected sibling layout ops/{{bom-helper, launcher, *-bom}}. "
            f"Launcher config: {launcher_path} "
            f"({'found' if launcher_path.is_file() else 'missing'})."
        )

    data = load_targets_yaml(targets_path)
    tenants = data.get("tenants") or {}

    if tenant_key and env_name and tenant_key != env_name:
        raise SystemExit(
            f"tenant {tenant_key!r} and env_name {env_name!r} differ. "
            "The tenants: key is the AWS prefix; they must be the same string."
        )
    if not tenant_key and env_name:
        tenant_key = tenant_key_for_env(tenants, env_name)
    if not env_name and tenant_key:
        env_name = tenant_key

    if not env_name:
        raise SystemExit(
            "Could not resolve env_name. Set launcher/cdk/customer-config.json env_name "
            "or PEER_ENV_NAME / --context env_name=…."
        )
    if not tenant_key or not isinstance(tenants, dict) or tenant_key not in tenants:
        raise SystemExit(
            f"No tenant {env_name!r} in deploy_targets.yml. "
            "Add a tenants: key equal to env_name, or pass PEER_TENANT / --context tenant=…."
        )
    if not bom_repo:
        raise SystemExit(
            "Could not resolve bom_repo. Set launcher/cdk/customer-config.json github_repo "
            "or BOM_REPO / --context bom_repo=org/repo."
        )

    return {
        "tenant": tenant_key,
        "env_name": env_name,
        "bom_repo": bom_repo,
        "bom_checkout": bom_checkout,
        "targets_path": str(targets_path),
        "github_owner_id": launcher.get("github_owner_id", ""),
        "github_repo_id": launcher.get("github_repo_id", ""),
        "package_registry_json": launcher.get("package_registry_json", ""),
    }
