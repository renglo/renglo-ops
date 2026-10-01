"""ExtensionStack: extension-declared AWS resources (indexes on platform vector bucket).

Resources come from installer/infra/cdk_extension.json. The platform owns the
vector bucket + default KB (Stack A AiStorage). Extensions may declare additional
indexes on that bucket and optional extra Knowledge Bases (never KB_ID).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aws_cdk import CfnDeletionPolicy, CfnOutput, RemovalPolicy, Stack, Tags
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from stacks.s3_vectors_kb_access import (
    add_kb_create_dependencies,
    add_kb_role_s3vectors_permissions,
    append_kb_role_to_vector_bucket_policy,
)

_DESCRIPTION = "Reglo Deployment"
_DEFAULT_VECTOR_DIM = 1024


class ExtensionStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        aws_account: str,
        extension_folder: Path,
        manifest: dict[str, Any],
        extension_config: dict[str, Any],
        attach_roles: dict[str, iam.IRole] | None = None,
        platform_vector_bucket_name: str | None = None,
        platform_vector_bucket_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        infra_dir = extension_folder / "installer" / "infra"
        policy_file = infra_dir / str(manifest["policy_file"])
        if not policy_file.is_file():
            raise FileNotFoundError(f"Extension policy document not found: {policy_file}")

        policy_name = str(manifest["policy_name"]).replace("{env}", env_name)
        policy_description = str(
            manifest.get("policy_description", "Extension actions policy")
        )
        policy_document = iam.PolicyDocument.from_json(
            json.loads(policy_file.read_text(encoding="utf-8"))
        )

        actions_policy = iam.ManagedPolicy(
            self,
            "ActionsPolicy",
            managed_policy_name=policy_name,
            document=policy_document,
            description=policy_description,
        )
        policy_cfn = actions_policy.node.default_child
        policy_cfn.cfn_options.deletion_policy = CfnDeletionPolicy.DELETE
        policy_cfn.cfn_options.update_replace_policy = CfnDeletionPolicy.DELETE

        runtime_outputs: dict[str, str] = {}
        inventory_outputs = {
            "ActionsPolicyArn": actions_policy.managed_policy_arn,
            "ActionsPolicyName": policy_name,
        }

        classic_buckets: dict[str, s3.Bucket] = {}
        for bucket_cfg in manifest.get("s3_buckets", []):
            bucket_id = str(bucket_cfg.get("id", "ExtensionBucket"))
            name_prefix = str(bucket_cfg["name_prefix"]).replace("{env}", env_name)
            bucket_name = f"{name_prefix}-{aws_account}"
            bucket = s3.Bucket(
                self,
                bucket_id,
                bucket_name=bucket_name,
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                encryption=s3.BucketEncryption.S3_MANAGED,
            )
            Tags.of(bucket).add("Description", _DESCRIPTION)

            output_key = str(bucket_cfg.get("output_var", f"{bucket_id}BucketName"))
            CfnOutput(self, f"{bucket_id}BucketName", value=bucket.bucket_name)
            CfnOutput(self, output_key, value=bucket.bucket_name)
            runtime_outputs[output_key] = bucket.bucket_name
            classic_buckets[output_key] = bucket

        index_arns: dict[str, str] = {}
        vector_bucket_name = (platform_vector_bucket_name or "").strip() or None
        vector_bucket_arn = (platform_vector_bucket_arn or "").strip() or None

        # Prefer s3_vector_indexes[] on the platform bucket. Legacy s3_vector_bucket.indexes
        # still creates indexes only (never a private vector bucket).
        index_cfgs = self._normalize_index_configs(manifest)
        if index_cfgs:
            if not vector_bucket_name or not vector_bucket_arn:
                raise ValueError(
                    "Extension declares s3_vector_indexes but Stack A platform vector "
                    "bucket name/ARN were not passed into ExtensionStack"
                )
            index_arns = self._provision_indexes_on_platform_bucket(
                vector_bucket_name=vector_bucket_name,
                index_cfgs=index_cfgs,
                runtime_outputs=runtime_outputs,
            )

        kb_cfgs = self._normalize_kb_configs(manifest)
        if kb_cfgs:
            if not vector_bucket_arn or not index_arns:
                raise ValueError(
                    "bedrock_knowledge_bases require platform vector bucket ARN and "
                    "declared indexes (vector_index_output_var must match an index output_var)"
                )
            for kb_cfg in kb_cfgs:
                self._provision_extra_bedrock_kb(
                    env_name=env_name,
                    kb_cfg=kb_cfg,
                    classic_buckets=classic_buckets,
                    vector_bucket_name=vector_bucket_name,
                    vector_bucket_arn=vector_bucket_arn,
                    index_arns=index_arns,
                    runtime_outputs=runtime_outputs,
                )

        for key, value in (manifest.get("runtime_defaults") or {}).items():
            if key in runtime_outputs:
                continue
            text = str(value).replace("{env}", env_name)
            if text:
                runtime_outputs[key] = text
                CfnOutput(self, key, value=text)

        for key, value in extension_config.items():
            if key in ("SECRETS",) or not isinstance(value, (str, int, float, bool)):
                continue
            text = str(value).replace("{env}", env_name)
            if not text or key in runtime_outputs:
                continue
            runtime_outputs[key] = text
            CfnOutput(self, key, value=text)

        role_refs = attach_roles or {}

        for role_template in manifest.get("attach_policy_to_roles", []):
            role_name = str(role_template).replace("{env}", env_name)
            role = role_refs.get(role_name)
            if role is None:
                if role_name.endswith(("-handlers-role", "-handlers-ecs-task")):
                    continue
                raise ValueError(
                    f"Extension attach_policy_to_roles: no IAM role reference for {role_name!r}. "
                    "Pass attach_roles from stack-b with concrete Role constructs."
                )
            actions_policy.attach_to_role(role)

        CfnOutput(self, "ActionsPolicyArn", value=actions_policy.managed_policy_arn)
        CfnOutput(self, "ActionsPolicyName", value=policy_name)
        CfnOutput(self, "ExtensionPath", value=extension_folder.name)

        external_handlers = str(
            extension_config.get("EXTERNAL_HANDLERS")
            or runtime_outputs.get("EXTERNAL_HANDLERS")
            or ""
        ).strip()
        # Handler catalogs stay on the peer zip. Hub/runtime outputs are membership only.
        runtime_outputs.pop("EXTERNAL_HANDLERS_HEAVY", None)
        runtime_outputs.pop("EXTERNAL_HANDLERS_ECS_HANDLERS", None)
        if external_handlers and "EXTERNAL_HANDLERS" not in runtime_outputs:
            runtime_outputs["EXTERNAL_HANDLERS"] = external_handlers
            CfnOutput(self, "EXTERNAL_HANDLERS", value=external_handlers)
        elif external_handlers:
            runtime_outputs["EXTERNAL_HANDLERS"] = external_handlers

        self.actions_policy = actions_policy
        self.runtime_outputs = runtime_outputs
        self.inventory_outputs = inventory_outputs

    @staticmethod
    def _normalize_index_configs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []
        multi = manifest.get("s3_vector_indexes")
        if isinstance(multi, list):
            for item in multi:
                if isinstance(item, dict) and item.get("enabled", True):
                    configs.append(item)
        # Legacy: indexes nested under s3_vector_bucket (bucket itself is ignored).
        legacy = manifest.get("s3_vector_bucket")
        if isinstance(legacy, dict):
            for item in legacy.get("indexes") or []:
                if isinstance(item, dict) and item.get("enabled", True):
                    configs.append(item)
        return configs

    @staticmethod
    def _normalize_kb_configs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """Optional extra KBs only — must not export platform default KB_ID."""
        configs: list[dict[str, Any]] = []
        multi = manifest.get("bedrock_knowledge_bases")
        if isinstance(multi, list):
            for item in multi:
                if isinstance(item, dict) and item.get("enabled", True):
                    configs.append(item)
        singular = manifest.get("bedrock_knowledge_base")
        if isinstance(singular, dict) and singular.get("enabled", True):
            configs.append(singular)
        return configs

    def _provision_indexes_on_platform_bucket(
        self,
        *,
        vector_bucket_name: str,
        index_cfgs: list[dict[str, Any]],
        runtime_outputs: dict[str, str],
    ) -> dict[str, str]:
        from aws_cdk import CfnResource

        index_arns: dict[str, str] = {}
        for idx_cfg in index_cfgs:
            idx_id = str(idx_cfg.get("id", "VectorIndex"))
            index_name = str(idx_cfg.get("index_name") or idx_id).lower().replace("_", "-")
            # Platform owns rag-kb; skip duplicate declaration.
            if index_name == "rag-kb":
                continue
            dimension = int(idx_cfg.get("dimension") or _DEFAULT_VECTOR_DIM)
            index = CfnResource(
                self,
                idx_id,
                type="AWS::S3Vectors::Index",
                properties={
                    "DataType": "float32",
                    "Dimension": dimension,
                    "DistanceMetric": "cosine",
                    "IndexName": index_name,
                    "VectorBucketName": vector_bucket_name,
                },
            )
            index_arn = index.get_att("IndexArn").to_string()
            idx_output = str(idx_cfg.get("output_var", f"S3_VECTORS_INDEX_{idx_id.upper()}"))
            CfnOutput(self, f"{idx_id}Name", value=index_name)
            CfnOutput(self, idx_output, value=index_name)
            CfnOutput(self, f"{idx_id}Arn", value=index_arn)
            runtime_outputs[idx_output] = index_name
            runtime_outputs[f"{idx_output}_ARN"] = index_arn
            index_arns[idx_output] = index_arn
        return index_arns

    def _provision_extra_bedrock_kb(
        self,
        *,
        env_name: str,
        kb_cfg: dict[str, Any],
        classic_buckets: dict[str, s3.Bucket],
        vector_bucket_name: str,
        vector_bucket_arn: str,
        index_arns: dict[str, str],
        runtime_outputs: dict[str, str],
    ) -> None:
        from aws_cdk import CfnResource

        kb_output = str(kb_cfg.get("kb_id_output_var") or "").strip()
        if not kb_output or kb_output == "KB_ID":
            raise ValueError(
                "Extra bedrock_knowledge_bases must set kb_id_output_var to a distinct "
                "name (not KB_ID — the platform default KB is owned by Stack A)"
            )

        docs_output_var = str(kb_cfg.get("rag_docs_output_var") or "").strip()
        docs_bucket = classic_buckets.get(docs_output_var) if docs_output_var else None
        if docs_bucket is None:
            raise ValueError(
                f"bedrock_knowledge_base.rag_docs_output_var={docs_output_var!r} "
                "must match an s3_buckets[].output_var"
            )

        index_output_var = str(kb_cfg.get("vector_index_output_var") or "").strip()
        rag_index_arn = index_arns.get(index_output_var)
        if not rag_index_arn:
            raise ValueError(
                f"bedrock_knowledge_base.vector_index_output_var={index_output_var!r} "
                "must match an s3_vector_indexes[].output_var"
            )
        index_name = runtime_outputs.get(index_output_var, index_output_var)

        stack = Stack.of(self)
        region = stack.region
        account_id = stack.account
        embedding_model_id = str(
            kb_cfg.get("embedding_model_id") or "amazon.titan-embed-text-v2:0"
        )
        embedding_model_arn = (
            f"arn:aws:bedrock:{region}::foundation-model/{embedding_model_id}"
        )
        docs_prefix = str(kb_cfg.get("rag_docs_prefix") or "rag/")
        if not docs_prefix.endswith("/"):
            docs_prefix = f"{docs_prefix}/"

        kb_id_construct = str(kb_cfg.get("id") or "ExtensionKnowledgeBase")
        kb_role = iam.Role(
            self,
            f"{kb_id_construct}Role",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Extra Bedrock KB access to docs S3 + S3 Vectors index",
        )
        docs_bucket.grant_read(kb_role)
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[embedding_model_arn],
            )
        )
        add_kb_role_s3vectors_permissions(
            kb_role,
            region=region,
            account_id=account_id,
            bucket_name=vector_bucket_name,
            index_name=index_name,
        )
        bucket_policy_grant = append_kb_role_to_vector_bucket_policy(
            self,
            f"{kb_id_construct}VectorBucketPolicyGrant",
            vector_bucket_name=vector_bucket_name,
            kb_role=kb_role,
        )

        kb_name = str(kb_cfg.get("name_prefix") or f"{env_name}-extension-kb").replace(
            "{env}", env_name
        )
        kb_description = str(
            kb_cfg.get("description") or "Extension knowledge base (S3 Vectors backend)"
        )
        knowledge_base = CfnResource(
            self,
            kb_id_construct,
            type="AWS::Bedrock::KnowledgeBase",
            properties={
                "Name": kb_name[:100],
                "Description": kb_description[:200],
                "RoleArn": kb_role.role_arn,
                "KnowledgeBaseConfiguration": {
                    "Type": "VECTOR",
                    "VectorKnowledgeBaseConfiguration": {
                        "EmbeddingModelArn": embedding_model_arn,
                    },
                },
                "StorageConfiguration": {
                    "Type": "S3_VECTORS",
                    "S3VectorsConfiguration": {
                        "VectorBucketArn": vector_bucket_arn,
                        "IndexArn": rag_index_arn,
                    },
                },
            },
        )
        add_kb_create_dependencies(
            knowledge_base,
            vector_bucket_policy_grant=bucket_policy_grant,
            kb_role=kb_role,
        )
        kb_id_attr = knowledge_base.get_att("KnowledgeBaseId").to_string()
        if kb_output in runtime_outputs:
            raise ValueError(f"Duplicate bedrock_knowledge_base kb_id_output_var={kb_output!r}")
        CfnOutput(self, f"{kb_id_construct}KbId", value=kb_id_attr)
        runtime_outputs[kb_output] = kb_id_attr

        ds_description = str(
            kb_cfg.get("data_source_description") or "S3 docs prefix for knowledge base"
        )
        data_source = CfnResource(
            self,
            f"{kb_id_construct}DataSource",
            type="AWS::Bedrock::DataSource",
            properties={
                "KnowledgeBaseId": kb_id_attr,
                "Name": f"{kb_name}-docs"[:100],
                "Description": ds_description[:200],
                "DataSourceConfiguration": {
                    "Type": "S3",
                    "S3Configuration": {
                        "BucketArn": docs_bucket.bucket_arn,
                        "InclusionPrefixes": [docs_prefix],
                    },
                },
            },
        )
        data_source.add_dependency(knowledge_base)
        ds_id = data_source.get_att("DataSourceId").to_string()
        ds_output = str(kb_cfg.get("data_source_id_output_var") or f"{kb_output}_DATA_SOURCE_ID")
        if ds_output in runtime_outputs:
            raise ValueError(
                f"Duplicate bedrock_knowledge_base data_source_id_output_var={ds_output!r}"
            )
        CfnOutput(self, f"{kb_id_construct}DataSourceId", value=ds_id)
        runtime_outputs[ds_output] = ds_id
