"""The role a target account must create before this peer can deploy there.

The peer never reads ~/.aws. It calls sts:AssumeRole on this role. The trust
policy names the installer role for this tenant's namespace and an external id.
The laptop and the peer both use that role. The permissions policy is
what cdk bootstrap and a deploy of stack A and stack B need in that account.
It is written after the env name, account, and region are known, so the hub
resources are limited to that name. The CDK toolkit names are not.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from renglo_ops.model.errors import RengloOpsError

DEFAULT_NAMESPACE = "renglo"
_NAMESPACE = re.compile(r"[a-z][a-z0-9-]{0,22}")
DEFAULT_CDK_QUALIFIER = "hnb659fds"


def normalize_namespace(value: str | None = None) -> str:
    """Short IAM token for one product. Empty means the historical default."""
    text = str(value or "").strip().lower() or DEFAULT_NAMESPACE
    if not _NAMESPACE.fullmatch(text):
        raise RengloOpsError(
            "namespace must start with a letter and use only lowercase letters, digits, and hyphens"
        )
    return text


def deploy_role_name(namespace: str | None = None) -> str:
    return f"{normalize_namespace(namespace)}-deploy"


def installer_role_name(namespace: str | None = None) -> str:
    return f"{normalize_namespace(namespace)}-installer"


def env_tag_key(namespace: str | None = None) -> str:
    return f"{normalize_namespace(namespace)}:env"


def bind_namespace_text(text: str, namespace: str | None = None) -> str:
    """Replace ``{namespace}`` in a policy document with the tenant namespace."""
    return str(text or "").replace("{namespace}", normalize_namespace(namespace))


def is_deploy_role_name(value: str) -> bool:
    text = str(value or "")
    suffix = "-deploy"
    if not text.endswith(suffix):
        return False
    try:
        return deploy_role_name(text[: -len(suffix)]) == text
    except RengloOpsError:
        return False


ROLE_NAME = deploy_role_name()
INSTALLER_ROLE_NAME = installer_role_name()
_ENV_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,39}")
_QUALIFIER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,9}")
_REGION = re.compile(r"[a-z]{2}-[a-z]+-\d")


def new_external_id() -> str:
    return str(uuid.uuid4())


def role_arn(account_id: str, role_name: str = ROLE_NAME) -> str:
    account = str(account_id or "").strip()
    name = str(role_name or "").strip() or ROLE_NAME
    return f"arn:aws:iam::{account}:role/{name}"


def account_from_arn(arn: str) -> str:
    parts = str(arn or "").split(":")
    if len(parts) >= 5 and len(parts[4]) == 12 and parts[4].isdigit():
        return parts[4]
    return ""


def installer_role_arn(account_id: str, namespace: str | None = None) -> str:
    """The one role the laptop and the peer both use before assuming the deploy role."""
    return role_arn(account_id, installer_role_name(namespace))


def is_installer_role(arn: str, namespace: str | None = None) -> bool:
    name = installer_role_name(namespace)
    text = str(arn or "")
    return f":assumed-role/{name}/" in text or text.endswith(f":role/{name}")


def installer_trust_policy(account_id: str) -> dict[str, Any]:
    """Who may become the installer role. Any principal in that account, never a named user."""
    account = str(account_id or "").strip()
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account}:root"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def installer_permissions_policy(namespace: str | None = None) -> dict[str, Any]:
    """The installer role may only assume the deploy role. It does not deploy by itself."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeDeployRole",
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": f"arn:aws:iam::*:role/{deploy_role_name(namespace)}",
            }
        ],
    }


def principal_phrase(arn: str) -> str:
    """Name the trusted principal the way the ARN is actually shaped."""
    text = str(arn or "").strip()
    if ":user/" in text:
        return f"IAM user {text}"
    if ":role/" in text:
        return f"IAM role {text}"
    return text


def role_arn_from_caller(arn: str) -> str:
    """Turn an STS caller ARN into the IAM role ARN that can be trusted.

    A task role shows up as arn:aws:sts::ACCOUNT:assumed-role/ROLE/SESSION.
    The trust policy has to name arn:aws:iam::ACCOUNT:role/ROLE.
    """
    text = str(arn or "").strip()
    marker = ":assumed-role/"
    if marker not in text:
        return text
    partition = text.split(":")[1]
    account = text.split(":")[4]
    role = text.split(marker, 1)[1].split("/", 1)[0]
    return f"arn:{partition}:iam::{account}:role/{role}"


def trust_policy(peer_role_arn: str, external_id: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": peer_role_arn},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
            }
        ],
    }


_S3_ACTIONS = [
    "s3:CreateBucket",
    "s3:DeleteBucket",
    "s3:GetBucketLocation",
    "s3:PutEncryptionConfiguration",
    "s3:GetEncryptionConfiguration",
    "s3:PutBucketPublicAccessBlock",
    "s3:GetBucketPublicAccessBlock",
    "s3:PutBucketCors",
    "s3:GetBucketCors",
    "s3:PutBucketTagging",
    "s3:GetBucketTagging",
    "s3:PutBucketOwnershipControls",
    "s3:GetBucketOwnershipControls",
    "s3:PutBucketPolicy",
    "s3:GetBucketPolicy",
    "s3:DeleteBucketPolicy",
    "s3:PutBucketVersioning",
    "s3:GetBucketVersioning",
    "s3:PutLifecycleConfiguration",
    "s3:GetLifecycleConfiguration",
    "s3:ListBucket",
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:GetObjectVersion",
    "s3:DeleteObjectVersion",
    "s3:AbortMultipartUpload",
    "s3:ListBucketMultipartUploads",
    "s3:ListMultipartUploadParts",
]

_CLOUDFORMATION_ACTIONS = [
    "cloudformation:CreateStack",
    "cloudformation:UpdateStack",
    "cloudformation:DeleteStack",
    "cloudformation:DescribeStacks",
    "cloudformation:DescribeStackEvents",
    "cloudformation:DescribeStackResources",
    "cloudformation:GetTemplate",
    "cloudformation:CreateChangeSet",
    "cloudformation:DescribeChangeSet",
    "cloudformation:ExecuteChangeSet",
    "cloudformation:DeleteChangeSet",
    "cloudformation:ListStackResources",
    "cloudformation:TagResource",
    "cloudformation:UntagResource",
]

_IAM_ROLE_ACTIONS = [
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:GetRole",
    "iam:TagRole",
    "iam:UntagRole",
    "iam:UpdateAssumeRolePolicy",
    "iam:AttachRolePolicy",
    "iam:DetachRolePolicy",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:GetRolePolicy",
    "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies",
    "iam:PassRole",
]

_IAM_POLICY_ACTIONS = [
    "iam:CreatePolicy",
    "iam:DeletePolicy",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:CreatePolicyVersion",
    "iam:DeletePolicyVersion",
    "iam:ListPolicyVersions",
]

_ECR_ACTIONS = [
    "ecr:CreateRepository",
    "ecr:DeleteRepository",
    "ecr:DescribeRepositories",
    "ecr:PutLifecyclePolicy",
    "ecr:GetLifecyclePolicy",
    "ecr:DeleteLifecyclePolicy",
    "ecr:SetRepositoryPolicy",
    "ecr:GetRepositoryPolicy",
    "ecr:DeleteRepositoryPolicy",
    "ecr:TagResource",
    "ecr:UntagResource",
    "ecr:ListTagsForResource",
]

_DYNAMODB_ACTIONS = [
    "dynamodb:CreateTable",
    "dynamodb:DeleteTable",
    "dynamodb:DescribeTable",
    "dynamodb:UpdateTable",
    "dynamodb:TagResource",
    "dynamodb:UntagResource",
    "dynamodb:ListTagsOfResource",
]

_LAMBDA_ACTIONS = [
    "lambda:CreateFunction",
    "lambda:DeleteFunction",
    "lambda:GetFunction",
    "lambda:GetFunctionConfiguration",
    "lambda:UpdateFunctionCode",
    "lambda:UpdateFunctionConfiguration",
    "lambda:PublishVersion",
    "lambda:CreateAlias",
    "lambda:UpdateAlias",
    "lambda:GetAlias",
    "lambda:DeleteAlias",
    "lambda:AddPermission",
    "lambda:RemovePermission",
    "lambda:GetPolicy",
    "lambda:TagResource",
    "lambda:UntagResource",
    "lambda:ListTags",
]

