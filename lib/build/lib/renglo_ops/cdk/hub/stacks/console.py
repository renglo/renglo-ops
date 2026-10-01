"""Amplify Hosting for the console SPA (WEB platform, OIDC zip deploy)."""

from __future__ import annotations

from aws_cdk import CfnDeletionPolicy, CfnOutput, Fn
from aws_cdk import aws_amplify as amplify
from constructs import Construct

SPA_CUSTOM_RULE = amplify.CfnApp.CustomRuleProperty(
    source="/<*>",
    target="/index.html",
    status="404-200",
)


def branch_console_url(default_domain: str, branch: str, path: str = "/") -> str:
    """https://{branch}.{DefaultDomain}{path} — matches Amplify default hosting URLs."""
    return Fn.join("", ["https://", branch, ".", default_domain, path])


def _apply_delete_policy(resource: amplify.CfnApp | amplify.CfnBranch) -> None:
    resource.cfn_options.deletion_policy = CfnDeletionPolicy.DELETE
    resource.cfn_options.update_replace_policy = CfnDeletionPolicy.DELETE


class ConsoleStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        enable_staging: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        app = amplify.CfnApp(
            self,
            "ConsoleApp",
            name=f"{env_name}-console",
            platform="WEB",
            custom_rules=[SPA_CUSTOM_RULE],
        )
        _apply_delete_policy(app)

        prod_branch = amplify.CfnBranch(
            self,
            "ProductionBranch",
            app_id=app.attr_app_id,
            branch_name="production",
        )
        _apply_delete_policy(prod_branch)

        if enable_staging:
            staging_branch = amplify.CfnBranch(
                self,
                "StagingBranch",
                app_id=app.attr_app_id,
                branch_name="staging",
            )
            _apply_delete_policy(staging_branch)

        default_domain = app.attr_default_domain
        self.amplify_app_id = app.attr_app_id
        self.default_domain = default_domain
        self.production_url = branch_console_url(default_domain, "production", "/")
        self.production_callback_url = branch_console_url(default_domain, "production", "/callback")
        self.staging_url = (
            branch_console_url(default_domain, "staging", "/") if enable_staging else None
        )
        self.staging_callback_url = (
            branch_console_url(default_domain, "staging", "/callback") if enable_staging else None
        )

        CfnOutput(self, "AmplifyAppId", value=app.attr_app_id)
        CfnOutput(self, "AmplifyDefaultDomain", value=default_domain)
