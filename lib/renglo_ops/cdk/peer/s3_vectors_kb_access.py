"""Shared IAM + vector bucket policy helpers for Bedrock Knowledge Bases on S3 Vectors."""

from __future__ import annotations

from typing import Sequence

from aws_cdk import CfnResource, Duration
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import custom_resources as cr
from constructs import Construct, IConstruct

KB_S3VECTORS_ACTIONS = (
    "s3vectors:GetIndex",
    "s3vectors:ListIndexes",
    "s3vectors:QueryVectors",
    "s3vectors:PutVectors",
    "s3vectors:GetVectors",
    "s3vectors:ListVectors",
    "s3vectors:DeleteVectors",
)


def kb_s3vectors_resource_arns(
    *,
    region: str,
    account_id: str,
    bucket_name: str,
    index_name: str | None = None,
) -> list[str]:
    bucket_arn = f"arn:aws:s3vectors:{region}:{account_id}:bucket/{bucket_name}"
    resources = [bucket_arn, f"{bucket_arn}/*"]
    if index_name:
        resources.append(f"{bucket_arn}/index/{index_name}")
    return resources


def add_kb_role_s3vectors_permissions(
    role: iam.Role,
    *,
    region: str,
    account_id: str,
    bucket_name: str,
    index_name: str | None = None,
) -> None:
    role.add_to_policy(
        iam.PolicyStatement(
            sid="S3VectorsKnowledgeBaseAccess",
            actions=list(KB_S3VECTORS_ACTIONS),
            resources=kb_s3vectors_resource_arns(
                region=region,
                account_id=account_id,
                bucket_name=bucket_name,
                index_name=index_name,
            ),
        )
    )


def kb_vector_bucket_policy_document(
    *,
    region: str,
    account_id: str,
    bucket_name: str,
    kb_role_arns: Sequence[str],
) -> dict:
    bucket_arn = f"arn:aws:s3vectors:{region}:{account_id}:bucket/{bucket_name}"
    principals = list(dict.fromkeys(kb_role_arns))
    if len(principals) == 1:
        principal: dict = {"AWS": principals[0]}
    else:
        principal = {"AWS": principals}
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowBedrockKbRoleAccess",
                "Effect": "Allow",
                "Principal": principal,
                "Action": list(KB_S3VECTORS_ACTIONS),
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            }
        ],
    }


def create_kb_vector_bucket_policy(
    scope: Construct,
    construct_id: str,
    *,
    vector_bucket_name: str,
    vector_bucket: IConstruct,
    kb_role_arns: Sequence[str],
    region: str,
    account_id: str,
) -> CfnResource:
    """Resource-based policy required by S3 Vectors in addition to the KB role IAM policy."""
    policy = CfnResource(
        scope,
        construct_id,
        type="AWS::S3Vectors::VectorBucketPolicy",
        properties={
            "VectorBucketName": vector_bucket_name,
            "Policy": kb_vector_bucket_policy_document(
                region=region,
                account_id=account_id,
                bucket_name=vector_bucket_name,
                kb_role_arns=kb_role_arns,
            ),
        },
    )
    policy.add_dependency(vector_bucket.node.default_child or vector_bucket)
    return policy


def add_kb_create_dependencies(
    knowledge_base: CfnResource,
    *,
    rag_index: CfnResource | None = None,
    vector_bucket_policy: CfnResource | None = None,
    vector_bucket_policy_grant: cr.CustomResource | None = None,
    kb_role: iam.Role,
) -> None:
    if rag_index is not None:
        knowledge_base.add_dependency(rag_index)
    if vector_bucket_policy is not None:
        knowledge_base.add_dependency(vector_bucket_policy)
    if vector_bucket_policy_grant is not None:
        knowledge_base.node.add_dependency(vector_bucket_policy_grant)
    default_policy = kb_role.node.try_find_child("DefaultPolicy")
    if default_policy is not None:
        knowledge_base.node.add_dependency(default_policy)


