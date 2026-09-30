"""Stack B (post-seed): backend app, webhook, optional extension resources.

Overflow handlers compute lives on peer stacks, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

from stack_names import stack_b_description
from stacks.app import AppStack
from stacks.blueprint_uploader import BlueprintUploader
from stacks.bootstrap_config import BootstrapConfigStack
from stacks.extension import ExtensionStack
from stacks.stack_exports import (
    export_stack_b_app_outputs,
    export_stack_b_extension_outputs,
)
from stacks.webhook_ingress import WebhookIngressStack, export_webhook_ingress_outputs

_ROOT = Path(__file__).resolve().parents[1]
_release = _ROOT.parents[1] / "release"
_peer = _ROOT.parent / "peer"
for _extra in (_ROOT / "lib", _release, _peer):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from extension_actions import ExtensionActionsSpec, handle_from_extension_folder  # noqa: E402
from extension_actions_iam import attach_extension_action_specs  # noqa: E402


class StackB(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        github_repo: str,
        enable_staging: bool = True,
        architecture: str = "x86_64",
        tenant_role: iam.IRole | None = None,
        stack_a_auth: Any = None,
        stack_a_storage: Any = None,
        stack_a_console: Any = None,
        stack_a_runtime: Any = None,
        stack_a_ai_storage: Any = None,
        from_email: str = "",
        extension_folder: Path | None = None,
        extension_manifest: dict[str, Any] | None = None,
        extension_config: dict[str, Any] | None = None,
        include_extension: bool = False,
        hub_actions_specs: list[ExtensionActionsSpec] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            scope,
            construct_id,
            description=stack_b_description(include_extension=include_extension),
            **kwargs,
        )
        # Env-agnostic: unresolved tokens → AWS::AccountId / AWS::Region at deploy.
        aws_account = self.account
        aws_region = self.region
        app = AppStack(
            self,
            "App",
            env_name=env_name,
            aws_account=aws_account,
            aws_region=aws_region,
            enable_staging=enable_staging,
            architecture=architecture,
        )

        self.app = app
        extension = None

        rest_url = app.production.get("rest_url") or ""
        tt_role_arn = (
            tenant_role.role_arn
            if tenant_role is not None
            else f"arn:aws:iam::{aws_account}:role/{env_name}_tt_role"
        )
        webhook = WebhookIngressStack(
            self,
            "WebhookIngress",
            env_name=env_name,
            aws_account=aws_account,
            aws_region=aws_region,
            api_base_url=rest_url,
            tenant_role_arn=tt_role_arn,
        )
        self.webhook = webhook

        platform_vector_bucket_name = None
        platform_vector_bucket_arn = None
        if stack_a_ai_storage is not None:
            platform_vector_bucket_name = getattr(
                stack_a_ai_storage, "vector_bucket_name", None
            )
            platform_vector_bucket_arn = getattr(
                stack_a_ai_storage, "vector_bucket_arn", None
            )

        if extension_folder is not None and extension_manifest is not None:
            attach_roles: dict[str, iam.IRole] = {}
            bundled_handle = handle_from_extension_folder(extension_folder)
            hub_handles = {spec.handle for spec in (hub_actions_specs or [])}
            if tenant_role is not None and (
                not hub_handles or (bundled_handle and bundled_handle in hub_handles)
            ):
                attach_roles[f"{env_name}_tt_role"] = tenant_role

            extension = ExtensionStack(
                self,
                "Extension",
                env_name=env_name,
                aws_account=aws_account,
                extension_folder=extension_folder,
                manifest=extension_manifest,
                extension_config=extension_config or {},
                attach_roles=attach_roles,
                platform_vector_bucket_name=platform_vector_bucket_name,
                platform_vector_bucket_arn=platform_vector_bucket_arn,
            )
            self.extension = extension

        remaining_hub = [
            spec
            for spec in (hub_actions_specs or [])
            if extension_folder is None
            or spec.handle != handle_from_extension_folder(extension_folder)
        ]
        if remaining_hub and tenant_role is not None:
            attach_extension_action_specs(
                self,
                env_name=env_name,
                specs=remaining_hub,
                roles=[tenant_role],
            )

        export_stack_b_app_outputs(self, app)
        export_webhook_ingress_outputs(self, webhook)
        if extension is not None:
            export_stack_b_extension_outputs(self, extension)

        if (
            stack_a_auth is not None
            and stack_a_storage is not None
            and stack_a_console is not None
            and stack_a_runtime is not None
        ):
            BootstrapConfigStack(
                self,
                "BootstrapConfig",
                env_name=env_name,
                aws_account=aws_account,
                aws_region=aws_region,
                github_repo=github_repo,
                enable_staging=enable_staging,
                auth=stack_a_auth,
                storage=stack_a_storage,
                console=stack_a_console,
                runtime=stack_a_runtime,
                ai_storage=stack_a_ai_storage,
                app=app,
                extension=extension,
                from_email=from_email,
                webhook=webhook,
            )

        BlueprintUploader(self, "BlueprintUploader", env_name=env_name)