_TABLES = ("blueprints", "entities", "rel", "chat", "session", "search", "data", "graph", "timeseries")
_DATA_INDEXES = ("geo_index", "path_index", "time_index")


def require_env_name(env_name: str) -> str:
    """Init only. A new env name is the CDK qualifier, which allows at most 10 characters."""
    name = str(env_name or "").strip()
    if not _QUALIFIER.fullmatch(name):
        raise RengloOpsError(
            "env name must be 1 to 10 characters, start with a letter, and use only letters, digits, and hyphens"
        )
    return name


def cdk_qualifier(env_name: str) -> str:
    """Use the env name when it fits. Names already deployed stay on the CDK default."""
    name = str(env_name or "").strip()
    if _QUALIFIER.fullmatch(name):
        return name
    return DEFAULT_CDK_QUALIFIER


def uses_named_qualifier(env_name: str) -> bool:
    """A new env name is its own toolkit. Older names stay on the shared default."""
    return cdk_qualifier(env_name) == str(env_name or "").strip() and bool(str(env_name or "").strip())


def toolkit_stack_name(env_name: str) -> str:
    """One toolkit stack per environment. The default name CDKToolkit is shared by the whole account."""
    name = str(env_name or "").strip()
    if uses_named_qualifier(name):
        return f"CDKToolkit-{name}"
    return "CDKToolkit"


def execution_policy_name(env_name: str) -> str:
    return f"{str(env_name or '').strip()}-cfn-exec"


def deploy_policy_name(env_name: str) -> str:
    return f"{str(env_name or '').strip()}-deploy"


def runtime_policy_name(env_name: str) -> str:
    return f"{str(env_name or '').strip()}-runtime"


def runtime_policy_arn(account_id: str, env_name: str) -> str:
    return f"arn:aws:iam::{str(account_id or '').strip()}:policy/{runtime_policy_name(env_name)}"


def execution_policy_arn(account_id: str, env_name: str) -> str:
    return f"arn:aws:iam::{str(account_id or '').strip()}:policy/{execution_policy_name(env_name)}"


def _scope(env_name: str, account_id: str, region: str) -> tuple[str, str, str]:
    name = str(env_name or "").strip()
    if not _ENV_NAME.fullmatch(name):
        raise RengloOpsError("env name must start with a letter and use only letters, digits, and hyphens")
    account = str(account_id or "").strip()
    region_name = str(region or "").strip()
    if not (account.isdigit() and len(account) == 12):
        raise RengloOpsError("account id must be 12 digits")
    if not _REGION.fullmatch(region_name):
        raise RengloOpsError("region must look like us-east-1")
    return name, account, region_name


