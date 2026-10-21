"""Apply the customer-approved runtime boundary to every role in a stack."""

from __future__ import annotations

from aws_cdk import aws_iam as iam
from constructs import Construct

from renglo_ops.target_role import runtime_policy_arn, uses_named_qualifier


def apply_runtime_boundary(scope: Construct, *, env_name: str, account: str) -> None:
    """New environments only. Older names keep the toolkit they already bootstrapped."""
    if not uses_named_qualifier(env_name):
        return
    iam.PermissionsBoundary.of(scope).apply(
        iam.ManagedPolicy.from_managed_policy_arn(
            scope,
            "RuntimePermissionsBoundary",
            runtime_policy_arn(account, env_name),
        )
    )
