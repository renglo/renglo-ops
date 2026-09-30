"""Upload launcher + extension blueprints to DynamoDB on stack-b deploy."""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CustomResource, Duration
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import custom_resources as cr
from constructs import Construct

_BLUEPRINTS_ASSET_DIR = Path(__file__).resolve().parents[1] / "bootstrap-assets" / "blueprints"

_HANDLER_CODE = """
import json
import zipfile
from io import BytesIO

import boto3


def _download_zip(bucket, key):
    s3 = boto3.client("s3")
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


def _upload_blueprints(table_name, zip_bytes):
    table = boto3.resource("dynamodb").Table(table_name)
    uploaded = []
    failed = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith(".json") or name.endswith("/"):
                continue
            irn = name.rsplit("/", 1)[-1].removesuffix(".json")
            try:
                blueprint = json.loads(zf.read(name).decode("utf-8"))
                if "irn" not in blueprint:
                    blueprint["irn"] = irn
                if "version" not in blueprint:
                    blueprint["version"] = "latest"
                table.put_item(Item=blueprint)
                uploaded.append(blueprint["irn"] + "@" + blueprint["version"])
            except Exception as exc:
                failed.append(name + ": " + str(exc))
    return {"uploaded": uploaded, "failed": failed}


def handler(event, context):
    print(json.dumps(event))
    request_type = event.get("RequestType", "")
    physical_id = event.get("PhysicalResourceId") or "blueprints"
    if request_type == "Delete":
        return {"PhysicalResourceId": physical_id}

    props = event.get("ResourceProperties", {})
    env_name = props["EnvName"]
    bucket = props["BlueprintBucket"]
    key = props["BlueprintKey"]
    table_name = env_name + "_blueprints"

    zip_bytes = _download_zip(bucket, key)
    result = _upload_blueprints(table_name, zip_bytes)
    print(json.dumps(result))
    return {
        "PhysicalResourceId": "blueprints-" + env_name,
        "Data": result,
    }
"""


class BlueprintUploader(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        blueprints_asset_dir: Path | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        asset_dir = blueprints_asset_dir or _BLUEPRINTS_ASSET_DIR
        if not asset_dir.is_dir() or not any(asset_dir.rglob("*.json")):
            return

        asset = s3_assets.Asset(self, "BlueprintsZip", path=str(asset_dir))

        on_event = lambda_.Function(
            self,
            "OnEventHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(_HANDLER_CODE),
            timeout=Duration.minutes(5),
            memory_size=256,
        )
        on_event.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[asset.bucket.arn_for_objects(asset.s3_object_key)],
            )
        )
        on_event.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:PutItem", "dynamodb:BatchWriteItem"],
                resources=[f"arn:aws:dynamodb:*:*:table/{env_name}_blueprints"],
            )
        )

        provider = cr.Provider(
            self,
            "Provider",
            on_event_handler=on_event,
        )

        CustomResource(
            self,
            "Resource",
            service_token=provider.service_token,
            properties={
                "EnvName": env_name,
                "BlueprintBucket": asset.s3_bucket_name,
                "BlueprintKey": asset.s3_object_key,
            },
        )
