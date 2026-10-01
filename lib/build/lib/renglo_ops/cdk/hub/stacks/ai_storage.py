"""AiStorageStack: platform S3 Vectors + default Bedrock KB (OS amenities)."""

from __future__ import annotations

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from stacks.s3_vectors_kb_access import (
    add_kb_create_dependencies,
    add_kb_role_s3vectors_permissions,
    create_kb_vector_bucket_policy,
)

_DEFAULT_VECTOR_DIM = 1024
_DEFAULT_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
_RAG_INDEX_NAME = "rag-kb"
_RAG_DOCS_PREFIX = "rag/"


class AiStorageStack(Construct):
    """Always-on vector bucket, rag-kb index, docs bucket, and default Knowledge Base."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        aws_account: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        from aws_cdk import CfnResource

        stack = Stack.of(self)
        region = stack.region

        docs_bucket_name = f"{env_name.lower()}-rag-docs-{aws_account}"[:63].rstrip("-.")
        docs_bucket = s3.Bucket(
            self,
            "RagDocsBucket",
            bucket_name=docs_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )
        self.rag_docs_bucket = docs_bucket
        self.rag_docs_prefix = _RAG_DOCS_PREFIX

        vector_bucket_name = f"{env_name.lower()}-vectors-{aws_account}"[:63].rstrip("-.")
        vector_bucket = CfnResource(
            self,
            "PlatformVectorBucket",
            type="AWS::S3Vectors::VectorBucket",
            properties={"VectorBucketName": vector_bucket_name},
        )
        vector_bucket_arn = vector_bucket.get_att("VectorBucketArn").to_string()
        self.vector_bucket_name = vector_bucket_name
        self.vector_bucket_arn = vector_bucket_arn

        rag_index = CfnResource(
            self,
            "RagKbIndex",
            type="AWS::S3Vectors::Index",
            properties={
                "DataType": "float32",
                "Dimension": _DEFAULT_VECTOR_DIM,
                "DistanceMetric": "cosine",
                "IndexName": _RAG_INDEX_NAME,
                "VectorBucketName": vector_bucket_name,
            },
        )
        rag_index.add_dependency(vector_bucket)
        rag_index_arn = rag_index.get_att("IndexArn").to_string()
        self.rag_kb_index_name = _RAG_INDEX_NAME
        self.rag_kb_index_arn = rag_index_arn

        embedding_model_id = _DEFAULT_EMBEDDING_MODEL
        embedding_model_arn = (
            f"arn:aws:bedrock:{region}::foundation-model/{embedding_model_id}"
        )
        self.embedding_model_id = embedding_model_id

        kb_role = iam.Role(
            self,
            "PlatformKnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Platform Bedrock KB access to RAG docs S3 + S3 Vectors",
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
            account_id=aws_account,
            bucket_name=vector_bucket_name,
            index_name=_RAG_INDEX_NAME,
        )

        vector_bucket_policy = create_kb_vector_bucket_policy(
            self,
            "PlatformVectorBucketPolicy",
            vector_bucket_name=vector_bucket_name,
            vector_bucket=vector_bucket,
            kb_role_arns=[kb_role.role_arn],
            region=region,
            account_id=aws_account,
        )

        kb_name = f"{env_name}-platform-kb"
        knowledge_base = CfnResource(
            self,
            "PlatformKnowledgeBase",
            type="AWS::Bedrock::KnowledgeBase",
            properties={
                "Name": kb_name[:100],
                "Description": "Platform default knowledge base (S3 Vectors backend)",
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
            rag_index=rag_index,
            vector_bucket_policy=vector_bucket_policy,
            kb_role=kb_role,
        )
        kb_id = knowledge_base.get_att("KnowledgeBaseId").to_string()
        self.kb_id = kb_id

        data_source = CfnResource(
            self,
            "PlatformKbDataSource",
            type="AWS::Bedrock::DataSource",
            properties={
                "KnowledgeBaseId": kb_id,
                "Name": f"{kb_name}-docs"[:100],
                "Description": "Platform RAG docs prefix",
                "DataSourceConfiguration": {
                    "Type": "S3",
                    "S3Configuration": {
                        "BucketArn": docs_bucket.bucket_arn,
                        "InclusionPrefixes": [_RAG_DOCS_PREFIX],
                    },
                },
            },
        )
        data_source.add_dependency(knowledge_base)
        self.rag_data_source_id = data_source.get_att("DataSourceId").to_string()

        ai_policy = iam.ManagedPolicy(
            self,
            "PlatformAiPolicy",
            managed_policy_name=f"{env_name}_ai_tt_policy",
            description="Platform AI amenities: Bedrock embeddings/RAG + S3 Vectors + RAG docs",
            document=iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        sid="BedrockEmbeddingsAndRag",
                        actions=[
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream",
                            "bedrock:Retrieve",
                            "bedrock:RetrieveAndGenerate",
                            "bedrock:StartIngestionJob",
                            "bedrock:GetIngestionJob",
                            "bedrock:ListIngestionJobs",
                            "bedrock:GetKnowledgeBase",
                            "bedrock:ListKnowledgeBases",
                            "bedrock:GetDataSource",
                            "bedrock:ListDataSources",
                        ],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        sid="S3VectorsAccess",
                        actions=[
                            "s3vectors:PutVectors",
                            "s3vectors:GetVectors",
                            "s3vectors:DeleteVectors",
                            "s3vectors:QueryVectors",
                            "s3vectors:ListVectors",
                            "s3vectors:GetIndex",
                            "s3vectors:ListIndexes",
                            "s3vectors:CreateIndex",
                            "s3vectors:GetVectorBucket",
                            "s3vectors:ListVectorBuckets",
                        ],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        sid="RagDocsBucket",
                        actions=[
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                            "s3:AbortMultipartUpload",
                        ],
                        resources=[
                            docs_bucket.bucket_arn,
                            f"{docs_bucket.bucket_arn}/*",
                        ],
                    ),
                ]
            ),
        )
        self.ai_policy = ai_policy

        self.runtime_outputs: dict[str, str] = {
            "S3_VECTORS_BUCKET": vector_bucket_name,
            "S3_VECTORS_BUCKET_ARN": vector_bucket_arn,
            "S3_VECTORS_INDEX_RAG_KB": _RAG_INDEX_NAME,
            "RAG_DOCS_BUCKET": docs_bucket.bucket_name,
            "RAG_DOCS_PREFIX": _RAG_DOCS_PREFIX,
            "KB_ID": kb_id,
            "RAG_DATA_SOURCE_ID": self.rag_data_source_id,
            "EMBEDDING_MODEL_ID": embedding_model_id,
            "PlatformAiPolicyArn": ai_policy.managed_policy_arn,
        }

        CfnOutput(self, "S3VectorsBucketName", value=vector_bucket_name)
        CfnOutput(self, "S3VectorsBucketArn", value=vector_bucket_arn)
        CfnOutput(self, "S3VectorsIndexRagKb", value=_RAG_INDEX_NAME)
        CfnOutput(self, "RagDocsBucketName", value=docs_bucket.bucket_name)
        CfnOutput(self, "RagDocsPrefix", value=_RAG_DOCS_PREFIX)
        CfnOutput(self, "KbId", value=kb_id)
        CfnOutput(self, "RagDataSourceId", value=self.rag_data_source_id)
        CfnOutput(self, "EmbeddingModelId", value=embedding_model_id)
        CfnOutput(self, "PlatformAiPolicyArn", value=ai_policy.managed_policy_arn)
