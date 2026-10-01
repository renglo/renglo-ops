"""Runtime resources: Backend infrastructure — ECR, IAM, CodeDeploy, OIDC, seed CodeBuild.

Part of stack-a. A custom resource runs the seed CodeBuild project during the
stack-a deploy (build + push ``{env}_backend:seed`` to ECR), so stack-b can be
deployed right after without a manual step.
"""

from __future__ import annotations

from aws_cdk import CfnCondition, CfnOutput, CustomResource, Duration, RemovalPolicy
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as aws_lambda
from aws_cdk import aws_logs as logs
from aws_cdk import custom_resources as cr
from constructs import Construct

from lib.github_oidc import github_environment_sub_claims
from lib.package_registry import codeartifact_owners
from platform_defaults import (
    backend_ecr_repository_name,
    backend_seed_image_tag,
    docker_platform,
)

# Account-level GitHub Actions OIDC provider host (one IAM OIDC provider per account/URL).
GITHUB_OIDC_PROVIDER_ARN_SUFFIX = "token.actions.githubusercontent.com"
GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_THUMBPRINT = "6938fd4d98bab03faadb97b34396831e3780aea1"
GITHUB_OIDC_CLIENT_ID = "sts.amazonaws.com"
DESCRIPTION = "Reglo Deployment"

# Minimal Lambda container stub — inlined in CodeBuild (NO_SOURCE); no local Docker.
_SEED_DOCKERFILE = """\
FROM public.ecr.aws/lambda/python:3.12
COPY app.py ${LAMBDA_TASK_ROOT}
CMD ["app.handler"]
"""

_SEED_APP_PY = """\
def handler(event, context):
    return {
        "statusCode": 200,
        "body": "seed image ok",
    }
"""


# Custom-resource handlers (inline). on_event triggers the build; is_complete
# polls until the build finishes. The Provider framework re-invokes is_complete
# on an interval, so neither Lambda has to block for the whole build duration.
_SEED_TRIGGER_CODE = '''\
import boto3

codebuild = boto3.client("codebuild")


def on_event(event, context):
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": event.get("PhysicalResourceId", "seed-build")}
    project = event["ResourceProperties"]["ProjectName"]
    build = codebuild.start_build(projectName=project)["build"]
    return {"PhysicalResourceId": build["id"], "Data": {"BuildId": build["id"]}}


def is_complete(event, context):
    if event["RequestType"] == "Delete":
        return {"IsComplete": True}
    build_id = event["PhysicalResourceId"]
    builds = codebuild.batch_get_builds(ids=[build_id]).get("builds", [])
    if not builds:
        return {"IsComplete": False}
    status = builds[0]["buildStatus"]
    if status == "SUCCEEDED":
        return {"IsComplete": True}
    if status == "IN_PROGRESS":
        return {"IsComplete": False}
    raise Exception("Seed build %s ended with status %s" % (build_id, status))
'''


def _seed_build_spec() -> codebuild.BuildSpec:
    return codebuild.BuildSpec.from_object(
        {
            "version": "0.2",
            "phases": {
                "pre_build": {
                    "commands": [
                        "REGISTRY=$(echo \"$REPO_URI\" | cut -d/ -f1)",
                        "aws ecr get-login-password --region \"$AWS_DEFAULT_REGION\" "
                        "| docker login --username AWS --password-stdin \"$REGISTRY\"",
                        "aws ecr-public get-login-password --region us-east-1 "
                        "| docker login --username AWS --password-stdin public.ecr.aws",
                    ]
                },
                "build": {
                    "commands": [
                        "cat > Dockerfile <<'EOF'\n"
                        f"{_SEED_DOCKERFILE}"
                        "EOF",
                        "cat > app.py <<'EOF'\n"
                        f"{_SEED_APP_PY}"
                        "EOF",
                        "docker build --platform \"$DOCKER_PLATFORM\" "
                        "-t \"$REPO_URI:$IMAGE_TAG\" .",
                        "docker push \"$REPO_URI:$IMAGE_TAG\"",
                    ]
                },
            },
        }
    )