def _allow(
    sid: str,
    actions: list[str],
    resources: list[str],
    condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    statement: dict[str, Any] = {"Sid": sid, "Effect": "Allow", "Action": actions, "Resource": resources}
    if condition:
        statement["Condition"] = condition
    return statement


def _stages(stage: str) -> tuple[str, ...]:
    which = str(stage or "production").strip() or "production"
    if which not in ("staging", "production"):
        raise RengloOpsError("role must be staging or production")
    if which == "staging":
        return ("production", "staging")
    return ("production",)


def _bucket_arns(*names: str) -> list[str]:
    arns: list[str] = []
    for item in names:
        arns.append(f"arn:aws:s3:::{item}")
        arns.append(f"arn:aws:s3:::{item}/*")
    return arns


def _ses_identity(email_from: str, email_identity: str) -> str:
    sender = str(email_from or "").strip()
    if not sender or "@" not in sender:
        return ""
    kind = str(email_identity or "email").strip().lower() or "email"
    if kind not in ("email", "domain"):
        raise RengloOpsError("email.identity must be email or domain")
    if kind == "domain":
        return sender.split("@", 1)[1].strip().lower()
    return sender


def _permissions_document(
    *,
    env_name: str,
    account_id: str,
    region: str,
    stage: str = "production",
    email_from: str = "",
    email_identity: str = "email",
) -> dict[str, Any]:
    """Deploy policy for one env in one account and region.

    Every action is named. Resources are the buckets, tables, functions, and
    roles stack A and stack B create. Actions AWS will not bind to a name stay on "*".
    """
    name, account, region_name = _scope(env_name, account_id, region)
    qualifier = cdk_qualifier(name)
    stages = _stages(stage)
    slug = name.lower()
    data_bucket = f"{slug}-{account}-{region_name}"
    docs_bucket = f"{slug}-rag-docs-{account}"[:63].rstrip("-.")
    assets_bucket = f"cdk-{qualifier}-assets-{account}-{region_name}"
    assets_repo = f"cdk-{qualifier}-container-assets-{account}-{region_name}"
    backend_repo = f"{name}_backend"
    cdk_roles = [
        f"arn:aws:iam::{account}:role/cdk-{qualifier}-{kind}-role-{account}-{region_name}"
        for kind in ("deploy", "file-publishing", "image-publishing", "lookup", "cfn-exec")
    ]
    role_arns = [
        f"arn:aws:iam::{account}:role/{item}"
        for item in (f"{name}_tt_role", f"{name}-codedeploy-lambda-role", f"{name}-webhook-edge-role")
    ]
    policy_arns = [f"arn:aws:iam::{account}:policy/{item}" for item in (f"{name}_tt_policy", f"{name}_ai_tt_policy")]
    aws_policy_arns = [
        "arn:aws:iam::aws:policy/AdministratorAccess",
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/service-role/AWSCodeDeployRoleForLambda",
    ]
    tables = [f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_{suffix}" for suffix in _TABLES]
    tables.extend(
        f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_data/index/{index}" for index in _DATA_INDEXES
    )
    tables.append(f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_graph/index/backward_index")
    function_names = [f"{name}-webhook-edge", f"{name}-auth-pre-signup"]
    function_names.extend(f"{name}-backend-{item}" for item in stages)
    function_arns = [f"arn:aws:lambda:{region_name}:{account}:function:{item}" for item in function_names]
    function_arns.extend(
        f"arn:aws:lambda:{region_name}:{account}:function:{name}-backend-{item}:{item}" for item in stages
    )
    log_groups = [
        f"arn:aws:logs:{region_name}:{account}:log-group:/aws/lambda/{name}-backend-{item}" for item in stages
    ]
    ssm_names = [f"/{name}/bootstrap/platform-vars/production", f"/{name}/bootstrap/deploy-input"]
    if "staging" in stages:
        ssm_names.append(f"/{name}/bootstrap/platform-vars/staging")
    ses_name = _ses_identity(email_from, email_identity)
    statements: list[dict[str, Any]] = [
            _allow("CallerIdentity", ["sts:GetCallerIdentity"], ["*"]),
            _allow(
                "ActionsWithoutAName",
                [
                    "cloudformation:ListStacks",
                    "cloudformation:GetTemplateSummary",
                    "cloudformation:ValidateTemplate",
                    "ecr:GetAuthorizationToken",
                ],
                ["*"],
            ),
            _allow(
                "ServiceLinkedRoles",
                ["iam:CreateServiceLinkedRole"],
                [f"arn:aws:iam::{account}:role/aws-service-role/*"],
            ),
            _allow("AssumeToolkitRoles", ["sts:AssumeRole"], cdk_roles),
            _allow(
                "CloudFormationStacks",
                _CLOUDFORMATION_ACTIONS,
                [
                    f"arn:aws:cloudformation:{region_name}:{account}:stack/{toolkit_stack_name(name)}/*",
                    f"arn:aws:cloudformation:{region_name}:{account}:stack/{name}-stack-a/*",
                    f"arn:aws:cloudformation:{region_name}:{account}:stack/{name}-stack-b/*",
                ],
            ),
            _allow("IamRoles", _IAM_ROLE_ACTIONS, cdk_roles + role_arns),
            _allow("IamPolicies", _IAM_POLICY_ACTIONS, policy_arns),
            _allow(
                "AwsManagedPolicies",
                [
                    "iam:GetPolicy",
                    "iam:GetPolicyVersion",
                    "iam:AttachRolePolicy",
                    "iam:DetachRolePolicy",
                ],
                aws_policy_arns,
            ),
            _allow("S3Buckets", _S3_ACTIONS, _bucket_arns(data_bucket, docs_bucket, assets_bucket)),
            _allow(
                "EcrRepositories",
                _ECR_ACTIONS,
                [
                    f"arn:aws:ecr:{region_name}:{account}:repository/{backend_repo}",
                    f"arn:aws:ecr:{region_name}:{account}:repository/{assets_repo}",
                ],
            ),
            _allow(
                "SsmParameters",
                [
                    "ssm:PutParameter",
                    "ssm:GetParameter",
                    "ssm:DeleteParameter",
                    "ssm:AddTagsToResource",
                    "ssm:RemoveTagsFromResource",
                ],
                [f"arn:aws:ssm:{region_name}:{account}:parameter/cdk-bootstrap/{qualifier}/version"]
                + [f"arn:aws:ssm:{region_name}:{account}:parameter{item}" for item in ssm_names],
            ),
            _allow("DynamoDbTables", _DYNAMODB_ACTIONS, tables),
            _allow("LambdaFunctions", _LAMBDA_ACTIONS, function_arns),
            _allow(
                "LogGroups",
                [
                    "logs:CreateLogGroup",
                    "logs:DeleteLogGroup",
                    "logs:TagResource",
                    "logs:UntagResource",
                    "logs:ListTagsForResource",
                ],
                log_groups,
            ),
            _allow(
                "CodeBuildProject",
                [
                    "codebuild:CreateProject",
                    "codebuild:DeleteProject",
                    "codebuild:UpdateProject",
                    "codebuild:BatchGetProjects",
                ],
                [f"arn:aws:codebuild:{region_name}:{account}:project/{name}-seed-image"],
            ),
            _allow(
                "CodeDeploy",
                [
                    "codedeploy:CreateApplication",
                    "codedeploy:DeleteApplication",
                    "codedeploy:GetApplication",
                    "codedeploy:CreateDeploymentGroup",
                    "codedeploy:DeleteDeploymentGroup",
                    "codedeploy:GetDeploymentGroup",
                    "codedeploy:UpdateDeploymentGroup",
                ],
                [f"arn:aws:codedeploy:{region_name}:{account}:application:{name}-backend-codedeploy"]
                + [
                    f"arn:aws:codedeploy:{region_name}:{account}:deploymentgroup:{name}-backend-codedeploy/{name}-backend-{item}"
                    for item in stages
                ],
            ),
            _allow(
                "IngressSecret",
                [
                    "secretsmanager:CreateSecret",
                    "secretsmanager:DeleteSecret",
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:UpdateSecret",
                    "secretsmanager:TagResource",
                ],
                [f"arn:aws:secretsmanager:{region_name}:{account}:secret:{name}/renglo/ingress-secret-*"],
            ),
            _allow(
                "WebhookEvents",
                [
                    "events:PutRule",
                    "events:DeleteRule",
                    "events:DescribeRule",
                    "events:PutTargets",
                    "events:RemoveTargets",
                    "events:ListTargetsByRule",
                    "events:CreateConnection",
                    "events:DeleteConnection",
                    "events:DescribeConnection",
                    "events:UpdateConnection",
                    "events:CreateApiDestination",
                    "events:DeleteApiDestination",
                    "events:DescribeApiDestination",
                    "events:UpdateApiDestination",
                    "events:TagResource",
                    "events:UntagResource",
                ],
                [
                    f"arn:aws:events:{region_name}:{account}:rule/{name}-renglo-webhook",
                    f"arn:aws:events:{region_name}:{account}:connection/{name}-renglo-ingress",
                    f"arn:aws:events:{region_name}:{account}:api-destination/{name}-renglo-process",
                ],
            ),
    ]
    if ses_name:
        statements.append(
            _allow(
                "SesIdentity",
                [
                    "ses:GetIdentityVerificationAttributes",
                    "ses:VerifyEmailIdentity",
                    "ses:VerifyDomainIdentity",
                    "ses:GetIdentityDkimAttributes",
                    "ses:VerifyDomainDkim",
                ],
                [f"arn:aws:ses:{region_name}:{account}:identity/{ses_name}"],
            )
        )
    return {"Version": "2012-10-17", "Statement": statements}


def _role_boundary_denies(prefix: str, resources: list[str], boundary_arn: str) -> list[dict[str, Any]]:
    """CreateRole must set this boundary. A missing or different boundary is denied."""
    action = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    return [
        {
            "Sid": f"Deny{prefix}WithoutBoundary",
            "Effect": "Deny",
            "Action": action,
            "Resource": resources,
            "Condition": {"Null": {"iam:PermissionsBoundary": "true"}},
        },
        {
            "Sid": f"Deny{prefix}WrongBoundary",
            "Effect": "Deny",
            "Action": action,
            "Resource": resources,
            "Condition": {"StringNotEquals": {"iam:PermissionsBoundary": boundary_arn}},
        },
    ]


def runtime_policy(
    *,
    env_name: str,
    account_id: str,
    region: str,
    email_from: str = "",
    namespace: str | None = None,
) -> dict[str, Any]:
    """Ceiling for application roles. CloudFormation must set this boundary when it creates them.

    It is wider than the execution policy because the running system sends mail, starts
    tasks, and calls the API. It is not AdministratorAccess. An identity policy written
    at deploy time cannot grant more than this.
    """
    name, account, region_name = _scope(env_name, account_id, region)
    if not uses_named_qualifier(name):
        raise RengloOpsError("a runtime boundary is only generated when the env name is the CDK qualifier")
    sender = str(email_from or "").strip()
    slug = name.lower()
    data_bucket = f"{slug}-{account}-{region_name}"
    docs_bucket = f"{slug}-rag-docs-{account}"[:63].rstrip("-.")
    vector_bucket = f"{slug}-vectors-{account}"[:63].rstrip("-.")
    assets_bucket = f"cdk-{name}-assets-{account}-{region_name}"
    assets_repo = f"cdk-{name}-container-assets-{account}-{region_name}"
    tagged = {"StringEquals": {f"aws:ResourceTag/{env_tag_key(namespace)}": name}}
    roles = [
        f"arn:aws:iam::{account}:role/{name}-*",
        f"arn:aws:iam::{account}:role/{name}_*",
        f"arn:aws:iam::{account}:role/GitHubActionsDeployRole-{name}-*",
    ]
    document = {
        "Version": "2012-10-17",
        "Statement": [
            _allow("CallerIdentity", ["sts:GetCallerIdentity"], ["*"]),
            _allow(
                "Functions",
                ["lambda:*"],
                [
                    f"arn:aws:lambda:{region_name}:{account}:function:{name}-*",
                    f"arn:aws:lambda:{region_name}:{account}:function:{name}-*:*",
                    f"arn:aws:lambda:{region_name}:{account}:function:{name}_*",
                    f"arn:aws:lambda:{region_name}:{account}:function:{name}_*:*",
                ],
            ),
            _allow(
                "Logs",
                ["logs:*"],
                [
                    f"arn:aws:logs:{region_name}:{account}:log-group:/aws/lambda/{name}*",
                    f"arn:aws:logs:{region_name}:{account}:log-group:/aws/lambda/{name}*:*",
                    f"arn:aws:logs:{region_name}:{account}:log-group:/aws/codebuild/{name}*",
                    f"arn:aws:logs:{region_name}:{account}:log-group:/aws/codebuild/{name}*:*",
                ],
            ),
            _allow("Storage", ["s3:*"], _bucket_arns(data_bucket, docs_bucket, assets_bucket) + [
                f"arn:aws:s3:::{name}-*",
                f"arn:aws:s3:::{name}-*/*",
            ]),
            _allow(
                "Tables",
                ["dynamodb:*"],
                [
                    f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_*",
                    f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_*/index/*",
                ],
            ),
            _allow(
                "Build",
                ["codebuild:*"],
                [f"arn:aws:codebuild:{region_name}:{account}:project/{name}-*"],
            ),
            _allow(
                "Releases",
                ["codedeploy:*"],
                [
                    f"arn:aws:codedeploy:{region_name}:{account}:application:{name}-*",
                    f"arn:aws:codedeploy:{region_name}:{account}:deploymentgroup:{name}-*/*",
                    f"arn:aws:codedeploy:{region_name}:{account}:deploymentconfig:*",
                ],
            ),
            _allow(
                "Images",
                ["ecr:*"],
                [
                    f"arn:aws:ecr:{region_name}:{account}:repository/{name}_backend",
                    f"arn:aws:ecr:{region_name}:{account}:repository/{assets_repo}",
                ],
            ),
            _allow("ImageLogin", ["ecr:GetAuthorizationToken"], ["*"]),
            _allow(
                "Tasks",
                ["ecs:RunTask", "ecs:DescribeTasks", "ecs:ListTasks", "ecs:DescribeClusters", "ecs:StopTask"],
                [
                    f"arn:aws:ecs:{region_name}:{account}:cluster/{name}-*",
                    f"arn:aws:ecs:{region_name}:{account}:task-definition/{name}-*",
                    f"arn:aws:ecs:{region_name}:{account}:task/{name}-*/*",
                ],
            ),
            _allow(
                "NetworkRead",
                ["ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"],
                ["*"],
            ),
            _allow(
                "Parameters",
                ["ssm:GetParameter", "ssm:GetParameters", "ssm:PutParameter"],
                [f"arn:aws:ssm:{region_name}:{account}:parameter/{name}/*"],
            ),
            _allow(
                "Events",
                ["events:*"],
                [
                    f"arn:aws:events:{region_name}:{account}:rule/{name}*",
                    f"arn:aws:events:{region_name}:{account}:event-bus/default",
                    f"arn:aws:events:{region_name}:{account}:api-destination/{name}-*",
                    f"arn:aws:events:{region_name}:{account}:connection/{name}-*",
                ],
            ),
            *(
                [
                    _allow(
                        "Mail",
                        ["ses:SendEmail", "ses:SendRawEmail"],
                        [f"arn:aws:ses:{region_name}:{account}:identity/{sender}"],
                    )
                ]
                if sender
                else []
            ),
            _allow("Cognito", ["cognito-idp:*"], [f"arn:aws:cognito-idp:{region_name}:{account}:userpool/*"], tagged),
            _allow(
                "Api",
                ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:DELETE"],
                [f"arn:aws:apigateway:{region_name}::/restapis/*", f"arn:aws:apigateway:{region_name}::/apis/*"],
                tagged,
            ),
            _allow(
                "Sockets",
                ["execute-api:Invoke", "execute-api:ManageConnections"],
                [f"arn:aws:execute-api:{region_name}:{account}:*/*/*/@connections/*"],
            ),
            _allow(
                "Search",
                ["aoss:APIAccessAll"],
                [f"arn:aws:aoss:{region_name}:{account}:collection/*"],
            ),
            _allow(
                "InvokeModels",
                ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                [
                    f"arn:aws:bedrock:{region_name}::foundation-model/*",
                    f"arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{region_name}:{account}:inference-profile/*",
                ],
            ),
            _allow(
                "KnowledgeBases",
                ["bedrock:*"],
                [f"arn:aws:bedrock:{region_name}:{account}:knowledge-base/*"],
                tagged,
            ),
            _allow("ListKnowledgeBases", ["bedrock:ListKnowledgeBases"], ["*"]),
            _allow(
                "Vectors",
                ["s3vectors:*"],
                [
                    f"arn:aws:s3vectors:{region_name}:{account}:bucket/{vector_bucket}",
                    f"arn:aws:s3vectors:{region_name}:{account}:bucket/{vector_bucket}/index/*",
                ],
            ),
            _allow("Amplify", ["amplify:*"], [f"arn:aws:amplify:{region_name}:{account}:apps/*"], tagged),
            _allow(
                "Packages",
                [
                    "codeartifact:DescribeDomain",
                    "codeartifact:GetAuthorizationToken",
                    "codeartifact:GetRepositoryEndpoint",
                    "codeartifact:ReadFromRepository",
                    "codeartifact:DescribeRepository",
                    "codeartifact:ListPackages",
                    "codeartifact:ListPackageVersions",
                    "codeartifact:DescribePackageVersion",
                    "codeartifact:GetPackageVersionAsset",
                ],
                ["*"],
            ),
            _allow(
                "PassRole",
                ["iam:PassRole"],
                [
                    f"arn:aws:iam::{account}:role/{name}_tt_role",
                    f"arn:aws:iam::{account}:role/{name}-*-ecs-execution",
                    f"arn:aws:iam::{account}:role/{name}-*-ecs-task",
                    f"arn:aws:iam::{account}:role/{name}-handlers-role",
                    f"arn:aws:iam::{account}:role/{name}-peer-*-role",
                ],
                {
                    "StringEquals": {
                        "iam:PassedToService": [
                            "lambda.amazonaws.com",
                            "events.amazonaws.com",
                            "ecs-tasks.amazonaws.com",
                        ]
                    }
                },
            ),
            _allow("ReadRoles", ["iam:GetRole", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies"], roles),
            {
                "Sid": "DenyAdministratorAccess",
                "Effect": "Deny",
                "Action": ["iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy"],
                "Resource": "arn:aws:iam::aws:policy/AdministratorAccess",
            },
            {
                "Sid": "DenyBoundaryRemoval",
                "Effect": "Deny",
                "Action": ["iam:DeleteRolePermissionsBoundary", "iam:DeleteUserPermissionsBoundary"],
                "Resource": "*",
            },
        ],
    }
    keep = {"DenyAdministratorAccess", "DenyBoundaryRemoval"}
    for row in document["Statement"]:
        if row.get("Sid") not in keep:
            row.pop("Sid", None)
    return document


def execution_policy(
    *,
    env_name: str,
    account_id: str,
    region: str,
    stage: str = "production",
    email_from: str = "",
    email_identity: str = "email",
    namespace: str | None = None,
) -> dict[str, Any]:
    """Permissions cap for CloudFormation. The admin creates this. Deploy cannot edit it.

    IAM managed policies are limited to 6144 characters. Actions are grouped
    per service, and every resource is this env or its toolkit. A grouped
    action still cannot name a resource outside that list.
    """
    name, account, region_name = _scope(env_name, account_id, region)
    if not uses_named_qualifier(name):
        raise RengloOpsError("an execution policy is only generated when the env name is the CDK qualifier")
    slug = name.lower()
    data_bucket = f"{slug}-{account}-{region_name}"
    docs_bucket = f"{slug}-rag-docs-{account}"[:63].rstrip("-.")
    vector_bucket = f"{slug}-vectors-{account}"[:63].rstrip("-.")
    assets_bucket = f"cdk-{name}-assets-{account}-{region_name}"
    assets_repo = f"cdk-{name}-container-assets-{account}-{region_name}"
    execution_arn = execution_policy_arn(account, name)
    tag_key = env_tag_key(namespace)
    tag = {"StringEquals": {f"aws:RequestTag/{tag_key}": name}}
    tagged = {"StringEquals": {f"aws:ResourceTag/{tag_key}": name}}
    ses_name = _ses_identity(email_from, email_identity)
    statements: list[dict[str, Any]] = [
        _allow("CallerIdentity", ["sts:GetCallerIdentity"], ["*"]),
        _allow(
            "UnnamedReads",
            ["cloudformation:ListStacks", "cloudformation:GetTemplateSummary", "cloudformation:ValidateTemplate", "ecr:GetAuthorizationToken"],
            ["*"],
        ),
        _allow(
            "Stacks",
            ["cloudformation:*"],
            [
                f"arn:aws:cloudformation:{region_name}:{account}:stack/{toolkit_stack_name(name)}/*",
                f"arn:aws:cloudformation:{region_name}:{account}:stack/{name}-stack-a/*",
                f"arn:aws:cloudformation:{region_name}:{account}:stack/{name}-stack-b/*",
            ],
        ),
        _allow("S3", ["s3:*"], _bucket_arns(data_bucket, docs_bucket, assets_bucket)),
        _allow(
            "Tables",
            ["dynamodb:*"],
            [
                f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_*",
                f"arn:aws:dynamodb:{region_name}:{account}:table/{name}_*/index/*",
            ],
        ),
        _allow(
            "Functions",
            ["lambda:*"],
            [
                f"arn:aws:lambda:{region_name}:{account}:function:{name}-*",
                f"arn:aws:lambda:{region_name}:{account}:function:{name}-*:*",
            ],
        ),
        _allow(
            "Roles",
            ["iam:*"],
            [
                f"arn:aws:iam::{account}:role/{name}-*",
                f"arn:aws:iam::{account}:role/{name}_*",
                f"arn:aws:iam::{account}:role/cdk-{name}-*-role-{account}-{region_name}",
                f"arn:aws:iam::{account}:policy/{name}_tt_policy",
                f"arn:aws:iam::{account}:policy/{name}_ai_tt_policy",
            ],
        ),
        _allow(
            "ServiceRoles",
            ["iam:CreateServiceLinkedRole"],
            [f"arn:aws:iam::{account}:role/aws-service-role/*"],
        ),
        _allow(
            "AwsPolicies",
            ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:AttachRolePolicy", "iam:DetachRolePolicy"],
            [
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                "arn:aws:iam::aws:policy/service-role/AWSCodeDeployRoleForLambda",
                "arn:aws:iam::aws:policy/ReadOnlyAccess",
                "arn:aws:iam::aws:policy/AWSCloudFormationReadOnlyAccess",
            ],
        ),
        _allow(
            "Ecr",
            ["ecr:*"],
            [
                f"arn:aws:ecr:{region_name}:{account}:repository/{name}_backend",
                f"arn:aws:ecr:{region_name}:{account}:repository/{assets_repo}",
            ],
        ),
        _allow("Logs", ["logs:*"], [f"arn:aws:logs:{region_name}:{account}:log-group:/aws/lambda/{name}-*"]),
        _allow(
            "Ssm",
            ["ssm:*"],
            [
                f"arn:aws:ssm:{region_name}:{account}:parameter/cdk-bootstrap/{name}/version",
                f"arn:aws:ssm:{region_name}:{account}:parameter/{name}/bootstrap/*",
            ],
        ),
        _allow("Build", ["codebuild:*"], [f"arn:aws:codebuild:{region_name}:{account}:project/{name}-*"]),
        _allow(
            "DeployApps",
            ["codedeploy:*"],
            [
                f"arn:aws:codedeploy:{region_name}:{account}:application:{name}-*",
                f"arn:aws:codedeploy:{region_name}:{account}:deploymentgroup:{name}-*/*",
            ],
        ),
        _allow("Secret", ["secretsmanager:*"], [f"arn:aws:secretsmanager:{region_name}:{account}:secret:{name}/*"]),
        _allow(
            "Events",
            ["events:*"],
            [
                f"arn:aws:events:{region_name}:{account}:rule/{name}-*",
                f"arn:aws:events:{region_name}:{account}:connection/{name}-*",
                f"arn:aws:events:{region_name}:{account}:api-destination/{name}-*",
            ],
        ),
        _allow("CognitoCreate", ["cognito-idp:CreateUserPool"], ["*"], tag),
        _allow("Cognito", ["cognito-idp:*"], [f"arn:aws:cognito-idp:{region_name}:{account}:userpool/*"], tagged),
        _allow(
            "ApiCreate",
            ["apigateway:POST"],
            [f"arn:aws:apigateway:{region_name}::/restapis", f"arn:aws:apigateway:{region_name}::/apis"],
            tag,
        ),
        _allow(
            "Api",
            ["apigateway:*"],
            [f"arn:aws:apigateway:{region_name}::/restapis/*", f"arn:aws:apigateway:{region_name}::/apis/*"],
            tagged,
        ),
        _allow("AmplifyCreate", ["amplify:CreateApp"], ["*"], tag),
        _allow("Amplify", ["amplify:*"], [f"arn:aws:amplify:{region_name}:{account}:apps/*"], tagged),
        _allow("BedrockCreate", ["bedrock:CreateKnowledgeBase"], ["*"], tag),
        _allow("Bedrock", ["bedrock:*"], [f"arn:aws:bedrock:{region_name}:{account}:knowledge-base/*"], tagged),
        _allow(
            "Vectors",
            ["s3vectors:*"],
            [
                f"arn:aws:s3vectors:{region_name}:{account}:bucket/{vector_bucket}",
                f"arn:aws:s3vectors:{region_name}:{account}:bucket/{vector_bucket}/index/*",
            ],
        ),
        {
            "Sid": "DenyAdministratorAccess",
            "Effect": "Deny",
            "Action": ["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
            "Resource": "arn:aws:iam::aws:policy/AdministratorAccess",
        },
        {
            "Sid": "DenyExecutionPolicyChange",
            "Effect": "Deny",
            "Action": ["iam:CreatePolicyVersion", "iam:DeletePolicy", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion"],
            "Resource": f"arn:aws:iam::{account}:policy/{name}-*",
        },
        {
            "Effect": "Deny",
            "Action": "iam:DetachRolePolicy",
            "Resource": "*",
            "Condition": {
                "ArnLike": {"iam:PolicyARN": f"arn:aws:iam::{account}:policy/{name}-*"}
            },
        },
        {
            "Sid": "DenyBoundaryRemoval",
            "Effect": "Deny",
            "Action": ["iam:DeleteRolePermissionsBoundary", "iam:DeleteUserPermissionsBoundary"],
            "Resource": "*",
        },
    ]
    if ses_name:
        statements.append(
            _allow(
                "Ses",
                ["ses:GetIdentityVerificationAttributes", "ses:VerifyEmailIdentity", "ses:VerifyDomainIdentity", "ses:GetIdentityDkimAttributes", "ses:VerifyDomainDkim"],
                [f"arn:aws:ses:{region_name}:{account}:identity/{ses_name}"],
            )
        )
    # IAM allows 6144 characters. A 10-character env name plus a sender address
    # only fits when the review labels are limited to the statements a reviewer
    # has to find: the role list and the three denies.
    keep = {
        "Roles",
        "DenyAdministratorAccess",
        "DenyExecutionPolicyChange",
        "DenyBoundaryRemoval",
    }
    for row in statements:
        if row.get("Sid") not in keep:
            row.pop("Sid", None)
    return {"Version": "2012-10-17", "Statement": statements}


def role_guard_policy(
    *,
    env_name: str,
    account_id: str,
    region: str,
) -> dict[str, Any]:
    """Denies attached to the CloudFormation execution role, beside the execution policy.

    The execution policy is also a permissions boundary, so it cannot hold another
    boundary's rules without exceeding the IAM size limit. This policy is identity
    only. Creating an application role requires the runtime boundary. Creating a
    toolkit role requires the execution boundary.
    """
    name, account, region_name = _scope(env_name, account_id, region)
    if not uses_named_qualifier(name):
        raise RengloOpsError("a role guard is only generated when the env name is the CDK qualifier")
    app_roles = [
        f"arn:aws:iam::{account}:role/{name}-*",
        f"arn:aws:iam::{account}:role/{name}_*",
        f"arn:aws:iam::{account}:role/GitHubActionsDeployRole-{name}-*",
    ]
    toolkit_roles = [
        f"arn:aws:iam::{account}:role/cdk-{name}-*-role-{account}-{region_name}",
    ]
    protected = [
        execution_policy_arn(account, name),
        runtime_policy_arn(account, name),
        f"arn:aws:iam::{account}:policy/{name}-role-guard",
    ]
    return {
        "Version": "2012-10-17",
        "Statement": [
            *_role_boundary_denies("ApplicationRole", app_roles, runtime_policy_arn(account, name)),
            *_role_boundary_denies("ToolkitRole", toolkit_roles, execution_policy_arn(account, name)),
            {
                "Sid": "DenyProtectedPolicyChange",
                "Effect": "Deny",
                "Action": [
                    "iam:CreatePolicyVersion",
                    "iam:DeletePolicy",
                    "iam:DeletePolicyVersion",
                    "iam:SetDefaultPolicyVersion",
                ],
                "Resource": protected,
            },
            {
                "Sid": "DenyProtectedPolicyDetach",
                "Effect": "Deny",
                "Action": "iam:DetachRolePolicy",
                "Resource": "*",
                "Condition": {"ArnEquals": {"iam:PolicyARN": protected}},
            },
        ],
    }


def _deploy_role_policy(
    *,
    env_name: str,
    account_id: str,
    region: str,
) -> dict[str, Any]:
    """What the deploy role may do itself. Creating the application is left to CloudFormation."""
    name, account, region_name = _scope(env_name, account_id, region)
    qualifier = cdk_qualifier(name)
    execution_arn = execution_policy_arn(account, name)
    boundary = {"StringEquals": {"iam:PermissionsBoundary": execution_arn}}
    # One pattern for the five toolkit roles. Listing each ARN repeats the account
    # and region often enough to exceed the 6144-character IAM policy limit.
    cdk_roles = [f"arn:aws:iam::{account}:role/cdk-{qualifier}-*-role-{account}-{region_name}"]
    assume_roles = [
        f"arn:aws:iam::{account}:role/cdk-{qualifier}-{kind}-role-{account}-{region_name}"
        for kind in ("deploy", "file-publishing", "image-publishing", "lookup")
    ]
    assets_bucket = f"cdk-{qualifier}-assets-{account}-{region_name}"
    assets_repo = f"cdk-{qualifier}-container-assets-{account}-{region_name}"
    toolkit_policies = [
        execution_arn,
        "arn:aws:iam::aws:policy/ReadOnlyAccess",
        "arn:aws:iam::aws:policy/AWSCloudFormationReadOnlyAccess",
    ]
    protected = f"arn:aws:iam::{account}:policy/{name}-*"
    document = {
        "Version": "2012-10-17",
        "Statement": [
            _allow("CallerIdentity", ["sts:GetCallerIdentity"], ["*"]),
            _allow(
                "ActionsWithoutAName",
                ["cloudformation:ListStacks", "cloudformation:GetTemplateSummary", "cloudformation:ValidateTemplate", "ecr:GetAuthorizationToken"],
                ["*"],
            ),
            _allow("AssumeToolkitRoles", ["sts:AssumeRole"], assume_roles),
            _allow(
                "CloudFormationToolkit",
                _CLOUDFORMATION_ACTIONS,
                [f"arn:aws:cloudformation:{region_name}:{account}:stack/{toolkit_stack_name(name)}/*"],
            ),
            _allow(
                "ToolkitRoleReads",
                ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"],
                cdk_roles,
            ),
            _allow(
                "ToolkitRoleCreate",
                ["iam:CreateRole", "iam:PutRolePermissionsBoundary"],
                cdk_roles,
                boundary,
            ),
            _allow(
                "ToolkitRoleModify",
                [
                    "iam:DeleteRole",
                    "iam:TagRole",
                    "iam:UntagRole",
                    "iam:UpdateAssumeRolePolicy",
                    "iam:DetachRolePolicy",
                    "iam:DeleteRolePolicy",
                    "iam:PutRolePolicy",
                    "iam:AttachRolePolicy",
                ],
                cdk_roles,
            ),
            _allow(
                "AttachKnownPolicies",
                ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                toolkit_policies,
            ),
            _allow("S3Buckets", _S3_ACTIONS, _bucket_arns(assets_bucket)),
            _allow(
                "EcrRepositories",
                _ECR_ACTIONS,
                [f"arn:aws:ecr:{region_name}:{account}:repository/{assets_repo}"],
            ),
            _allow(
                "SsmParameters",
                ["ssm:PutParameter", "ssm:GetParameter", "ssm:DeleteParameter", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource"],
                [f"arn:aws:ssm:{region_name}:{account}:parameter/cdk-bootstrap/{qualifier}/version"],
            ),
            *_role_boundary_denies("ToolkitRole", cdk_roles, execution_arn),
            {
                "Sid": "DenyAdministratorAccess",
                "Effect": "Deny",
                "Action": ["iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy"],
                "Resource": "arn:aws:iam::aws:policy/AdministratorAccess",
            },
            {
                "Sid": "DenyExecutionPolicyChange",
                "Effect": "Deny",
                "Action": [
                    "iam:CreatePolicy",
                    "iam:CreatePolicyVersion",
                    "iam:DeletePolicy",
                    "iam:DeletePolicyVersion",
                    "iam:SetDefaultPolicyVersion",
                ],
                "Resource": protected,
            },
            {
                "Sid": "DenyProtectedPolicyDetach",
                "Effect": "Deny",
                "Action": "iam:DetachRolePolicy",
                "Resource": "*",
                "Condition": {"ArnLike": {"iam:PolicyARN": protected}},
            },
            {
                "Sid": "DenyBoundaryRemoval",
                "Effect": "Deny",
                "Action": ["iam:DeleteRolePermissionsBoundary", "iam:DeleteUserPermissionsBoundary"],
                "Resource": "*",
            },
        ],
    }
    keep = {
        "ActionsWithoutAName",
        "ToolkitRoleCreate",
        "ToolkitRoleModify",
        "DenyProtectedPolicyDetach",
        "DenyAdministratorAccess",
        "DenyBoundaryRemoval",
    }
    for row in document["Statement"]:
        if row.get("Sid") not in keep:
            row.pop("Sid", None)
    return document


def permissions_policy(
    *,
    env_name: str,
    account_id: str,
    region: str,
    stage: str = "production",
    email_from: str = "",
    email_identity: str = "email",
) -> dict[str, Any]:
    """Policy the admin attaches to renglo-deploy.

    A new env name cannot create application resources itself and cannot attach
    AdministratorAccess. CloudFormation uses the separate execution policy.
    An older env name keeps the shared toolkit behavior.
    """
    name, _account, _region_name = _scope(env_name, account_id, region)
    if uses_named_qualifier(name):
        return _deploy_role_policy(env_name=name, account_id=account_id, region=region)
    return _permissions_document(
        env_name=name,
        account_id=account_id,
        region=region,
        stage=stage,
        email_from=email_from,
        email_identity=email_identity,
    )


def instructions(
    *,
    peer_role_arn: str,
    external_id: str,
    env_name: str,
    account_id: str,
    region: str,
    stage: str = "production",
    email_from: str = "",
    email_identity: str = "email",
    role_name: str = "",
    namespace: str | None = None,
) -> str:
    slug = normalize_namespace(namespace)
    role_name = str(role_name or "").strip() or deploy_role_name(slug)
    name, account, region_name = _scope(env_name, account_id, region)
    if not uses_named_qualifier(name):
        trust = json.dumps(trust_policy(peer_role_arn, external_id), indent=2)
        permissions = json.dumps(
            permissions_policy(
                env_name=name,
                account_id=account,
                region=region_name,
                stage=stage,
                email_from=email_from,
                email_identity=email_identity,
            ),
            indent=2,
        )
        return (
            f"Create an IAM role in account {account} ({region_name}) before deploy.\n"
            f"The permissions below cover environment {name} and the CDK toolkit in that account.\n"
            f"\n"
            f"Role name: {role_name}\n"
            f"External id the host will send: {external_id}\n"
            f"Trusted principal: {principal_phrase(peer_role_arn)}\n"
            f"The laptop and the peer both use this same installer role.\n"
            f"\n"
            f"1. Open account {account}. This is not the account that runs the peer.\n"
            f"2. Create a role named {role_name}.\n"
            f"3. Paste the trust policy below as the role's trust relationship.\n"
            f"4. Create a customer managed policy from the permissions policy below and attach it to {role_name}.\n"
            f"5. Reply to the host operator once the role exists. Deploy can run after that.\n"
            f"\n"
            f"Trust policy:\n"
            f"{trust}\n"
            f"\n"
            f"Permissions policy:\n"
            f"{permissions}\n"
        )
    caller_account = account_from_arn(peer_role_arn)
    caller_note = f" ({caller_account})" if caller_account else ""
    sender = str(email_from or "").strip() or "the sender address saved on this installation"
    template = onboarding_template_yaml(slug)
    return (
        f"Before you deploy: upload this stack in account {account} ({region_name})\n"
        f"\n"
        f"The administrator of account {account} runs the CloudFormation template below in {region_name}. "
        f"That creates role {role_name} and the policies that limit the deployment. "
        f"They do not create those IAM roles by hand, and they do not install or run CDK.\n"
        f"\n"
        f"Who is allowed in\n"
        f"\n"
        f"- Role they will create: {role_name}\n"
        f"- Who may assume it: {peer_role_arn}\n"
        f"- External id, sent on every assume-role call: {external_id}\n"
        f"\n"
        f"Steps\n"
        f"\n"
        f"1. Sign in to account {account}. This is their account, not the host's account{caller_note}.\n"
        f"2. Open CloudFormation in {region_name} and create a stack with the template below.\n"
        f"3. Set the parameters to these values:\n"
        f"    VendorPrincipalArn: {peer_role_arn}\n"
        f"    ExternalId: {external_id}\n"
        f"    EnvironmentId: {name}\n"
        f"    EmailIdentity: {sender}\n"
        f"4. Wait until the stack is CREATE_COMPLETE, then tell the host operator. Deploy can run after that.\n"
        f"\n"
        f"Do not create {role_name} or the policies a second time in IAM.\n"
        f"\n"
        f"CloudFormation template:\n"
        f"{template}"
    )


_BOUNDARY_BLOCK = (
    "      PermissionsBoundary:\n"
    "        Fn::Sub: arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${Qualifier}-cfn-exec\n"
)

_STOCK_EXECUTION_POLICIES = (
    "      ManagedPolicyArns:\n"
    "        Fn::If:\n"
    "          - HasCloudFormationExecutionPolicies\n"
    "          - Ref: CloudFormationExecutionPolicies\n"
    "          - Fn::If:\n"
    "            - HasTrustedAccounts\n"
    "            # The CLI will prevent this case from occurring\n"
    "            - Ref: AWS::NoValue\n"
    "            # The CLI will advertise that we picked this implicitly\n"
    '            - - Fn::Sub: "arn:${AWS::Partition}:iam::aws:policy/AdministratorAccess"\n'
)

_FIXED_EXECUTION_POLICIES = (
    "      ManagedPolicyArns:\n"
    "        - Fn::Sub: arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${Qualifier}-cfn-exec\n"
    "        - Fn::Sub: arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${Qualifier}-role-guard\n"
)

_STOCK_BOUNDARY = (
    "      PermissionsBoundary:\n"
    "        Fn::If:\n"
    "          - PermissionsBoundarySet\n"
    "          - Fn::Sub: 'arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${InputPermissionsBoundary}'\n"
    "          - Ref: AWS::NoValue\n"
)

# The deploy role may change only this environment's toolkit stack and its two application stacks.
_STACK_RESOURCES = (
    "                Resource:\n"
    "                  - Fn::Sub: arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/CDKToolkit-${Qualifier}/*\n"
    "                  - Fn::Sub: arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/${Qualifier}-stack-a/*\n"
    "                  - Fn::Sub: arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/${Qualifier}-stack-b/*\n"
)

_STOCK_DEPLOY_CLOUDFORMATION = (
    "                  - cloudformation:ContinueUpdateRollback\n"
    '                Resource: "*"\n'
)

_STOCK_CLI_CLOUDFORMATION = (
    "                  - cloudformation:DeleteStack\n"
    "                  - cloudformation:UpdateTerminationProtection\n"
    "                  - sts:GetCallerIdentity\n"
    '                Resource: "*"\n'
    "                Effect: Allow\n"
)

_FIXED_CLI_CLOUDFORMATION = (
    "                  - cloudformation:DeleteStack\n"
    "                  - cloudformation:UpdateTerminationProtection\n"
    + _STACK_RESOURCES
    + "                Effect: Allow\n"
    "              - Sid: CallerIdentity\n"
    "                Action: sts:GetCallerIdentity\n"
    '                Resource: "*"\n'
    "                Effect: Allow\n"
)

_STOCK_REFACTOR = (
    "                  - cloudformation:ExecuteStackRefactor\n"
    '                Resource: "*"\n'
)

_STOCK_KMS_DEFAULT = (
    "  FileAssetsBucketKmsKeyId:\n"
    "    Description: Empty to create a new key (default), 'AWS_MANAGED_KEY' to use a managed\n"
    "      S3 key, or the ID/ARN of an existing key.\n"
    "    Default: ''\n"
)

_FIXED_KMS_DEFAULT = (
    "  FileAssetsBucketKmsKeyId:\n"
    "    Description: AWS_MANAGED_KEY uses Amazon S3 encryption (SSE-S3). A key id creates or\n"
    "      selects a customer-managed key. Deploy does not require a customer-managed key.\n"
    "    Default: AWS_MANAGED_KEY\n"
)

_STOCK_BUCKET_ENCRYPTION = (
    "              SSEAlgorithm: aws:kms\n"
    "              KMSMasterKeyID:\n"
    "                Fn::If:\n"
    "                  - CreateNewKey\n"
    '                  - Fn::Sub: "${FileAssetsBucketEncryptionKey.Arn}"\n'
    "                  - Fn::If:\n"
    "                    - UseAwsManagedKey\n"
    "                    - Ref: AWS::NoValue\n"
    '                    - Fn::Sub: "${FileAssetsBucketKmsKeyId}"\n'
)

_FIXED_BUCKET_ENCRYPTION = (
    "              SSEAlgorithm:\n"
    "                Fn::If:\n"
    "                  - UseAwsManagedKey\n"
    "                  - AES256\n"
    "                  - aws:kms\n"
    "              KMSMasterKeyID:\n"
    "                Fn::If:\n"
    "                  - UseAwsManagedKey\n"
    "                  - Ref: AWS::NoValue\n"
    "                  - Fn::If:\n"
    "                    - CreateNewKey\n"
    '                    - Fn::Sub: "${FileAssetsBucketEncryptionKey.Arn}"\n'
    '                    - Fn::Sub: "${FileAssetsBucketKmsKeyId}"\n'
)

_STOCK_PUBLISH_KMS = (
    "          - Action:\n"
    "              - kms:Decrypt\n"
    "              - kms:DescribeKey\n"
    "              - kms:Encrypt\n"
    "              - kms:ReEncrypt*\n"
    "              - kms:GenerateDataKey*\n"
    "            Effect: Allow\n"
    "            Resource:\n"
    "              Fn::If:\n"
    "                - CreateNewKey\n"
    '                - Fn::Sub: "${FileAssetsBucketEncryptionKey.Arn}"\n'
    "                - Fn::Sub: arn:${AWS::Partition}:kms:${AWS::Region}:${AWS::AccountId}:key/${FileAssetsBucketKmsKeyId}\n"
)


def bound_bootstrap_roles(template: str) -> str:
    """Pin every toolkit role to this environment's execution policy.

    The CloudFormation execution role is attached to ``{qualifier}-cfn-exec`` and
    ``{qualifier}-role-guard``. The stock AdministratorAccess fallback is removed.
    """
    if _STOCK_EXECUTION_POLICIES in template:
        template = template.replace(_STOCK_EXECUTION_POLICIES, _FIXED_EXECUTION_POLICIES, 1)
    if _STOCK_BOUNDARY in template:
        template = template.replace(_STOCK_BOUNDARY, _BOUNDARY_BLOCK, 1)
    if _STOCK_DEPLOY_CLOUDFORMATION in template:
        template = template.replace(_STOCK_DEPLOY_CLOUDFORMATION, "                  - cloudformation:ContinueUpdateRollback\n" + _STACK_RESOURCES, 1)
    if _STOCK_CLI_CLOUDFORMATION in template:
        template = template.replace(_STOCK_CLI_CLOUDFORMATION, _FIXED_CLI_CLOUDFORMATION, 1)
    if _STOCK_REFACTOR in template:
        template = template.replace(_STOCK_REFACTOR, "                  - cloudformation:ExecuteStackRefactor\n" + _STACK_RESOURCES, 1)
    if _STOCK_KMS_DEFAULT in template:
        template = template.replace(_STOCK_KMS_DEFAULT, _FIXED_KMS_DEFAULT, 1)
    if _STOCK_BUCKET_ENCRYPTION in template:
        template = template.replace(_STOCK_BUCKET_ENCRYPTION, _FIXED_BUCKET_ENCRYPTION, 1)
    if _STOCK_PUBLISH_KMS in template:
        template = template.replace(_STOCK_PUBLISH_KMS, "", 1)
    example = template.find("  CdkBoostrapPermissionsBoundaryPolicy:")
    version = template.find("  # The SSM parameter is used in pipeline-deployed templates")
    if example != -1 and version != -1 and example < version:
        template = template[:example] + template[version:]
    lines = template.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("    Type: AWS::IAM::Role"):
            block = [line]
            index += 1
            while index < len(lines) and not (
                len(lines[index]) > 2
                and lines[index].startswith("  ")
                and not lines[index].startswith("   ")
                and lines[index].rstrip().endswith(":")
            ):
                block.append(lines[index])
                index += 1
            if "PermissionsBoundary:" not in "".join(block):
                inserted = False
                for row in block:
                    if not inserted and row.startswith("      RoleName:"):
                        out.append(_BOUNDARY_BLOCK)
                        inserted = True
                    out.append(row)
                if not inserted:
                    raise RengloOpsError("CDK bootstrap role is missing RoleName")
            else:
                out.extend(block)
            continue
        out.append(line)
        index += 1
    return "".join(out)


def find_cdk_bootstrap_template() -> Path:
    roots: list[Path] = []
    cdk = shutil.which("cdk")
    if cdk:
        roots.append(Path(cdk).resolve())
    roots.extend(Path(__file__).resolve().parents)
    seen: set[Path] = set()
    for root in roots:
        for parent in (root, *root.parents):
            if parent in seen:
                continue
            seen.add(parent)
            for candidate in (
                parent / "lib" / "api" / "bootstrap" / "bootstrap-template.yaml",
                parent / "node_modules" / "aws-cdk" / "lib" / "api" / "bootstrap" / "bootstrap-template.yaml",
                parent / ".venv" / "node_modules" / "aws-cdk" / "lib" / "api" / "bootstrap" / "bootstrap-template.yaml",
            ):
                if candidate.is_file():
                    return candidate
    raise RengloOpsError("CDK bootstrap template was not found next to the cdk command")


_TEMPLATE_ENV = "envname"
_TEMPLATE_ACCOUNT = "111111111111"
_TEMPLATE_REGION = "us-east-1"
_TEMPLATE_EMAIL = "sender@example.com"


def _cfn_sub(node: Any) -> Any:
    """Turn sentinel account, region, env, and sender into CloudFormation substitutions."""
    if isinstance(node, str):
        text = (
            node.replace("arn:aws:", "arn:${AWS::Partition}:")
            .replace(_TEMPLATE_ACCOUNT, "${AWS::AccountId}")
            .replace(_TEMPLATE_REGION, "${AWS::Region}")
            .replace(_TEMPLATE_EMAIL, "${EmailIdentity}")
            .replace(_TEMPLATE_ENV, "${EnvironmentId}")
        )
        if "${" in text:
            return {"Fn::Sub": text}
        return text
    if isinstance(node, list):
        return [_cfn_sub(item) for item in node]
    if isinstance(node, dict):
        return {key: _cfn_sub(value) for key, value in node.items()}
    return node


def onboarding_template(namespace: str | None = None) -> dict[str, Any]:
    """One stack the target account uploads. It installs the same door a deploy already uses.

    The customer does not run CDK. The deploy role trusts the installer role and can
    bootstrap the toolkit for this env name. CloudFormation is capped by the
    execution policy, which is also the role's permissions boundary.
    """
    slug = normalize_namespace(namespace)
    deploy_name = deploy_role_name(slug)
    installer_name = installer_role_name(slug)
    common = {
        "env_name": _TEMPLATE_ENV,
        "account_id": _TEMPLATE_ACCOUNT,
        "region": _TEMPLATE_REGION,
    }
    execution = _cfn_sub(
        execution_policy(**common, email_from=_TEMPLATE_EMAIL, email_identity="email", namespace=slug)
    )
    runtime = _cfn_sub(runtime_policy(**common, email_from=_TEMPLATE_EMAIL, namespace=slug))
    guard = _cfn_sub(role_guard_policy(**common))
    deploy = _cfn_sub(_deploy_role_policy(**common))
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": (
            "Onboarding stack for one environment. "
            f"It creates role {deploy_name}, the execution policy used as that role's permissions boundary, "
            "the runtime policy used as the boundary on application roles, "
            f"and the deploy policy attached to {deploy_name}. "
            "Neither policy is AdministratorAccess. "
            "CloudFormation also receives the role-guard policy, so it can create an application role only with the runtime boundary "
            "and a toolkit role only with the execution boundary. "
            "Upload this stack in the target account and do not also create those policies by hand. "
            "You do not install or run CDK. "
            f"The installer assumes {deploy_name} with the external id, bootstraps a toolkit stack named CDKToolkit-{{EnvironmentId}}, "
            "and deploys the application stacks. "
            "Each environment has its own toolkit stack, so several environments can share one account and region."
        ),
        "Parameters": {
            "VendorPrincipalArn": {
                "Type": "String",
                "Default": f"arn:aws:iam::339713094352:role/{installer_name}",
                "AllowedPattern": r"^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]+$",
                "Description": "Installer role in the account that runs the deployment.",
            },
            "ExternalId": {
                "Type": "String",
                "NoEcho": True,
                "MinLength": 16,
                "Description": "External id from the installation. The installer sends it on every assume-role call.",
            },
            "EnvironmentId": {
                "Type": "String",
                "AllowedPattern": r"^[a-z][a-z0-9-]{0,9}$",
                "ConstraintDescription": "1 to 10 characters. Start with a letter. Lowercase letters, digits, and hyphens. This name is the CDK qualifier.",
                "Description": "Environment name. It is also the CDK bootstrap qualifier.",
            },
            "EmailIdentity": {
                "Type": "String",
                "MinLength": 3,
                "MaxLength": 128,
                "Description": "SES identity for this environment. An email address, or a domain when the whole domain is verified.",
            },
        },
        "Resources": {
            "ExecutionPolicy": {
                "Type": "AWS::IAM::ManagedPolicy",
                "Properties": {
                    "ManagedPolicyName": {"Fn::Sub": "${EnvironmentId}-cfn-exec"},
                    "Description": "CloudFormation execution policy and permissions boundary for toolkit roles.",
                    "PolicyDocument": execution,
                },
            },
            "RuntimePolicy": {
                "Type": "AWS::IAM::ManagedPolicy",
                "Properties": {
                    "ManagedPolicyName": {"Fn::Sub": "${EnvironmentId}-runtime"},
                    "Description": "Permissions boundary for application roles. CloudFormation must set it when creating them.",
                    "PolicyDocument": runtime,
                },
            },
            "RoleGuardPolicy": {
                "Type": "AWS::IAM::ManagedPolicy",
                "Properties": {
                    "ManagedPolicyName": {"Fn::Sub": "${EnvironmentId}-role-guard"},
                    "Description": "Requires the runtime boundary on application roles and the execution boundary on toolkit roles.",
                    "PolicyDocument": guard,
                },
            },
            "DeployPolicy": {
                "Type": "AWS::IAM::ManagedPolicy",
                "Properties": {
                    "ManagedPolicyName": {"Fn::Sub": "${EnvironmentId}-deploy"},
                    "Description": f"What {deploy_name} may do. Creating the application is left to CloudFormation.",
                    "PolicyDocument": deploy,
                },
            },
            "DeployRole": {
                "Type": "AWS::IAM::Role",
                "DependsOn": "ExecutionPolicy",
                "Properties": {
                    "RoleName": deploy_name,
                    "MaxSessionDuration": 3600,
                    "Description": "Door the installer assumes. CloudFormation deploys under the execution policy.",
                    "PermissionsBoundary": {"Ref": "ExecutionPolicy"},
                    "ManagedPolicyArns": [{"Ref": "DeployPolicy"}],
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": {"Ref": "VendorPrincipalArn"}},
                                "Action": "sts:AssumeRole",
                                "Condition": {"StringEquals": {"sts:ExternalId": {"Ref": "ExternalId"}}},
                            }
                        ],
                    },
                },
            },
        },
        "Outputs": {
            "VendorDeployRoleArn": {"Value": {"Fn::GetAtt": ["DeployRole", "Arn"]}},
            "ExecutionPolicyArn": {"Value": {"Ref": "ExecutionPolicy"}},
            "RuntimePolicyArn": {"Value": {"Ref": "RuntimePolicy"}},
            "RoleGuardPolicyArn": {"Value": {"Ref": "RoleGuardPolicy"}},
            "DeployPolicyArn": {"Value": {"Ref": "DeployPolicy"}},
        },
    }


def onboarding_template_yaml(namespace: str | None = None) -> str:
    import yaml

    return yaml.safe_dump(onboarding_template(namespace), sort_keys=False)


def bootstrap_arguments(workspace: Path, env_name: str, account_id: str) -> list[str]:
    """Flags for cdk bootstrap. A new env name uses the execution policy instead of AdministratorAccess."""
    name = str(env_name or "").strip()
    account = str(account_id or "").strip()
    if not _ENV_NAME.fullmatch(name):
        raise RengloOpsError("env name must start with a letter and use only letters, digits, and hyphens")
    if not (account.isdigit() and len(account) == 12):
        raise RengloOpsError("account id must be 12 digits")
    qualifier = cdk_qualifier(name)
    arguments = ["--qualifier", qualifier, "--bootstrap-kms-key-id", "AWS_MANAGED_KEY"]
    if not uses_named_qualifier(name):
        return arguments
    source = find_cdk_bootstrap_template()
    dest = workspace / ".renglo" / "bootstrap-template.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(bound_bootstrap_roles(source.read_text(encoding="utf-8")), encoding="utf-8")
    policy = execution_policy_name(name)
    guard = f"{name}-role-guard"
    arguments.extend(
        [
            "--template",
            str(dest),
            "--cloudformation-execution-policies",
            f"arn:aws:iam::{account}:policy/{policy},arn:aws:iam::{account}:policy/{guard}",
            "--custom-permissions-boundary",
            policy,
            "--toolkit-stack-name",
            toolkit_stack_name(name),
        ]
    )
    return arguments
