#!/usr/bin/env python3
"""Tear down the overflow handlers node after peer smoke is green.

Default is dry-run. Live delete requires both ``--execute`` and
``CONFIRM_OVERFLOW_TEARDOWN=yes``.

Requires a named AWS profile (``--profile`` or ``AWS_PROFILE``). Refuses
``default``. On ``--execute``, verifies caller account matches ``--account``.

Does **not** delete peer stacks (``{env}-peer-{peerId}``), Stack A, or
Stack B API/websocket.

AWS overflow resources (when --execute):

  * Lambda ``{env}-handlers`` and log group ``/aws/lambda/{env}-handlers``
  * ECS cluster ``{env}-handlers``, task family ``{env}-handlers-ecs``
  * ECR ``{env}-handlers-ecs``
  * S3 ``{env}-handlers-ecs-{account}``
  * IAM overflow roles/policy (not ``{env}-peer-{peerId}-*``)

SSM: strips singleton overflow keys from platform-vars after peers are the
only route. Stack B ComputeStack peel is documented in
docs/OVERFLOW_TEARDOWN.md — that is a git change, not this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

OVERFLOW_LAMBDA_SUFFIX = "-handlers"


def overflow_names(env_name: str, account: str) -> dict[str, str]:
    env = env_name.strip()
    return {
        "lambda": f"{env}-handlers",
        "lambda_log": f"/aws/lambda/{env}-handlers",
        "cluster": f"{env}-handlers",
        "task_family": f"{env}-handlers-ecs",
        "ecr": f"{env}-handlers-ecs",
        "bucket": f"{env}-handlers-ecs-{account}",
        "lambda_role": f"{env}-handlers-role",
        "ecs_exec_role": f"{env}-handlers-ecs-execution",
        "ecs_task_role": f"{env}-handlers-ecs-task",
        "policy": f"{env[0].upper()}{env[1:]}HandlersPolicy",
        "oidc_staging": f"GitHubActionsHandlersRole-{env}-staging",
        "oidc_production": f"GitHubActionsHandlersRole-{env}-production",
    }


def is_overflow_lambda(env_name: str, function_name: str) -> bool:
    """True for ``{env}-handlers``; false for peer Lambdas (``{env}-peer-{peerId}``)."""
    return function_name.strip() == f"{env_name.strip()}{OVERFLOW_LAMBDA_SUFFIX}"


def _resolve_profile(args: argparse.Namespace) -> str:
    profile = (args.profile or os.environ.get("AWS_PROFILE") or "").strip()
    if not profile:
        print("Missing AWS profile: pass --profile or set AWS_PROFILE", file=sys.stderr)
        raise SystemExit(2)
    if profile == "default":
        print("Refusing profile 'default'; use a named tenant profile", file=sys.stderr)
        raise SystemExit(2)
    return profile


def _boto_session(profile: str, region: str):
    import boto3

    return boto3.Session(profile_name=profile, region_name=region)


def _verify_account(session, expected_account: str) -> None:
    caller = session.client("sts").get_caller_identity()["Account"]
    expected = expected_account.strip()
    if caller != expected:
        print(
            f"Credential account {caller} does not match --account {expected}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(f"Verified AWS account {caller} (profile {session.profile_name})")


def _delete_lambda(session, names: dict[str, str], execute: bool) -> None:
    fn = names["lambda"]
    print(f"Lambda {fn}")
    if not execute:
        return
    from botocore.exceptions import ClientError

    client = session.client("lambda")
    try:
        client.delete_function(FunctionName=fn)
        print(f"  deleted {fn}")
    except ClientError as exc:
        print(f"  skip {fn}: {exc}")
    logs = session.client("logs")
    try:
        logs.delete_log_group(logGroupName=names["lambda_log"])
        print(f"  deleted {names['lambda_log']}")
    except ClientError as exc:
        print(f"  skip log group: {exc}")


def _delete_ecs(session, names: dict[str, str], execute: bool) -> None:
    print(f"ECS cluster {names['cluster']} family {names['task_family']}")
    if not execute:
        return
    from botocore.exceptions import ClientError

    ecs = session.client("ecs")
    try:
        arns = ecs.list_task_definitions(familyPrefix=names["task_family"]).get("taskDefinitionArns") or []
        for arn in arns:
            ecs.deregister_task_definition(taskDefinition=arn)
            print(f"  deregistered {arn}")
    except ClientError as exc:
        print(f"  skip task defs: {exc}")
    try:
        ecs.delete_cluster(cluster=names["cluster"])
        print(f"  deleted cluster {names['cluster']}")
    except ClientError as exc:
        print(f"  skip cluster: {exc}")


def _delete_ecr_s3_iam(
    session, names: dict[str, str], account: str, execute: bool
) -> None:
    print(f"ECR {names['ecr']}  S3 {names['bucket']}")
    if not execute:
        return
    from botocore.exceptions import ClientError

    ecr = session.client("ecr")
    try:
        ecr.delete_repository(repositoryName=names["ecr"], force=True)
        print(f"  deleted ECR {names['ecr']}")
    except ClientError as exc:
        print(f"  skip ECR: {exc}")
    s3 = session.client("s3")
    try:
        # Best-effort empty + delete
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=names["bucket"]):
            objs = []
            for key in ("Versions", "DeleteMarkers"):
                for obj in page.get(key) or []:
                    entry: dict[str, Any] = {"Key": obj["Key"]}
                    if obj.get("VersionId"):
                        entry["VersionId"] = obj["VersionId"]
                    objs.append(entry)
            if objs:
                s3.delete_objects(Bucket=names["bucket"], Delete={"Objects": objs})
        s3.delete_bucket(Bucket=names["bucket"])
        print(f"  deleted bucket {names['bucket']}")
    except ClientError as exc:
        print(f"  skip bucket: {exc}")
    iam = session.client("iam")
    for role in (names["lambda_role"], names["ecs_exec_role"], names["ecs_task_role"]):
        try:
            attached = iam.list_attached_role_policies(RoleName=role).get("AttachedPolicies") or []
            for pol in attached:
                iam.detach_role_policy(RoleName=role, PolicyArn=pol["PolicyArn"])
            inlines = iam.list_role_policies(RoleName=role).get("PolicyNames") or []
            for pname in inlines:
                iam.delete_role_policy(RoleName=role, PolicyName=pname)
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except ClientError as exc:
            print(f"  skip role {role}: {exc}")
    policy_arn = f"arn:aws:iam::{account}:policy/{names['policy']}"
    try:
        iam.delete_policy(PolicyArn=policy_arn)
        print(f"  deleted policy {policy_arn}")
    except ClientError as exc:
        print(f"  skip policy: {exc}")
    for role in (names["oidc_staging"], names["oidc_production"]):
        try:
            attached = iam.list_attached_role_policies(RoleName=role).get("AttachedPolicies") or []
            for pol in attached:
                iam.detach_role_policy(RoleName=role, PolicyArn=pol["PolicyArn"])
            inlines = iam.list_role_policies(RoleName=role).get("PolicyNames") or []
            for pname in inlines:
                iam.delete_role_policy(RoleName=role, PolicyName=pname)
            iam.delete_role(RoleName=role)
            print(f"  deleted OIDC role {role}")
        except ClientError as exc:
            print(f"  skip OIDC {role}: {exc}")


def _strip_ssm_overflow(session, env_name: str, execute: bool) -> None:
    keys = (
        "LAMBDA_EXTERNAL_HANDLERS_ARN",
        "LAMBDA_HANDLERS_FUNCTION_NAME",
        "ECS_CLUSTER",
        "ECS_TASK_DEFINITION",
        "ECS_RESULTS_BUCKET",
        "EXTERNAL_HANDLERS_PEER_ROUTING",
    )
    print(f"SSM strip overflow keys from /{env_name}/bootstrap/platform-vars/* : {', '.join(keys)}")
    if not execute:
        return
    from botocore.exceptions import ClientError

    ssm = session.client("ssm")
    for stage in ("staging", "production"):
        name = f"/{env_name}/bootstrap/platform-vars/{stage}"
        try:
            raw = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
            data = json.loads(raw)
        except ClientError as exc:
            print(f"  skip {name}: {exc}")
            continue
        vars_block = data.get("VARS")
        if not isinstance(vars_block, dict):
            continue
        for key in keys:
            vars_block.pop(key, None)
        ssm.put_parameter(Name=name, Value=json.dumps(data, indent=2, sort_keys=True) + "\n", Type="String", Overwrite=True)
        print(f"  updated {name}")
    for extra in (
        f"/{env_name}/bootstrap/ecs-vpc",
        f"/{env_name}/bootstrap/ecs-subnets",
        f"/{env_name}/bootstrap/ecs-security-groups",
    ):
        try:
            ssm.delete_parameter(Name=extra)
            print(f"  deleted {extra}")
        except ClientError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Tear down overflow {env}-handlers after peer cutover")
    parser.add_argument("--env-name", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--profile",
        default="",
        help="AWS CLI profile (else AWS_PROFILE env; must not be default)",
    )
    parser.add_argument("--execute", action="store_true", help="Perform deletes (also needs CONFIRM_OVERFLOW_TEARDOWN=yes)")
    args = parser.parse_args()

    profile = _resolve_profile(args)
    session = _boto_session(profile, args.region)

    names = overflow_names(args.env_name, args.account)
    print("Overflow teardown plan (will not touch peer stacks):")
    print(f"  profile: {profile}")
    for key, value in names.items():
        print(f"  {key}: {value}")

    execute = bool(args.execute) and os.environ.get("CONFIRM_OVERFLOW_TEARDOWN", "").strip() == "yes"
    if args.execute and not execute:
        print("Refusing --execute without CONFIRM_OVERFLOW_TEARDOWN=yes", file=sys.stderr)
        return 2
    if not execute:
        print("dry-run only (pass --execute and CONFIRM_OVERFLOW_TEARDOWN=yes to delete)")
    else:
        _verify_account(session, args.account)

    _delete_lambda(session, names, execute)
    _delete_ecs(session, names, execute)
    _delete_ecr_s3_iam(session, names, args.account, execute)
    _strip_ssm_overflow(session, args.env_name, execute)
    print("Done. Peel Stack B ComputeStack per docs/OVERFLOW_TEARDOWN.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