def _tt_policy_document(
    env_name: str,
    region: str,
    account: str,
    cognito_user_pool_id: str,
    s3_bucket_name: str,
    ses_identity_arn: str | None = None,
) -> iam.PolicyDocument:
    handlers_bucket = f"{env_name}-handlers-ecs-{account}"
    backend_repo_name = backend_ecr_repository_name(env_name)
    # SendRawEmail authorizes From and destination identities. Scoping the
    # resource to the From identity alone returns 403 in production while
    # local admin credentials still succeed.
    ses_resources = [f"arn:aws:ses:{region}:{account}:identity/*"]
    return iam.PolicyDocument(
        statements=[
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:{region}:{account}:function:{env_name}*",
                    f"arn:aws:lambda:{region}:{account}:function:{env_name}*:*",
                ],
            ),
            iam.PolicyStatement(
                actions=["apigateway:POST", "apigateway:GET", "apigateway:PUT", "apigateway:DELETE"],
                resources=[
                    f"arn:aws:apigateway:{region}::/restapis/*",
                    f"arn:aws:apigateway:{region}::/apis/*",
                ],
            ),
            iam.PolicyStatement(
                sid="S3ListEnvBucket",
                actions=["s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::{s3_bucket_name}",
                    f"arn:aws:s3:::{env_name}-*",
                ],
            ),
            iam.PolicyStatement(
                sid="S3ObjectsEnvBucket",
                actions=["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                resources=[
                    f"arn:aws:s3:::{s3_bucket_name}/*",
                    f"arn:aws:s3:::{env_name}-*/*",
                ],
            ),
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "logs:GetLogEvents",
                ],
                resources=[
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{env_name}*",
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{env_name}*:log-stream:*",
                ],
            ),
            iam.PolicyStatement(
                sid="EcrPullBackendImage",
                actions=[
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=[f"arn:aws:ecr:{region}:{account}:repository/{backend_repo_name}"],
            ),
            iam.PolicyStatement(
                sid="EcrAuthToken",
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                actions=[
                    "dynamodb:DescribeTable",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                ],
                resources=[
                    f"arn:aws:dynamodb:{region}:{account}:table/{env_name}_*",
                    f"arn:aws:dynamodb:{region}:{account}:table/{env_name}_*/index/*",
                ],
            ),
            iam.PolicyStatement(
                sid="SesSendEmail",
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=ses_resources,
            ),
            iam.PolicyStatement(
                actions=[
                    "cognito-idp:ListUsers",
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminDeleteUser",
                    "cognito-idp:AdminSetUserPassword",
                    "cognito-idp:AdminUpdateUserAttributes",
                    "cognito-idp:AdminInitiateAuth",
                    "cognito-idp:RespondToAuthChallenge",
                ],
                resources=[f"arn:aws:cognito-idp:{region}:{account}:userpool/{cognito_user_pool_id}"],
            ),
            iam.PolicyStatement(
                actions=[
                    "events:PutRule",
                    "events:PutTargets",
                    "events:RemoveTargets",
                    "events:DeleteRule",
                    "events:ListRules",
                    "events:DescribeRule",
                    "events:ListTargetsByRule",
                ],
                resources=[f"arn:aws:events:{region}:{account}:rule/{env_name}*"],
            ),
            iam.PolicyStatement(
                actions=["events:PutEvents"],
                resources=[f"arn:aws:events:{region}:{account}:event-bus/default"],
            ),
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[f"arn:aws:iam::{account}:role/{env_name}_tt_role"],
                conditions={"StringEquals": {"iam:PassedToService": "events.amazonaws.com"}},
            ),
            iam.PolicyStatement(
                actions=["execute-api:Invoke", "execute-api:ManageConnections"],
                resources=[
                    f"arn:aws:execute-api:{region}:{account}:*/*/POST/@connections/*",
                ],
            ),
            iam.PolicyStatement(
                actions=["events:InvokeApiDestination", "events:DescribeApiDestination"],
                resources=[f"arn:aws:events:{region}:{account}:api-destination/*"],
            ),
            iam.PolicyStatement(actions=["aoss:APIAccessAll"], resources=["*"]),
            # ECS handlers handshake (mirrors create_iam_policy._ecs_handlers_handshake_statements)
            iam.PolicyStatement(
                sid="Ec2DescribeDefaultVpc",
                actions=[
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeSecurityGroups",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="ECSRunTask",
                actions=["ecs:RunTask", "ecs:DescribeTasks", "ecs:ListTasks", "ecs:DescribeClusters"],
                resources=[
                    f"arn:aws:ecs:{region}:{account}:cluster/{env_name}-*",
                    f"arn:aws:ecs:{region}:{account}:task-definition/{env_name}-*-ecs:*",
                    f"arn:aws:ecs:{region}:{account}:task/{env_name}-*/*",
                ],
            ),
            iam.PolicyStatement(
                sid="SsmReadPeerRoutes",
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[
                    f"arn:aws:ssm:{region}:{account}:parameter/{env_name}/bootstrap/peer-routes",
                ],
            ),
            iam.PolicyStatement(
                sid="ECSPassRole",
                actions=["iam:PassRole"],
                resources=[
                    f"arn:aws:iam::{account}:role/{env_name}-handlers-ecs-execution",
                    f"arn:aws:iam::{account}:role/{env_name}-handlers-ecs-task",
                    f"arn:aws:iam::{account}:role/{env_name}-peer-*-ecs-execution",
                    f"arn:aws:iam::{account}:role/{env_name}-peer-*-ecs-task",
                ],
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            ),
            iam.PolicyStatement(
                sid="ECSHandshakeS3",
                actions=["s3:PutObject", "s3:GetObject"],
                resources=[
                    f"arn:aws:s3:::{handlers_bucket}/*",
                    f"arn:aws:s3:::{env_name}-peer-*-ecs-{account}/*",
                ],
            ),
        ]
    )


