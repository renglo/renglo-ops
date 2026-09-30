"""Stack A (pre-seed): Cognito, storage, backend runtime (ECR, seed CodeBuild, IAM, CodeDeploy, OIDC)."""

from __future__ import annotations

from aws_cdk import CfnCondition, CfnParameter, Fn, Stack
from constructs import Construct

from stack_names import stack_a_description
from stacks.ai_storage import AiStorageStack
from stacks.auth import AuthStack
from stacks.console import ConsoleStack
from stacks.email import EmailStack
from stacks.runtime import RuntimeStack
from stacks.stack_exports import export_stack_a_outputs
from stacks.storage import StorageStack


class StackA(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        github_repo: str,
        enable_staging: bool = True,
        email_from: str = "",
        email_identity_type: str = "email",
        email_hosted_zone_id: str = "",
        github_owner_id: str | None = None,
        github_repo_id: str | None = None,
        package_registry: dict | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            scope,
            construct_id,
            description=stack_a_description(),
            **kwargs,
        )
        # Env-agnostic: unresolved tokens → AWS::AccountId / AWS::Region at deploy.
        aws_account = self.account
        aws_region = self.region
        create_oidc = CfnParameter(
            self,
            "CreateGitHubOIDC",
            type="String",
            default="false",
            allowed_values=["true", "false"],
            description=(
                "Create the GitHub Actions OIDC provider in this account. "
                "Set to true only if token.actions.githubusercontent.com is not already registered."
            ),
        )
        create_oidc_condition = CfnCondition(
            self,
            "CreateGitHubOIDCCondition",
            expression=Fn.condition_equals(create_oidc.value_as_string, "true"),
        )
        storage = StorageStack(
            self,
            "Storage",
            env_name=env_name,
            aws_account=aws_account,
            aws_region=aws_region,
        )
        console = ConsoleStack(
            self,
            "Console",
            env_name=env_name,
            enable_staging=enable_staging,
        )
        callback_urls = [console.production_url, console.production_callback_url]
        logout_urls = [console.production_url]
        if enable_staging and console.staging_url and console.staging_callback_url:
            callback_urls.extend([console.staging_url, console.staging_callback_url])
            logout_urls.append(console.staging_url)
        auth = AuthStack(
            self,
            "Auth",
            env_name=env_name,
            aws_region=aws_region,
            console_setup_base_url=console.production_url,
            console_callback_urls=callback_urls,
            console_logout_urls=logout_urls,
        )
        email = None
        ses_identity_arn = None
        if email_from.strip():
            email = EmailStack(
                self,
                "Email",
                env_name=env_name,
                email_from=email_from,
                email_identity_type=email_identity_type,
                email_hosted_zone_id=email_hosted_zone_id,
            )
            ses_identity_arn = email.email_identity_arn
        runtime = RuntimeStack(
            self,
            "Runtime",
            env_name=env_name,
            aws_account=aws_account,
            aws_region=aws_region,
            github_repo=github_repo,
            cognito_user_pool_id=auth.user_pool_id,
            s3_bucket_name=storage.bucket_name,
            enable_staging=enable_staging,
            amplify_app_id=console.amplify_app_id,
            github_owner_id=github_owner_id,
            github_repo_id=github_repo_id,
            create_github_oidc_condition=create_oidc_condition,
            ses_identity_arn=ses_identity_arn,
            package_registry=package_registry,
        )
        ai_storage = AiStorageStack(
            self,
            "AiStorage",
            env_name=env_name,
            aws_account=aws_account,
        )
        ai_storage.ai_policy.attach_to_role(runtime.tt_role)

        self.auth = auth
        self.storage = storage
        self.ai_storage = ai_storage
        self.console = console
        self.email = email
        self.runtime = runtime
        self.tt_policy = runtime.tt_policy
        self.tt_role = runtime.tt_role
        self.ai_policy = ai_storage.ai_policy
        self.from_email = email.email_from if email is not None else ""

        export_stack_a_outputs(
            self,
            auth=auth,
            storage=storage,
            console=console,
            runtime=runtime,
            email=email,
            ai_storage=ai_storage,
            env_name=env_name,
            enable_staging=enable_staging,
        )
