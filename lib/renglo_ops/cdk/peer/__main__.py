"""Synth peer stacks from renglo.yaml.

    RENGLO_TENANT=/path/to/renglo.yaml PEER_ID=lab python -m renglo_ops.cdk.peer
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_path() -> Path:
    peer_dir = Path(__file__).resolve().parent
    release = peer_dir.parents[1] / "release"
    for entry in (peer_dir, release):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)
    return peer_dir


def main() -> None:
    peer_dir = _prepare_path()
    from aws_cdk import App, Environment

    from peers import load_peers, peer_stack_name  # type: ignore
    from renglo_ops.model.legacy import customer_config_dict
    from renglo_ops.model.tenant import find_tenant_file, load_tenant
    from renglo_ops.release.catalog import catalog_data

    raw = os.environ.get("RENGLO_TENANT", "").strip()
    tenant_path = Path(raw).expanduser().resolve() if raw else find_tenant_file(Path.cwd())
    tenant = load_tenant(tenant_path)
    data = catalog_data(tenant_path.parent)
    if not data:
        raise SystemExit(f"no catalog at {tenant_path}")
    peers = load_peers(data)
    want = os.environ.get("PEER_ID", "").strip()
    selected = [peer for peer in peers if not want or peer["id"] == want]
    if not selected:
        raise SystemExit("no peers in placement.peers" if not want else f"peer_id {want!r} not in catalog")

    cfg = customer_config_dict(tenant)
    primary = tenant.primary_account()
    outdir = os.environ.get("CDK_OUTDIR", "").strip()
    app = App(outdir=outdir or None)
    for peer in selected:
        region = str(peer.get("aws_region") or primary.region)
        stack_name = peer_stack_name(tenant.name, peer["id"])
        HandlersPeerStack(
            app,
            stack_name,
            stack_name=stack_name,
            env=Environment(account=primary.id or None, region=region),
            env_name=tenant.name,
            peer=peer,
            github_handlers_repo=tenant.github_repo,
            github_handlers_owner_id=tenant.github_owner_id or None,
            github_handlers_repo_id=tenant.github_repo_id or None,
            aws_account=primary.id,
            aws_region=region,
            enable_staging=bool(cfg["enable_staging"]),
            package_registry=cfg["package_registry"],
            workspace=_workspace(),
        )
    app.synth()


def _workspace() -> Path:
    raw = os.environ.get("RENGLO_WORKSPACE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd()


class HandlersPeerStack:
    def __init__(self, scope, stack_id, *, env_name, peer, github_handlers_repo, aws_account, aws_region, enable_staging, package_registry, workspace, github_handlers_owner_id=None, github_handlers_repo_id=None, **kwargs):
        from aws_cdk import Stack
        from aws_cdk import aws_iam as iam
        from compute_stack import ComputeStack  # type: ignore

        stack = Stack(scope, stack_id, **kwargs)
        ComputeStack(
            stack,
            "Compute",
            env_name=env_name,
            aws_account=aws_account,
            aws_region=aws_region,
            compute_type=str(peer["compute"]),
            peer_id=str(peer["id"]),
            task_size=str(peer.get("task_size") or "medium"),
            ec2_instance_type=str(peer.get("ec2_instance_type") or "t3.medium"),
            ec2_min_instances=int(peer.get("ec2_min_instances") or 0),
            ec2_desired_instances=int(peer.get("ec2_desired_instances") or 1),
            ec2_max_instances=int(peer.get("ec2_max_instances") or 2),
            github_handlers_repo=github_handlers_repo,
            github_handlers_owner_id=github_handlers_owner_id,
            github_handlers_repo_id=github_handlers_repo_id,
            enable_staging=enable_staging,
            package_registry=package_registry,
            tenant_policy=iam.ManagedPolicy.from_managed_policy_name(
                stack,
                "ImportedTenantPolicy",
                managed_policy_name=f"{env_name}_tt_policy",
            ),
            extension_handles=list(peer.get("extensions") or []),
            extensions_root=workspace,
        )


if __name__ == "__main__":
    main()