def _backend_lambda_function_name(env_name: str, stage: str) -> str:
    return f"{env_name}-backend-{stage}"


def _codeartifact_read_statements(
    region: str,
    account: str,
    package_registry: dict | None = None,
) -> list[iam.PolicyStatement]:
    """Allow the deploy role to pip/npm install from CodeArtifact.

    Same-account pull is always granted. List foreign publisher AWS accounts in
    ``package_registry.domain_owners`` (each must also list this tenant in
    ``reader_aws_accounts`` on their publisher stack).
    """
    owners = codeartifact_owners(account, package_registry)

    resources: list[str] = []
    for owner in owners:
        resources.extend(
            [
                f"arn:aws:codeartifact:{region}:{owner}:domain/*",
                f"arn:aws:codeartifact:{region}:{owner}:repository/*/*",
                f"arn:aws:codeartifact:{region}:{owner}:package/*/*/*/*",
            ]
        )
    return [
        iam.PolicyStatement(
            sid="CodeArtifactRead",
            actions=[
                "codeartifact:DescribeDomain",
                "codeartifact:GetAuthorizationToken",
                "codeartifact:GetRepositoryEndpoint",
                "codeartifact:ReadFromRepository",
                "codeartifact:DescribeRepository",
                "codeartifact:ListPackages",
                "codeartifact:ListPackageVersions",
                "codeartifact:DescribePackageVersion",
                "codeartifact:GetPackageVersionAsset",
                "codeartifact:GetPackageVersionReadme",
                "codeartifact:ListPackageVersionAssets",
                "codeartifact:ListPackageVersionDependencies",
            ],
            resources=resources,
        ),
        iam.PolicyStatement(
            sid="CodeArtifactBearerToken",
            actions=["sts:GetServiceBearerToken"],
            resources=["*"],
            conditions={
                "StringEquals": {"sts:AWSServiceName": "codeartifact.amazonaws.com"}
            },
        ),
    ]


