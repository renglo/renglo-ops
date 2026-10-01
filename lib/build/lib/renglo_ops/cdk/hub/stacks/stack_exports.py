"""Stable CloudFormation stack outputs for bootstrap config and external tooling.

Nested constructs prefix output keys (e.g. StorageDataBucketName…). Re-export
here at the Stack scope so OutputKey matches the contract (DataBucketName, …).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Stack, Token
from constructs import IConstruct


def _emit(stack: Stack, key: str, value: Any) -> None:
    out_value = value if isinstance(value, str) else Token.as_string(value)
    CfnOutput(stack, key, value=out_value)


def export_stack_a_outputs(
    stack: Stack,
    *,
    auth: IConstruct,
    storage: IConstruct,
    console: IConstruct,
    runtime: IConstruct,
    env_name: str,
    enable_staging: bool,
    email: IConstruct | None = None,
    ai_storage: IConstruct | None = None,
) -> None:
    _emit(stack, "AwsAccountId", stack.account)
    _emit(stack, "AwsRegion", stack.region)
    _emit(stack, "UserPoolId", auth.user_pool_id)
    _emit(stack, "UserPoolArn", auth.user_pool_arn)
    _emit(stack, "AppClientId", auth.app_client_id)
    _emit(stack, "CognitoDomain", auth.cognito_domain)

    _emit(stack, "DataBucketName", storage.data_bucket.bucket_name)
    _emit(stack, "DataBucketArn", storage.data_bucket.bucket_arn)
    _emit(stack, "EnvName", env_name)

    _emit(stack, "AmplifyAppId", console.amplify_app_id)
    _emit(stack, "AmplifyDefaultDomain", console.default_domain)
    _emit(stack, "AmplifyConsoleUrlProduction", console.production_url)
    if enable_staging and console.staging_url is not None:
        _emit(stack, "AmplifyConsoleUrlStaging", console.staging_url)

    if email is not None:
        _emit(stack, "FromEmail", email.email_from)
        _emit(stack, "SesIdentityName", email.email_identity_name)
        _emit(stack, "SesDnsMode", email.dns_mode)

    _emit(stack, "BackendEcrRepoName", runtime.backend_repo.repository_name)
    _emit(stack, "BackendEcrRepoUri", runtime.backend_repo.repository_uri)
    _emit(stack, "SeedCodeBuildProjectName", runtime.seed_project.project_name)
    _emit(stack, "TenantPolicyArn", runtime.tt_policy.managed_policy_arn)
    _emit(stack, "TenantRoleArn", runtime.tt_role.role_arn)
    _emit(stack, "CodeDeployAppName", runtime.cd_app.application_name)
    _emit(stack, "OidcProviderArn", runtime.oidc_provider.open_id_connect_provider_arn)
    _emit(stack, "OidcDeployRoleArnProduction", runtime.oidc_deploy_role_production.role_arn)
    if enable_staging and runtime.oidc_deploy_role_staging is not None:
        _emit(stack, "OidcDeployRoleArnStaging", runtime.oidc_deploy_role_staging.role_arn)

    if ai_storage is not None:
        for key, value in (getattr(ai_storage, "runtime_outputs", None) or {}).items():
            if value is None or (isinstance(value, str) and value == ""):
                continue
            _emit(stack, key, value)


def export_stack_b_app_outputs(stack: Stack, app: IConstruct) -> None:
    prod: dict[str, str] = app.production
    _emit(stack, "BackendLambdaFunctionNameProduction", prod["fn_name"])
    _emit(stack, "BackendLambdaAliasArnProduction", prod["alias_arn"])
    _emit(stack, "BackendLambdaLogGroupNameProduction", prod["log_group_name"])
    _emit(stack, "RestApiUrlProduction", prod["rest_url"])
    _emit(stack, "WebSocketUrlProduction", prod["ws_url"])
    _emit(stack, "WebSocketConnectionsUrlProduction", prod["ws_connections"])
    _emit(stack, "BackendLambdaArchitecture", app.architecture)
    _emit(stack, "BackendLambdaExecutionRoleArn", app.exec_role_arn)

    staging: dict[str, str] | None = app.staging
    if staging is not None:
        _emit(stack, "BackendLambdaFunctionNameStaging", staging["fn_name"])
        _emit(stack, "BackendLambdaAliasArnStaging", staging["alias_arn"])
        _emit(stack, "BackendLambdaLogGroupNameStaging", staging["log_group_name"])
        _emit(stack, "RestApiUrlStaging", staging["rest_url"])
        _emit(stack, "WebSocketUrlStaging", staging["ws_url"])
        _emit(stack, "WebSocketConnectionsUrlStaging", staging["ws_connections"])


def export_stack_b_extension_outputs(stack: Stack, extension: IConstruct) -> None:
    for key, value in (getattr(extension, "runtime_outputs", None) or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        _emit(stack, key, value)
    for key, value in (getattr(extension, "inventory_outputs", None) or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        _emit(stack, key, value)
