"""Platform webhook edge + EventBridge → /_schd/ingress.

Part of stack-b (needs AppStack REST API URL for the API Destination).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as aws_lambda
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "webhook_edge"

EVENT_SOURCE = "custom.renglo.webhook"
EVENT_DETAIL_TYPE = "WebhookReceived"


class WebhookIngressStack(Construct):
    """Edge Lambda + HTTP API + shared EventBridge Connection to /_schd/ingress."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        aws_account: str,
        aws_region: str,
        api_base_url: str,
        tenant_role_arn: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.secret = secretsmanager.Secret(
            self,
            "IngressSecret",
            secret_name=f"{env_name}/renglo/ingress-secret",
            description="Shared EventBridge → Renglo API ingress secret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=64,
                exclude_punctuation=True,
                exclude_characters="\"'\\",
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        lambda_role = iam.Role(
            self,
            "WebhookEdgeRole",
            role_name=f"{env_name}-webhook-edge-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["events:PutEvents"],
                resources=[f"arn:aws:events:{aws_region}:{aws_account}:event-bus/default"],
            )
        )
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem"],
                resources=[f"arn:aws:dynamodb:{aws_region}:{aws_account}:table/{env_name}_data"],
            )
        )

        fn = aws_lambda.Function(
            self,
            "WebhookEdgeFn",
            function_name=f"{env_name}-webhook-edge",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=aws_lambda.Code.from_asset(str(_ASSET_DIR)),
            role=lambda_role,
            timeout=Duration.seconds(15),
            memory_size=128,
            environment={
                "ENVIRONMENT": env_name,
                "DYNAMODB_DATA_TABLE": f"{env_name}_data",
                "EVENT_BUS_NAME": "default",
            },
        )

        http_api = apigwv2.HttpApi(
            self,
            "WebhookHttpApi",
            api_name=f"{env_name}-webhook-edge",
            description="Renglo native webhook edge",
            create_default_stage=True,
        )
        integration = apigwv2_integrations.HttpLambdaIntegration(
            "WebhookIntegration",
            fn,
        )
        http_api.add_routes(
            path="/{portfolio}/{org}/{channel}",
            methods=[apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
            integration=integration,
        )
        # Legacy WhatsApp Meta URL shape (channel defaults to whatsapp in Lambda)
        http_api.add_routes(
            path="/{portfolio}/{org}",
            methods=[apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
            integration=integration,
        )

        self.webhook_base_url = http_api.api_endpoint or ""
        # HttpApi.api_endpoint is a token; expose via attribute for exports
        self.webhook_api_endpoint = http_api.api_endpoint
        self.webhook_api_id = http_api.http_api_id

        connection = events.Connection(
            self,
            "IngressConnection",
            connection_name=f"{env_name}-renglo-ingress",
            description="EventBridge → Renglo universal /_schd/ingress",
            authorization=events.Authorization.api_key(
                "X-Renglo-Ingress-Secret",
                self.secret.secret_value,
            ),
        )

        # AppStack rest_url already ends with "/"; avoid str.rstrip on CDK tokens.
        ingress_url = Fn.join("", [api_base_url, "_schd/ingress"])
        destination = events.ApiDestination(
            self,
            "IngressDestination",
            api_destination_name=f"{env_name}-renglo-process",
            connection=connection,
            endpoint=ingress_url,
            http_method=events.HttpMethod.POST,
            rate_limit_per_second=20,
            description="POST webhook events to Renglo /_schd/ingress",
        )

        rule = events.CfnRule(
            self,
            "WebhookRule",
            name=f"{env_name}-renglo-webhook",
            description="Route platform webhook edge events to Renglo API ingress",
            state="ENABLED",
            event_pattern={
                "source": [EVENT_SOURCE],
                "detail-type": [EVENT_DETAIL_TYPE],
            },
            targets=[
                events.CfnRule.TargetProperty(
                    id="renglo-ingress",
                    arn=destination.api_destination_arn,
                    role_arn=tenant_role_arn,
                    http_parameters=events.CfnRule.HttpParametersProperty(
                        header_parameters={"Content-Type": "application/json"},
                    ),
                )
            ],
        )
        rule.node.add_dependency(destination)

        self.connection_name = f"{env_name}-renglo-ingress"
        self.destination_name = f"{env_name}-renglo-process"
        self.rule_name = f"{env_name}-renglo-webhook"
        self.ingress_url = ingress_url
        self.secret_arn = self.secret.secret_arn
        self.secret_name = self.secret.secret_name
        self.lambda_function_name = fn.function_name

        # Values for bootstrap / platform-vars (ARN only — fetch secret at write-local-config)
        self.runtime_outputs: dict[str, Any] = {
            "WEBHOOK_EDGE_BASE_URL": http_api.api_endpoint,
            "RENGLO_INGRESS_SECRET_ARN": self.secret.secret_arn,
            "RENGLO_INGRESS_SECRET_NAME": self.secret.secret_name or f"{env_name}/renglo/ingress-secret",
            "WEBHOOK_EDGE_FUNCTION_NAME": fn.function_name,
            "RENGLO_INGRESS_CONNECTION": self.connection_name,
            "RENGLO_INGRESS_DESTINATION": self.destination_name,
        }

        CfnOutput(self, "WebhookEdgeBaseUrl", value=http_api.api_endpoint)
        CfnOutput(self, "RengloIngressSecretArn", value=self.secret.secret_arn)
        CfnOutput(self, "RengloIngressUrl", value=ingress_url)


def export_webhook_ingress_outputs(stack: Stack, webhook: WebhookIngressStack) -> None:
    from stacks.stack_exports import _emit

    _emit(stack, "WebhookEdgeBaseUrl", webhook.webhook_api_endpoint)
    _emit(stack, "RengloIngressSecretArn", webhook.secret_arn)
    _emit(stack, "RengloIngressUrl", webhook.ingress_url)
    _emit(stack, "WebhookEdgeFunctionName", webhook.lambda_function_name)