def _deploy_permissions_policy(
    env_name: str,
    region: str,
    account: str,
    *,
    stage: str,
    amplify_app_id: str | None = None,
    package_registry: dict | None = None,
) -> iam.PolicyDocument:
    """Permissions for the releases-repo GitHub Actions OIDC deploy role."""
    ecr_repo_arn = f"arn:aws:ecr:{region}:{account}:repository/{backend_ecr_repository_name(env_name)}"
    codedeploy_app_arn = f"arn:aws:codedeploy:{region}:{account}:application:{env_name}-backend-codedeploy"
    codedeploy_group_arn = f"arn:aws:codedeploy:{region}:{account}:deploymentgroup:{env_name}-backend-codedeploy/*"
    codedeploy_config_arn = f"arn:aws:codedeploy:{region}:{account}:deploymentconfig:*"
    lambda_execution_role_arn = f"arn:aws:iam::{account}:role/{env_name}_tt_role"

    backend_lambda_arns = [
        f"arn:aws:lambda:{region}:{account}:function:{_backend_lambda_function_name(env_name, stage)}",
        f"arn:aws:lambda:{region}:{account}:function:{_backend_lambda_function_name(env_name, stage)}:*",
    ]

    statements: list[iam.PolicyStatement] = [
        iam.PolicyStatement(
            sid="ReadIdentity",
            actions=["sts:GetCallerIdentity"],
            resources=["*"],
        ),
        iam.PolicyStatement(
            sid="EcrPushPull",
            actions=[
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
            ],
            resources=[ecr_repo_arn],
        ),
        iam.PolicyStatement(
            sid="EcrAuthToken",
            actions=["ecr:GetAuthorizationToken"],
            resources=["*"],
        ),
        iam.PolicyStatement(
            sid="BackendLambdaDeploy",
            actions=[
                "lambda:CreateFunction",
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:PublishVersion",
                "lambda:CreateAlias",
                "lambda:UpdateAlias",
                "lambda:GetAlias",
                "lambda:AddPermission",
            ],
            resources=backend_lambda_arns,
        ),
        iam.PolicyStatement(
            sid="CodeDeployLambdaRelease",
            actions=[
                "codedeploy:CreateDeployment",
                "codedeploy:RegisterApplicationRevision",
                "codedeploy:GetDeployment",
                "codedeploy:GetDeploymentConfig",
                "codedeploy:GetDeploymentGroup",
                "codedeploy:GetApplication",
            ],
            resources=[codedeploy_app_arn, codedeploy_group_arn, codedeploy_config_arn],
        ),
        iam.PolicyStatement(
            sid="PassExecutionRole",
            actions=["iam:PassRole"],
            resources=[lambda_execution_role_arn],
            conditions={"StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}},
        ),
        iam.PolicyStatement(
            sid="CloudFormationDescribe",
            actions=["cloudformation:DescribeStacks"],
            resources=["*"],
        ),
        iam.PolicyStatement(
            sid="SsmReadPlatformVars",
            actions=["ssm:GetParameter", "ssm:GetParameters"],
            resources=[
                f"arn:aws:ssm:{region}:{account}:parameter/{env_name}/bootstrap/platform-vars/{stage}",
                f"arn:aws:ssm:{region}:{account}:parameter/{env_name}/bootstrap/ecs-vpc",
                f"arn:aws:ssm:{region}:{account}:parameter/{env_name}/bootstrap/ecs-subnets",
                f"arn:aws:ssm:{region}:{account}:parameter/{env_name}/bootstrap/ecs-security-groups",
            ],
        ),
    ]
    if amplify_app_id:
        statements.append(
            iam.PolicyStatement(
                sid="AmplifyConsoleDeploy",
                actions=[
                    "amplify:CreateDeployment",
                    "amplify:StartDeployment",
                    "amplify:GetJob",
                    "amplify:StopJob",
                    "amplify:GetApp",
                    "amplify:GetBranch",
                ],
                resources=[f"arn:aws:amplify:{region}:{account}:apps/{amplify_app_id}/*"],
            )
        )

    statements.extend(_codeartifact_read_statements(region, account, package_registry))
    return iam.PolicyDocument(statements=statements)


class RuntimeStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        aws_account: str,
        aws_region: str,
        github_repo: str,
        cognito_user_pool_id: str,
        s3_bucket_name: str,
        enable_staging: bool = True,
        amplify_app_id: str | None = None,
        github_owner_id: str | None = None,
        github_repo_id: str | None = None,
        create_github_oidc_condition: CfnCondition | None = None,
        ses_identity_arn: str | None = None,
        package_registry: dict | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        backend_repo_name = backend_ecr_repository_name(env_name)
        backend_repo = ecr.Repository(
            self,
            "BackendEcrRepo",
            repository_name=backend_repo_name,
            removal_policy=RemovalPolicy.DESTROY,
        )
        backend_repo.add_lifecycle_rule(max_image_count=10)

        # --- Tenant IAM policy + role ---
        tt_policy = iam.ManagedPolicy(
            self,
            "TenantPolicy",
            managed_policy_name=f"{env_name}_tt_policy",
            document=_tt_policy_document(
                env_name,
                aws_region,
                aws_account,
                cognito_user_pool_id,
                s3_bucket_name,
                ses_identity_arn=ses_identity_arn,
            ),
        )
        tt_role = iam.Role(
            self,
            "TenantRole",
            role_name=f"{env_name}_tt_role",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("lambda.amazonaws.com"),
                iam.ServicePrincipal("events.amazonaws.com"),
                iam.ServicePrincipal("apigateway.amazonaws.com"),
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                tt_policy,
            ],
            description=DESCRIPTION,
        )

        self.tt_policy = tt_policy
        self.tt_role = tt_role

        # --- ECR policy (allow Lambda to pull from the private repo after seed push) ---
        backend_repo.add_to_resource_policy(
            iam.PolicyStatement(
                sid="LambdaECRPull",
                principals=[iam.ServicePrincipal("lambda.amazonaws.com")],
                actions=[
                    "ecr:BatchGetImage",
                    "ecr:DeleteRepositoryPolicy",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:GetRepositoryPolicy",
                    "ecr:SetRepositoryPolicy",
                ],
                conditions={
                    "StringLike": {
                        "aws:sourceArn": f"arn:aws:lambda:{aws_region}:{aws_account}:function:*"
                    }
                },
            )
        )

        # --- Seed image CodeBuild (NO_SOURCE; run by custom resource below) ---
        seed_project = codebuild.Project(
            self,
            "SeedImageBuilder",
            project_name=f"{env_name}-seed-image",
            description=f"{DESCRIPTION} — build and push backend seed image",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
                compute_type=codebuild.ComputeType.SMALL,
                environment_variables={
                    "REPO_URI": codebuild.BuildEnvironmentVariable(
                        value=backend_repo.repository_uri
                    ),
                    "IMAGE_TAG": codebuild.BuildEnvironmentVariable(
                        value=backend_seed_image_tag()
                    ),
                    "DOCKER_PLATFORM": codebuild.BuildEnvironmentVariable(
                        value=docker_platform()
                    ),
                },
            ),
            build_spec=_seed_build_spec(),
        )
        backend_repo.grant_pull_push(seed_project)
        seed_project.add_to_role_policy(
            iam.PolicyStatement(
                sid="EcrPublicAuth",
                actions=[
                    "ecr-public:GetAuthorizationToken",
                    "sts:GetServiceBearerToken",
                ],
                resources=["*"],
            )
        )

        # --- Custom resource: run the seed build during stack-a deploy ---
        seed_trigger_code = aws_lambda.Code.from_inline(_SEED_TRIGGER_CODE)
        seed_on_event = aws_lambda.Function(
            self,
            "SeedBuildOnEvent",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="index.on_event",
            code=seed_trigger_code,
            timeout=Duration.minutes(2),
            description=f"{DESCRIPTION} — start seed CodeBuild",
        )
        seed_is_complete = aws_lambda.Function(
            self,
            "SeedBuildIsComplete",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="index.is_complete",
            code=seed_trigger_code,
            timeout=Duration.minutes(2),
            description=f"{DESCRIPTION} — poll seed CodeBuild status",
        )
        seed_build_permissions = iam.PolicyStatement(
            actions=["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
            resources=[seed_project.project_arn],
        )
        seed_on_event.add_to_role_policy(seed_build_permissions)
        seed_is_complete.add_to_role_policy(seed_build_permissions)

        seed_provider = cr.Provider(
            self,
            "SeedBuildProvider",
            on_event_handler=seed_on_event,
            is_complete_handler=seed_is_complete,
            query_interval=Duration.seconds(15),
            total_timeout=Duration.minutes(30),
            log_retention=logs.RetentionDays.ONE_WEEK,
        )
        seed_build = CustomResource(
            self,
            "SeedBuildRunner",
            service_token=seed_provider.service_token,
            properties={"ProjectName": seed_project.project_name},
        )
        seed_build.node.add_dependency(seed_project)

        # --- CodeDeploy ---
        cd_service_role = iam.Role(
            self,
            "CodeDeployServiceRole",
            role_name=f"{env_name}-codedeploy-lambda-role",
            assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSCodeDeployRoleForLambda"
                )
            ],
            description=DESCRIPTION,
        )
        cd_app = codedeploy.CfnApplication(
            self,
            "CodeDeployApp",
            application_name=f"{env_name}-backend-codedeploy",
            compute_platform="Lambda",
        )
        codedeploy.CfnDeploymentGroup(
            self,
            "CodeDeployGroupProduction",
            application_name=cd_app.application_name,
            deployment_group_name=f"{env_name}-backend-production",
            service_role_arn=cd_service_role.role_arn,
            deployment_config_name="CodeDeployDefault.LambdaCanary10Percent10Minutes",
            deployment_style=codedeploy.CfnDeploymentGroup.DeploymentStyleProperty(
                deployment_type="BLUE_GREEN",
                deployment_option="WITH_TRAFFIC_CONTROL",
            ),
        )
        if enable_staging:
            codedeploy.CfnDeploymentGroup(
                self,
                "CodeDeployGroupStaging",
                application_name=cd_app.application_name,
                deployment_group_name=f"{env_name}-backend-staging",
                service_role_arn=cd_service_role.role_arn,
                deployment_config_name="CodeDeployDefault.LambdaAllAtOnce",
                deployment_style=codedeploy.CfnDeploymentGroup.DeploymentStyleProperty(
                    deployment_type="BLUE_GREEN",
                    deployment_option="WITH_TRAFFIC_CONTROL",
                ),
            )

        # --- GitHub OIDC + deploy roles ---
        oidc_provider_arn = (
            f"arn:aws:iam::{aws_account}:oidc-provider/{GITHUB_OIDC_PROVIDER_ARN_SUFFIX}"
        )
        if create_github_oidc_condition is not None:
            oidc_provider_resource = iam.CfnOIDCProvider(
                self,
                "GitHubOidcProviderResource",
                url=GITHUB_OIDC_URL,
                client_id_list=[GITHUB_OIDC_CLIENT_ID],
                thumbprint_list=[GITHUB_OIDC_THUMBPRINT],
            )
            oidc_provider_resource.cfn_options.condition = create_github_oidc_condition
        oidc_provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self,
            "GitHubOidcProvider",
            open_id_connect_provider_arn=oidc_provider_arn,
        )
        def _oidc_role(stage: str) -> iam.Role:
            policy_doc = _deploy_permissions_policy(
                env_name,
                aws_region,
                aws_account,
                stage=stage,
                amplify_app_id=amplify_app_id,
                package_registry=package_registry,
            )
            return iam.Role(
                self,
                f"OidcDeployRole{stage.capitalize()}",
                role_name=f"GitHubActionsDeployRole-{env_name}-{stage}",
                assumed_by=iam.WebIdentityPrincipal(
                    oidc_provider.open_id_connect_provider_arn,
                    conditions={
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                        },
                        "StringLike": {
                            "token.actions.githubusercontent.com:sub": github_environment_sub_claims(
                                github_repo,
                                stage,
                                owner_id=github_owner_id,
                                repo_id=github_repo_id,
                            ),
                        },
                    },
                ),
                inline_policies={
                    f"GitHubActionsDeployPolicy-{env_name}-{stage}": policy_doc
                },
                description=DESCRIPTION,
            )

        prod_oidc_role = _oidc_role("production")
        staging_oidc_role = _oidc_role("staging") if enable_staging else None

        self.backend_repo = backend_repo
        self.seed_project = seed_project
        self.cd_app = cd_app
        self.oidc_provider = oidc_provider
        self.oidc_deploy_role_production = prod_oidc_role
        self.oidc_deploy_role_staging = staging_oidc_role

        # --- Outputs ---
        CfnOutput(self, "BackendEcrRepoName", value=backend_repo.repository_name)
        CfnOutput(self, "BackendEcrRepoUri", value=backend_repo.repository_uri)
        CfnOutput(self, "SeedCodeBuildProjectName", value=seed_project.project_name)
        CfnOutput(self, "TenantPolicyArn", value=tt_policy.managed_policy_arn)
        CfnOutput(self, "TenantRoleArn", value=tt_role.role_arn)
        CfnOutput(self, "CodeDeployAppName", value=cd_app.application_name)
        CfnOutput(self, "OidcProviderArn", value=oidc_provider.open_id_connect_provider_arn)
        CfnOutput(self, "OidcDeployRoleArnProduction", value=prod_oidc_role.role_arn)
        if staging_oidc_role:
            CfnOutput(self, "OidcDeployRoleArnStaging", value=staging_oidc_role.role_arn)