_APPEND_POLICY_HANDLER = """
import json
import boto3

ACTIONS = [
    "s3vectors:GetIndex",
    "s3vectors:ListIndexes",
    "s3vectors:QueryVectors",
    "s3vectors:PutVectors",
    "s3vectors:GetVectors",
    "s3vectors:ListVectors",
    "s3vectors:DeleteVectors",
]


def _principal_arns(principal):
    if isinstance(principal, str):
        return {principal}
    if isinstance(principal, dict) and "AWS" in principal:
        aws = principal["AWS"]
        if isinstance(aws, str):
            return {aws}
        return set(aws)
    return set()


def _statement_for_role(role_arn, bucket_arn):
    sid_suffix = role_arn.split("/")[-1].replace("-", "")[:64]
    return {
        "Sid": f"AllowBedrockKbRoleAccess{sid_suffix}",
        "Effect": "Allow",
        "Principal": {"AWS": role_arn},
        "Action": ACTIONS,
        "Resource": [bucket_arn, f"{bucket_arn}/*"],
    }


def _load_policy(client, bucket_name):
    try:
        resp = client.get_vector_bucket_policy(vectorBucketName=bucket_name)
        return json.loads(resp.get("policy") or "{}")
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NotFoundException", "NoSuchBucketPolicy", "VectorBucketPolicyNotFound"):
            return {"Version": "2012-10-17", "Statement": []}
        raise


def _save_policy(client, bucket_name, policy):
    client.put_vector_bucket_policy(
        vectorBucketName=bucket_name,
        policy=json.dumps(policy),
    )


def _delete_policy(client, bucket_name):
    try:
        client.delete_vector_bucket_policy(vectorBucketName=bucket_name)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code not in ("NotFoundException", "NoSuchBucketPolicy", "VectorBucketPolicyNotFound"):
            raise


def handler(event, context):
    request_type = event["RequestType"]
    props = event["ResourceProperties"]
    bucket_name = props["VectorBucketName"]
    role_arn = props["KbRoleArn"]
    region = props["Region"]
    account_id = props["AccountId"]
    bucket_arn = f"arn:aws:s3vectors:{region}:{account_id}:bucket/{bucket_name}"
    client = boto3.client("s3vectors", region_name=region)

    if request_type == "Delete":
        policy = _load_policy(client, bucket_name)
        statements = [
            stmt
            for stmt in policy.get("Statement", [])
            if role_arn not in _principal_arns(stmt.get("Principal", {}))
        ]
        policy["Statement"] = statements
        if statements:
            _save_policy(client, bucket_name, policy)
        else:
            _delete_policy(client, bucket_name)
        return {"PhysicalResourceId": f"{bucket_name}:{role_arn}"}

    policy = _load_policy(client, bucket_name)
    statements = [
        stmt
        for stmt in policy.get("Statement", [])
        if role_arn not in _principal_arns(stmt.get("Principal", {}))
    ]
    statements.append(_statement_for_role(role_arn, bucket_arn))
    policy["Version"] = "2012-10-17"
    policy["Statement"] = statements
    _save_policy(client, bucket_name, policy)
    return {"PhysicalResourceId": f"{bucket_name}:{role_arn}"}
"""


def append_kb_role_to_vector_bucket_policy(
    scope: Construct,
    construct_id: str,
    *,
    vector_bucket_name: str,
    kb_role: iam.Role,
) -> cr.CustomResource:
    """Merge an extension KB role into the platform vector bucket policy (stack-b)."""
    from aws_cdk import Stack

    stack = Stack.of(scope)
    region = stack.region
    account_id = stack.account

    fn = lambda_.Function(
        scope,
        f"{construct_id}Fn",
        runtime=lambda_.Runtime.PYTHON_3_12,
        handler="index.handler",
        timeout=Duration.seconds(120),
        code=lambda_.Code.from_inline(_APPEND_POLICY_HANDLER),
    )
    fn.add_to_role_policy(
        iam.PolicyStatement(
            actions=[
                "s3vectors:GetVectorBucketPolicy",
                "s3vectors:PutVectorBucketPolicy",
                "s3vectors:DeleteVectorBucketPolicy",
            ],
            resources=["*"],
        )
    )

    provider = cr.Provider(
        scope,
        f"{construct_id}Provider",
        on_event_handler=fn,
    )

    return cr.CustomResource(
        scope,
        construct_id,
        service_token=provider.service_token,
        properties={
            "VectorBucketName": vector_bucket_name,
            "KbRoleArn": kb_role.role_arn,
            "Region": region,
            "AccountId": account_id,
        },
    )

