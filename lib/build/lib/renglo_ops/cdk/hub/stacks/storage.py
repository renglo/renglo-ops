"""StorageStack: S3 data bucket + 8 DynamoDB tables."""

from aws_cdk import CfnOutput, RemovalPolicy
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from constructs import Construct


_BILLING = dynamodb.BillingMode.PAY_PER_REQUEST
_S = dynamodb.AttributeType.STRING


class StorageStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        aws_account: str,
        aws_region: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Do not .lower() the whole string — account/region may be CFN tokens.
        bucket_name = f"{env_name.lower()}-{aws_account}-{aws_region}"
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            # Presigned GET redirects: <img> does not need CORS; fetch() of the
            # final S3 URL does. Objects stay private (signature still required).
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.HEAD],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
        )

        self.data_bucket = data_bucket
        self.bucket_name = bucket_name

        self._make_table(env_name, "blueprints", pk="irn", sk="version")
        self._make_table(env_name, "entities", pk="index", sk="_id")
        self._make_table(env_name, "rel", pk="index", sk="rel")
        self._make_table(env_name, "chat", pk="index", sk="entity_index")
        self._make_table(env_name, "session", pk="index", sk="entity_index")
        self._make_table(env_name, "search", pk="index", sk="search_index")
        self._make_data_table(env_name)
        self._make_graph_table(env_name)

        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "DataBucketArn", value=data_bucket.bucket_arn)
        CfnOutput(self, "EnvName", value=env_name)

    def _make_table(
        self,
        env_name: str,
        suffix: str,
        pk: str,
        sk: str | None = None,
    ) -> dynamodb.Table:
        table_name = f"{env_name}_{suffix}"
        kwargs: dict = dict(
            table_name=table_name,
            partition_key=dynamodb.Attribute(name=pk, type=_S),
            billing_mode=_BILLING,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )
        if sk:
            kwargs["sort_key"] = dynamodb.Attribute(name=sk, type=_S)
        table = dynamodb.Table(self, f"Table{suffix.capitalize()}", **kwargs)
        CfnOutput(self, f"Table{suffix.capitalize()}Arn", value=table.table_arn)
        return table

    def _make_data_table(self, env_name: str) -> dynamodb.Table:
        table = dynamodb.Table(
            self,
            "TableData",
            table_name=f"{env_name}_data",
            partition_key=dynamodb.Attribute(name="portfolio_index", type=_S),
            sort_key=dynamodb.Attribute(name="doc_index", type=_S),
            billing_mode=_BILLING,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )
        table.add_local_secondary_index(
            index_name="geo_index",
            sort_key=dynamodb.Attribute(name="geo_index", type=_S),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )
        table.add_local_secondary_index(
            index_name="path_index",
            sort_key=dynamodb.Attribute(name="path_index", type=_S),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        table.add_local_secondary_index(
            index_name="time_index",
            sort_key=dynamodb.Attribute(name="time_index", type=_S),
            projection_type=dynamodb.ProjectionType.INCLUDE,
            non_key_attributes=["path_index"],
        )
        CfnOutput(self, "TableDataArn", value=table.table_arn)
        return table

    def _make_graph_table(self, env_name: str) -> dynamodb.Table:
        table = dynamodb.Table(
            self,
            "TableGraph",
            table_name=f"{env_name}_graph",
            partition_key=dynamodb.Attribute(name="graph_index", type=_S),
            sort_key=dynamodb.Attribute(name="forward_index", type=_S),
            billing_mode=_BILLING,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )
        table.add_local_secondary_index(
            index_name="backward_index",
            sort_key=dynamodb.Attribute(name="backward_index", type=_S),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        CfnOutput(self, "TableGraphArn", value=table.table_arn)
        return table
