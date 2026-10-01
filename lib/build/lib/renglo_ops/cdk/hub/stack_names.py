"""CloudFormation stack IDs and descriptions for the two-stack CDK layout."""

from __future__ import annotations

REGLO_STACK_DESCRIPTION = "Reglo deployment"


def stack_a_id(env_name: str) -> str:
    return f"{env_name}-stack-a"


def stack_b_id(env_name: str) -> str:
    return f"{env_name}-stack-b"


def stack_a_description() -> str:
    return (
        f"{REGLO_STACK_DESCRIPTION} — auth, storage, runtime "
        "(Cognito, S3, DynamoDB, ECR, seed CodeBuild, IAM, CodeDeploy, OIDC)"
    )


def stack_b_description(*, include_extension: bool = True) -> str:
    areas = ["app"]
    details = ["backend Lambda/API Gateway"]
    if include_extension:
        areas.append("extension")
        details.append("extension S3/IAM")
    return f"{REGLO_STACK_DESCRIPTION} — {', '.join(areas)} ({'; '.join(details)})"
