#!/usr/bin/env python3
"""Merge peer-stack CloudFormation outputs into SSM handle → peer map.

Routes are handle → lambda/ECS endpoints only. Handler catalogs stay on
each extension's ``handlers_config.json`` (the peer). Run after
``cdk deploy`` of ``{env}-peer-{peerId}``.

    python scripts/write_peer_routes.py deploy_targets.yml \\
        --env-name acme0813 --region us-east-1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from peers import load_peers, peer_stack_name  # noqa: E402


def _output(outputs: dict[str, str], logical: str) -> str:
    """Read a CDK output by logical id (exact or nested ``Compute…<id><hash>``)."""
    direct = (outputs.get(logical) or "").strip()
    if direct:
        return direct
    matches = [
        str(val).strip()
        for key, val in outputs.items()
        if logical in key and str(val).strip()
    ]
    return matches[0] if matches else ""


def _cfn_outputs(stack_name: str, region: str) -> dict[str, str]:
    import boto3

    cfn = boto3.client("cloudformation", region_name=region)
    stacks = cfn.describe_stacks(StackName=stack_name).get("Stacks") or []
    if not stacks:
        return {}
    out: dict[str, str] = {}
    for item in stacks[0].get("Outputs") or []:
        key = str(item.get("OutputKey") or "").strip()
        val = item.get("OutputValue")
        if key and val is not None:
            out[key] = str(val)
    return out


def _csv_list(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _route_from_outputs(
    extensions: list[str],
    outputs: dict[str, str],
    region: str,
    account: str,
) -> dict[str, Any]:
    fn = _output(outputs, "HandlersLambdaFunctionName")
    if not fn:
        return {}
    route: dict[str, Any] = {
        "lambda_function_name": fn,
        "lambda_arn": f"arn:aws:lambda:{region}:{account}:function:{fn}",
        "ecs_cluster": _output(outputs, "HandlersEcsClusterName"),
        "ecs_task_definition": _output(outputs, "HandlersTaskFamily"),
        "ecs_results_bucket": _output(outputs, "HandlersResultsBucketName"),
        "region": region,
    }
    subnets_raw = _output(outputs, "HandlersComputeSubnetIds")
    sg = _output(outputs, "HandlersComputeSecurityGroupId")
    launch_type = _output(outputs, "HandlersLaunchType")
    network_mode = _output(outputs, "HandlersNetworkMode")
    if subnets_raw:
        route["subnets"] = _csv_list(subnets_raw)
    if sg:
        route["security_groups"] = [sg]
    if launch_type:
        route["launch_type"] = launch_type
    if network_mode:
        route["network_mode"] = network_mode
    return {ext: dict(route) for ext in extensions}


def _put_ssm(name: str, payload: dict[str, Any], region: str, *, dry_run: bool) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if dry_run:
        print(f"[dry-run] {name} ({len(body)} bytes)")
        return
    import boto3

    boto3.client("ssm", region_name=region).put_parameter(
        Name=name, Value=body, Type="String", Overwrite=True
    )
    print(f"wrote {name}")


# Dead overflow / soak leftovers. Routing lives on peer-routes SSM.
_LEGACY_PLATFORM_VAR_KEYS = frozenset(
    {
        "EXTERNAL_HANDLERS_PEER_MAP",
        "EXTERNAL_HANDLERS_HEAVY",
        "EXTERNAL_HANDLERS_ECS_HANDLERS",
        "EXTERNAL_HANDLERS_PEER_ROUTING",
        "LAMBDA_EXTERNAL_HANDLERS_ARN",
        "LAMBDA_HANDLERS_FUNCTION_NAME",
        "ECS_CLUSTER",
        "ECS_TASK_DEFINITION",
        "ECS_RESULTS_BUCKET",
        "ECS_LAUNCH_TYPE",
        "ECS_NETWORK_MODE",
        "ECS_VPC",
        "ECS_SUBNETS",
        "ECS_SECURITY_GROUPS",
    }
)


def _strip_legacy_peer_map_from_platform_vars(
    path: str, region: str, *, dry_run: bool
) -> None:
    """Remove overflow identity and soak blobs from platform-vars."""
    import boto3
    from botocore.exceptions import ClientError

    ssm = boto3.client("ssm", region_name=region)
    try:
        raw = ssm.get_parameter(Name=path, WithDecryption=True)["Parameter"]["Value"]
        data = json.loads(raw)
    except ClientError as exc:
        code = str((exc.response or {}).get("Error", {}).get("Code") or "")
        if code in {"ParameterNotFound", "ResourceNotFoundException"}:
            print(f"skip cleanup {path} (missing)")
            return
        raise
    vars_block = data.get("VARS")
    if not isinstance(vars_block, dict):
        return
    removed = [key for key in _LEGACY_PLATFORM_VAR_KEYS if key in vars_block]
    if not removed:
        return
    for key in removed:
        del vars_block[key]
    _put_ssm(path, data, region, dry_run=dry_run)
    print(f"removed leftover routing key(s) from {path}: {', '.join(removed)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write SSM peer routes from peer stack outputs")
    parser.add_argument("targets_file", help="deploy_targets.yml")
    parser.add_argument("--env-name", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", default="")
    parser.add_argument("--peer-id", default="", help="Single peer; default all catalog peers")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import yaml

    data = yaml.safe_load(Path(args.targets_file).read_text(encoding="utf-8")) or {}
    peers = load_peers(data)
    if args.peer_id:
        peers = [p for p in peers if p["id"] == args.peer_id]
        if not peers:
            print(f"unknown peer {args.peer_id!r}", file=sys.stderr)
            return 1

    account = args.account.strip()
    if not account:
        import boto3

        account = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]

    merged: dict[str, Any] = {}
    for peer in peers:
        region = peer["aws_region"] or args.region
        stack = peer_stack_name(args.env_name, peer["id"])
        outputs = _cfn_outputs(stack, region)
        if not outputs:
            print(f"skip {stack} (no outputs)")
            continue
        route = _route_from_outputs(peer["extensions"], outputs, region, account)
        if not route:
            print(
                f"skip {stack} (no HandlersLambdaFunctionName output)",
                file=sys.stderr,
            )
            continue
        merged.update(route)
        print(f"mapped {stack} → {','.join(peer['extensions'])}")

    if not merged:
        print(
            "ERROR: no peer routes resolved; not writing empty SSM map",
            file=sys.stderr,
        )
        return 1

    routes_path = f"/{args.env_name}/bootstrap/peer-routes"
    _put_ssm(routes_path, {"routes": merged}, args.region, dry_run=args.dry_run)
    for stage in ("staging", "production"):
        _strip_legacy_peer_map_from_platform_vars(
            f"/{args.env_name}/bootstrap/platform-vars/{stage}",
            args.region,
            dry_run=args.dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
