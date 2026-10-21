"""Operator actions for a handler. The laptop CLI is not involved.

A session is a workspace directory plus the peer's own boto3 session. That
session is only allowed to call sts:AssumeRole. Deploy, bootstrap, and
local-config use the temporary credentials from the role the caller named.
An empty profile is rejected before any AWS call.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.tenant import (
    Account,
    Tenant,
    dump_tenant,
    load_tenant,
    tenant_from_dict,
    tenant_to_dict,
)
from renglo_ops.target_role import (
    ROLE_NAME,
    account_from_arn,
    bootstrap_arguments,
    installer_role_arn,
    is_installer_role,
    require_env_name,
    role_arn,
)

_IDENTITIES = ("email", "domain")
_LOCAL_FILES = ("env_config.py", "run.sh", ".env.development")


def _platform_version() -> str:
    try:
        from renglo_ops import __version__
    except Exception:
        return "0.1.0"
    return str(__version__ or "0.1.0")


def _require_profile(profile: str) -> str:
    name = str(profile or "").strip()
    if not name:
        raise RengloOpsError("profile is required")
    return name


def _require_account(account_id: str) -> str:
    account = str(account_id or "").strip()
    if len(account) != 12 or not account.isdigit():
        raise RengloOpsError("aws account id must be 12 digits")
    return account


def _require_repo(repo: str) -> str:
    text = str(repo or "").strip()
    owner, _, name = text.partition("/")
    if not owner.strip() or not name.strip():
        raise RengloOpsError("github repo must be ORG/REPO")
    return text


class OperatorSession:
    def __init__(self, workspace: Path, session: Any | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.session = session

    def tenant_path(self) -> Path:
        return self.workspace / "bom" / "renglo.yaml"

    def init(
        self,
        *,
        env_name: str,
        account_id: str,
        profile: str,
        region: str,
        email_from: str,
        github_repo: str,
        role: str = "staging",
        email_identity: str = "email",
        force: bool = False,
    ) -> str:
        """Write renglo.yaml. The profile is required and is not stored in the file."""
        _require_profile(profile)
        account = _require_account(account_id)
        name = require_env_name(env_name)
        sender = str(email_from or "").strip()
        if not sender:
            raise RengloOpsError("sender address is required")
        identity = str(email_identity or "email").strip() or "email"
        if identity not in _IDENTITIES:
            raise RengloOpsError("email.identity must be email or domain")
        repo = _require_repo(github_repo)
        which = str(role or "staging").strip() or "staging"
        if which not in ("staging", "production"):
            raise RengloOpsError("role must be staging or production")
        region_name = str(region or "").strip()
        if not region_name:
            raise RengloOpsError("region is required")

        path = self.tenant_path()
        current: Tenant | None = None
        if path.is_file():
            current = load_tenant(path)
            if current.name != name and not force:
                raise RengloOpsError(
                    f"{path} already describes {current.name}. Pass force to replace it"
                )
            existing = current.accounts.get(which)
            if (
                not force
                and current.name == name
                and existing
                and existing.id
                and existing.id != account
            ):
                raise RengloOpsError(
                    f"accounts.{which}.id is already set. Pass force to replace it"
                )

        accounts = {
            "staging": Account(id="", region=region_name, enabled=False),
            "production": Account(id="", region=region_name, enabled=False),
        }
        if current is not None and (current.name == name or force):
            for key, item in current.accounts.items():
                accounts[key] = Account(id=item.id, region=item.region, enabled=item.enabled)
        accounts[which] = Account(id=account, region=region_name, enabled=True)

        tenant = Tenant(
            name=name,
            github_repo=repo,
            email_from=sender,
            email_identity=identity,
            platform=_platform_version(),
            accounts=accounts,
            release_bom="0.1.0",
            release_console="0.1.0",
        )
        document = tenant_to_dict(tenant)
        tenant_from_dict(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = dump_tenant(tenant)
        path.write_text(text, encoding="utf-8")
        return text

    def show(self) -> dict[str, Any]:
        return tenant_to_dict(load_tenant(self.tenant_path()))

    def deploy(
        self,
        *,
        account_id: str,
        profile: str,
        external_id: str,
        region: str,
        dry_run: bool = False,
        stacks: str = "a,b",
    ) -> dict[str, Any]:
        name = _require_profile(profile)
        account = _require_account(account_id)
        if not str(external_id or "").strip():
            raise RengloOpsError("external id is required")
        if not self.tenant_path().is_file():
            raise RengloOpsError("renglo.yaml is missing. Run init first.")
        tenant = load_tenant(self.tenant_path())
        from renglo_ops.cdk.hub.stack_names import stack_a_id, stack_b_id

        selected = [part.strip() for part in str(stacks or "a,b").split(",") if part.strip()]
        names: list[str] = []
        for part in selected:
            if part == "a":
                names.append(stack_a_id(tenant.name))
            elif part == "b":
                names.append(stack_b_id(tenant.name))
            else:
                raise RengloOpsError("stack must be a, b, or a,b")
        if not names:
            raise RengloOpsError("stack must be a, b, or a,b")
        outdir = self.workspace / ".renglo" / "cdk.out"
        command = _cdk_deploy_command(names, outdir)
        target = role_arn(account, name)
        if dry_run:
            return {"dry_run": True, "command": command, "role_arn": target}
        creds = self.assumed_credentials(
            account_id=account,
            profile=name,
            external_id=external_id,
            region=region,
        )
        outdir.mkdir(parents=True, exist_ok=True)
        env = _cdk_env(creds, region, tenant_path=self.tenant_path(), outdir=outdir)
        proc = subprocess.run(
            command,
            env=env,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        return {
            "dry_run": False,
            "command": command,
            "role_arn": target,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def bootstrap(
        self,
        *,
        account_id: str,
        profile: str,
        external_id: str,
        region: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        name = _require_profile(profile)
        account = _require_account(account_id)
        if not str(external_id or "").strip():
            raise RengloOpsError("external id is required")
        region_name = str(region or "").strip()
        if not region_name:
            raise RengloOpsError("region is required")
        if not self.tenant_path().is_file():
            raise RengloOpsError("renglo.yaml is missing. Run init first.")
        tenant = load_tenant(self.tenant_path())
        cdk = shutil.which("cdk") or "cdk"
        command = [
            cdk,
            "bootstrap",
            *bootstrap_arguments(self.workspace, tenant.name, account),
            f"aws://{account}/{region_name}",
        ]
        target = role_arn(account, name)
        if dry_run:
            return {"dry_run": True, "command": command, "role_arn": target}
        creds = self.assumed_credentials(
            account_id=account,
            profile=name,
            external_id=external_id,
            region=region_name,
        )
        env = _cdk_env(creds, region_name, tenant_path=self.tenant_path(), outdir=None)
        proc = subprocess.run(
            command,
            env=env,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        return {
            "dry_run": False,
            "command": command,
            "role_arn": target,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def local_config(
        self,
        *,
        account_id: str,
        profile: str,
        external_id: str,
        region: str,
    ) -> dict[str, str]:
        """Return the three preview files. Never copies them into a product tree."""
        creds = self.assumed_credentials(
            account_id=account_id,
            profile=profile,
            external_id=external_id,
            region=region,
        )
        tenant = load_tenant(self.tenant_path())
        preview = self.workspace / ".renglo" / "local-dev"
        keys = list(creds) + ["AWS_PROFILE"]
        saved = {key: os.environ.get(key) for key in keys}
        os.environ.pop("AWS_PROFILE", None)
        os.environ.update(creds)
        try:
            from renglo_ops.localdev_render import run_write_local_config

            run_write_local_config(
                env_name=tenant.name,
                aws_profile=None,
                aws_region=region,
                output_dir=preview,
                dry_run=False,
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        files: dict[str, str] = {}
        for filename in _LOCAL_FILES:
            path = preview / filename
            files[filename] = path.read_text(encoding="utf-8") if path.is_file() else ""
        return files

    def stack_metrics(
        self,
        *,
        account_id: str,
        profile: str,
        external_id: str,
        region: str,
        ultrahost_role: str = "production",
    ) -> dict[str, Any]:
        creds = self.assumed_credentials(
            account_id=account_id,
            profile=profile,
            external_id=external_id,
            region=region,
        )
        tenant = load_tenant(self.tenant_path())
        from renglo_ops.cdk.hub.stack_names import stack_a_id, stack_b_id

        import boto3

        session = boto3.Session(
            aws_access_key_id=creds["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
            aws_session_token=creds["AWS_SESSION_TOKEN"],
            region_name=region,
        )
        client = session.client("cloudformation")
        stacks: dict[str, Any] = {}
        outputs: dict[str, str] = {}
        for stack_name in (stack_a_id(tenant.name), stack_b_id(tenant.name)):
            try:
                described = client.describe_stacks(StackName=stack_name)["Stacks"][0]
            except Exception as exc:
                code = ""
                response = getattr(exc, "response", None)
                if isinstance(response, dict):
                    code = str((response.get("Error") or {}).get("Code") or "")
                stacks[stack_name] = {
                    "status": "ABSENT" if code in ("ValidationError", "") else code or "ABSENT"
                }
                continue
            stack_outputs = {
                str(item.get("OutputKey")): str(item.get("OutputValue") or "")
                for item in described.get("Outputs") or []
            }
            outputs.update(stack_outputs)
            stacks[stack_name] = {"status": described.get("StackStatus") or ""}
        api_key = "RestApiUrlStaging" if ultrahost_role == "staging" else "RestApiUrlProduction"
        live = all(
            str(item.get("status") or "").endswith("_COMPLETE") and "ROLLBACK" not in str(item.get("status") or "")
            for item in stacks.values()
        ) and any(str(item.get("status") or "").endswith("COMPLETE") for item in stacks.values())
        return {
            "env_name": tenant.name,
            "stacks": stacks,
            "stack_status": "live" if live else "provisioning",
            "api_url": outputs.get(api_key) or outputs.get("RestApiUrlProduction") or "",
            "user_pool": outputs.get("UserPoolId") or "",
            "client_id": outputs.get("AppClientId") or "",
            "sender": outputs.get("FromEmail") or tenant.email_from,
        }

    def assumed_credentials(
        self,
        *,
        account_id: str,
        profile: str,
        external_id: str,
        region: str,
    ) -> dict[str, str]:
        """Assume renglo-deploy as renglo-installer. A task role or user hops there first."""
        name = _require_profile(profile)
        account = _require_account(account_id)
        external = str(external_id or "").strip()
        if not external:
            raise RengloOpsError("external id is required")
        region_name = str(region or "").strip()
        if not region_name:
            raise RengloOpsError("region is required")
        target = role_arn(account, name)
        caller = self.session
        if caller is None:
            import boto3

            caller = boto3.Session()
        sts = caller.client("sts", region_name=region_name)
        current = sts.get_caller_identity()["Arn"]
        if not is_installer_role(current):
            caller_account = account_from_arn(current)
            if not caller_account:
                raise RengloOpsError("could not read the account from the caller")
            hopped = sts.assume_role(
                RoleArn=installer_role_arn(caller_account),
                RoleSessionName="rops-installer",
            )
            creds = hopped["Credentials"]
            import boto3

            sts = boto3.client(
                "sts",
                region_name=region_name,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
        response = sts.assume_role(
            RoleArn=target,
            RoleSessionName="rops-deploy",
            ExternalId=external,
        )
        creds = response["Credentials"]
        return {
            "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
            "AWS_SESSION_TOKEN": creds["SessionToken"],
        }


def _cdk_deploy_command(stack_names: list[str], outdir: Path) -> list[str]:
    cdk = shutil.which("cdk") or "cdk"
    venv_cdk = Path(sys.executable).resolve().parent / "cdk"
    if venv_cdk.is_file():
        cdk = str(venv_cdk)
    return [
        cdk,
        "deploy",
        "--app",
        f"{shlex.quote(sys.executable)} -m renglo_ops.cdk.hub",
        "--output",
        str(outdir),
        "--require-approval",
        "never",
        *stack_names,
    ]


def _cdk_env(
    creds: dict[str, str],
    region: str,
    *,
    tenant_path: Path,
    outdir: Path | None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_DEFAULT_PROFILE", None)
    env.update(creds)
    env["AWS_REGION"] = region
    env["AWS_DEFAULT_REGION"] = region
    env["RENGLO_TENANT"] = str(tenant_path)
    env["RENGLO_CREATE_INSTALLER_ROLE"] = "false"
    env["RENGLO_WORKSPACE"] = str(tenant_path.parent.parent)
    if outdir is not None:
        env["CDK_OUTDIR"] = str(outdir)
    return env
