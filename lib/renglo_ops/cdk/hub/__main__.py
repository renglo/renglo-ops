"""Synth hub stacks A and B from renglo.yaml.

    RENGLO_TENANT=/path/to/renglo.yaml python -m renglo_ops.cdk.hub
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_path() -> Path:
    hub = Path(__file__).resolve().parent
    release = hub.parents[1] / "release"
    for entry in (hub, release):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)
    return hub


def _workspace(hub: Path) -> Path:
    raw = os.environ.get("RENGLO_WORKSPACE", "").strip() or os.environ.get("EXTENSIONS_WORKSPACE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from extension_actions import repo_workspace_root  # type: ignore

    return repo_workspace_root(start=hub)


def main() -> None:
    hub = _prepare_path()
    import aws_cdk as cdk

    from renglo_ops.model.legacy import customer_config_dict
    from renglo_ops.model.tenant import find_tenant_file, load_tenant

    raw = os.environ.get("RENGLO_TENANT", "").strip()
    tenant_path = Path(raw).expanduser().resolve() if raw else find_tenant_file(Path.cwd())
    tenant = load_tenant(tenant_path)
    os.environ["RENGLO_CATALOG"] = str(tenant_path)
    cfg = customer_config_dict(tenant)

    import platform_defaults

    original_load = platform_defaults.load_platform_defaults

    def _load(config_dir: Path | None = None) -> dict:
        data = dict(original_load(config_dir))
        data["architecture"] = tenant.architecture
        cognito = dict(data.get("cognito") or {})
        cognito["token_validity_hours"] = tenant.cognito_token_hours
        data["cognito"] = cognito
        return data

    platform_defaults.load_platform_defaults = _load

    from extension_actions import (  # type: ignore
        extra_action_roots,
        find_deploy_targets,
        hub_actions_specs,
    )
    from extension_loader import (  # type: ignore
        load_extension_config,
        load_extension_manifest,
        resolve_extension_folder,
    )
    from lib.package_registry import validate_package_registry  # type: ignore
    from platform_defaults import architecture as platform_architecture  # type: ignore
    from stack_names import stack_a_id, stack_b_id  # type: ignore
    from stacks.stack_a import StackA  # type: ignore
    from stacks.stack_b import StackB  # type: ignore

    env_name = cfg["env_name"]
    github_repo = cfg["github_repo"]
    outdir = os.environ.get("CDK_OUTDIR", "").strip()
    app = cdk.App(outdir=outdir or None)
    stack_a = StackA(
        app,
        stack_a_id(env_name),
        env_name=env_name,
        github_repo=github_repo,
        enable_staging=bool(cfg["enable_staging"]),
        email_from=cfg["email_from"],
        email_identity_type=cfg["email_identity_type"],
        email_hosted_zone_id=cfg["email_hosted_zone_id"],
        github_owner_id=cfg["github_owner_id"] or None,
        github_repo_id=cfg["github_repo_id"] or None,
        package_registry=validate_package_registry(cfg["package_registry"]),
    )
    extension_path = os.environ.get("RENGLO_EXTENSION_PATH", "").strip()
    extension_folder = None
    extension_manifest = None
    extension_config = None
    if extension_path:
        extension_folder = resolve_extension_folder(extension_path)
        extension_manifest = load_extension_manifest(extension_folder)
        extension_config = load_extension_config(extension_folder)
    workspace = _workspace(hub)
    targets = find_deploy_targets(cdk_dir=hub, github_repo=github_repo)
    hub_actions = (
        hub_actions_specs(targets, workspace, extra_roots=extra_action_roots(hub))
        if targets is not None
        else []
    )
    stack_b = StackB(
        app,
        stack_b_id(env_name),
        env_name=env_name,
        github_repo=github_repo,
        enable_staging=bool(cfg["enable_staging"]),
        architecture=platform_architecture(),
        tenant_role=stack_a.tt_role,
        stack_a_auth=stack_a.auth,
        stack_a_storage=stack_a.storage,
        stack_a_console=stack_a.console,
        stack_a_runtime=stack_a.runtime,
        stack_a_ai_storage=stack_a.ai_storage,
        from_email=stack_a.from_email,
        extension_folder=extension_folder,
        extension_manifest=extension_manifest,
        extension_config=extension_config,
        include_extension=extension_folder is not None and extension_manifest is not None,
        hub_actions_specs=hub_actions,
    )
    stack_b.add_dependency(stack_a)
    app.synth()


if __name__ == "__main__":
    main()
