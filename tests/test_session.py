import json
from pathlib import Path

import pytest

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.tenant import Account, Tenant, dump_tenant, load_tenant
from renglo_ops.session import OperatorSession
from renglo_ops.target_role import (
    DEFAULT_CDK_QUALIFIER,
    ROLE_NAME,
    bound_bootstrap_roles,
    cdk_qualifier,
    execution_policy,
    onboarding_template,
    instructions,
    permissions_policy,
    principal_phrase,
    role_arn_from_caller,
    runtime_policy,
    trust_policy,
)


def _init(tmp_path: Path, **overrides):
    session = OperatorSession(tmp_path)
    fields = {
        "env_name": "acme1",
        "account_id": "123456789012",
        "profile": ROLE_NAME,
        "region": "us-east-1",
        "email_from": "noreply@acme.com",
        "github_repo": "acmeco/acme-bom",
        "role": "staging",
    }
    fields.update(overrides)
    return session, session.init(**fields)


def test_env_name_must_fit_the_cdk_qualifier(tmp_path: Path) -> None:
    with pytest.raises(RengloOpsError, match="10"):
        _init(tmp_path, env_name="arbitium1019")


def test_existing_env_keeps_the_default_qualifier(tmp_path: Path) -> None:
    assert cdk_qualifier("stanley0713") == DEFAULT_CDK_QUALIFIER
    session = OperatorSession(tmp_path)
    path = session.tenant_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        dump_tenant(
            Tenant(
                name="stanley0713",
                github_repo="acmeco/acme-bom",
                email_from="noreply@acme.com",
                email_identity="email",
                accounts={"staging": Account(id="123456789012", region="us-east-1", enabled=True)},
            )
        ),
        encoding="utf-8",
    )
    result = session.bootstrap(
        account_id="123456789012",
        profile=ROLE_NAME,
        external_id="ext-1",
        region="us-east-1",
        dry_run=True,
    )
    assert result["command"][result["command"].index("--qualifier") + 1] == DEFAULT_CDK_QUALIFIER
    assert "--cloudformation-execution-policies" not in result["command"]
    rendered = json.dumps(
        permissions_policy(env_name="stanley0713", account_id="123456789012", region="us-east-1")
    )
    assert f"cdk-{DEFAULT_CDK_QUALIFIER}-assets-123456789012-us-east-1" in rendered
    assert "cdk-stanley0713-" not in rendered


def test_runtime_policy_keeps_amplify_tagged_and_github_boundary_is_narrow() -> None:
    from renglo_ops.target_role import github_boundary_policy, role_guard_policy, runtime_policy

    document = runtime_policy(
        env_name="liga1030",
        account_id="339713094352",
        region="us-east-1",
        namespace="liga",
    )
    row = next(item for item in document["Statement"] if item.get("Action") == ["amplify:*"])
    assert row["Condition"]["StringEquals"]["aws:ResourceTag/liga:env"] == "liga1030"
    assert row["Resource"] == ["arn:aws:amplify:us-east-1:339713094352:apps/*"]
    github = github_boundary_policy(
        env_name="liga1030",
        account_id="339713094352",
        region="us-east-1",
    )
    console = next(item for item in github["Statement"] if item.get("Sid") == "AmplifyConsoleDeploy")
    assert "amplify:*" not in json.dumps(github)
    assert "Condition" not in console
    assert console["Action"] == [
        "amplify:CreateDeployment",
        "amplify:StartDeployment",
        "amplify:GetJob",
        "amplify:StopJob",
        "amplify:GetApp",
        "amplify:GetBranch",
    ]
    assert console["Resource"] == ["arn:aws:amplify:us-east-1:339713094352:apps/*"]
    assert len(json.dumps(github, separators=(",", ":"))) < 6144
    widest = github_boundary_policy(env_name="abcdefghij", account_id="982081058012", region="ap-southeast-2")
    assert len(json.dumps(widest, separators=(",", ":"))) < 6144
    guard = role_guard_policy(env_name="abcdefghij", account_id="982081058012", region="ap-southeast-2")
    assert len(json.dumps(guard, separators=(",", ":"))) < 6144
    wrong = next(item for item in guard["Statement"] if item.get("Sid") == "DenyGitHubRoleWrongBoundary")
    assert wrong["Condition"]["StringNotEquals"]["iam:PermissionsBoundary"].endswith(":policy/abcdefghij-github")


def test_execution_policy_can_tag_amplify_and_delete_the_installer_role() -> None:
    from renglo_ops.target_role import execution_policy

    document = execution_policy(
        env_name="liga1029",
        account_id="339713094352",
        region="us-east-1",
        email_from="noreply+liga@renglo.com",
        email_identity="domain",
        namespace="liga",
    )
    rendered = json.dumps(document)
    assert "arn:aws:iam::339713094352:role/liga-installer" in rendered
    assert "events!connection/liga1029-*" in rendered
    tag_statement = next(row for row in document["Statement"] if "amplify:TagResource" in row.get("Action", []))
    assert tag_statement["Resource"] == ["*"]
    assert "Condition" not in tag_statement
    branch_statement = next(row for row in document["Statement"] if row.get("Action") == ["amplify:*Branch"])
    assert branch_statement["Resource"] == ["*"]
    assert "Condition" not in branch_statement
    states_statement = next(row for row in document["Statement"] if row.get("Action") == ["states:*"])
    assert states_statement["Resource"] == ["*"]
    assert "Condition" not in states_statement
    bedrock_statement = next(row for row in document["Statement"] if row.get("Action") == ["bedrock:*"])
    assert bedrock_statement["Resource"] == ["*"]
    assert "Condition" not in bedrock_statement
    assert any(
        row.get("Action") == ["secretsmanager:GetRandomPassword"] and row.get("Resource") == ["*"]
        for row in document["Statement"]
    )
    assert any(
        row.get("Action") == ["apigateway:*"] and row.get("Resource") == ["arn:aws:apigateway:us-east-1::/tags/*"]
        for row in document["Statement"]
    )


def test_domain_identity_reuses_the_parent_domain() -> None:
    from renglo_ops.target_role import domain_identity_name, execution_policy

    assert domain_identity_name("noreply+liga@renglo.com") == "renglo.com"
    document = execution_policy(
        env_name="liga1029",
        account_id="339713094352",
        region="us-east-1",
        email_from="noreply+liga@renglo.com",
        email_identity="domain",
    )
    rendered = json.dumps(document)
    assert "identity/renglo.com" in rendered
    assert "identity/liga1029.renglo.com" not in rendered


def test_bootstrap_creates_the_policies_it_attaches() -> None:
    from renglo_ops.target_role import ensure_bootstrap_policies, failed_toolkit_status

    class Missing(Exception):
        def __init__(self) -> None:
            super().__init__("policy does not exist")
            self.response = {"Error": {"Code": "NoSuchEntity"}}

    class FakeIam:
        def __init__(self) -> None:
            self.policies: dict[str, dict] = {}

        def get_policy(self, *, PolicyArn: str) -> dict:
            name = PolicyArn.rsplit("/", 1)[-1]
            if name not in self.policies:
                raise Missing()
            return {"Policy": {"Arn": PolicyArn, "DefaultVersionId": "v1"}}

        def get_policy_version(self, *, PolicyArn: str, VersionId: str) -> dict:
            name = PolicyArn.rsplit("/", 1)[-1]
            return {"PolicyVersion": {"Document": self.policies[name], "VersionId": VersionId}}

        def create_policy(self, *, PolicyName: str, PolicyDocument: str, Description: str) -> dict:
            self.policies[PolicyName] = json.loads(PolicyDocument)
            return {"Policy": {"Arn": f"arn:aws:iam::123456789012:policy/{PolicyName}"}}

        def list_policy_versions(self, *, PolicyArn: str) -> dict:
            raise AssertionError("unchanged policy should not be versioned")

        def create_policy_version(self, **kwargs) -> dict:
            raise AssertionError("unchanged policy should not be versioned")

    iam = FakeIam()
    first = ensure_bootstrap_policies(
        iam,
        env_name="acme1",
        account_id="123456789012",
        region="us-east-1",
        email_from="noreply@acme.com",
    )
    assert first == ["created acme1-cfn-exec", "created acme1-role-guard", "created acme1-runtime", "created acme1-github"]
    second = ensure_bootstrap_policies(
        iam,
        env_name="acme1",
        account_id="123456789012",
        region="us-east-1",
        email_from="noreply@acme.com",
    )
    assert second == ["unchanged acme1-cfn-exec", "unchanged acme1-role-guard", "unchanged acme1-runtime", "unchanged acme1-github"]
    assert failed_toolkit_status("ROLLBACK_COMPLETE")
    assert not failed_toolkit_status("CREATE_COMPLETE")


def test_bootstrap_uses_the_env_name_as_qualifier(tmp_path: Path) -> None:
    session, _text = _init(tmp_path)
    result = session.bootstrap(
        account_id="123456789012",
        profile=ROLE_NAME,
        external_id="ext-1",
        region="us-east-1",
        dry_run=True,
    )
    assert result["command"][result["command"].index("--qualifier") + 1] == "acme1"
    assert (
        result["command"][result["command"].index("--cloudformation-execution-policies") + 1]
        == "arn:aws:iam::123456789012:policy/acme1-cfn-exec,arn:aws:iam::123456789012:policy/acme1-role-guard"
    )
    assert result["command"][result["command"].index("--custom-permissions-boundary") + 1] == "acme1-cfn-exec"
    assert result["command"][result["command"].index("--toolkit-stack-name") + 1] == "CDKToolkit-acme1"
    template = Path(result["command"][result["command"].index("--template") + 1]).read_text()
    assert template.count("PermissionsBoundary:") >= 5
    assert "policy/${Qualifier}-cfn-exec" in template
    assert "policy/${Qualifier}-role-guard" in template
    assert "AdministratorAccess" not in template
    assert "${CloudFormationExecutionRole.Arn}" in template
    resources = template.split("Resources:", 1)[1]
    assert "InputPermissionsBoundary" not in resources
    assert "ExplicitAllowAll" not in template
    assert "stack/CDKToolkit-${Qualifier}/*" in template
    assert "stack/${Qualifier}-stack-a/*" in template
    assert "stack/${Qualifier}-stack-b/*" in template
    deploy_role = template.split("DeploymentActionRole:", 1)[1].split("CloudFormationExecutionRole:", 1)[0]
    assert "cloudformation:CreateStack" in deploy_role
    assert 'Resource: "*"' not in deploy_role.split("Sid: DeployPermissions", 1)[1].split("Sid: Pipeline", 1)[0]
    assert "Default: AWS_MANAGED_KEY" in template
    assert "AES256" in template
    assert "key/${FileAssetsBucketKmsKeyId}" not in template


def test_init_writes_yaml_without_a_profile_field(tmp_path: Path) -> None:
    session, text = _init(tmp_path)
    tenant = load_tenant(session.tenant_path())
    assert tenant.name == "acme1"
    assert tenant.accounts["staging"].id == "123456789012"
    assert tenant.accounts["staging"].enabled is True
    assert tenant.accounts["production"].enabled is False
    assert "profile" not in text
    assert "renglo-deploy" not in text
    assert tenant.namespace == "renglo"
    assert "namespace: renglo" in text


def test_init_can_put_the_same_account_on_both_stages(tmp_path: Path) -> None:
    session, _text = _init(tmp_path, both=True)
    tenant = load_tenant(session.tenant_path())
    assert tenant.accounts["staging"].id == "123456789012"
    assert tenant.accounts["staging"].enabled is True
    assert tenant.accounts["production"].id == "123456789012"
    assert tenant.accounts["production"].enabled is True
    assert tenant.accounts["staging"].region == tenant.accounts["production"].region


def test_init_keeps_an_existing_account_until_force(tmp_path: Path) -> None:
    session, _text = _init(tmp_path)
    with pytest.raises(RengloOpsError):
        session.init(
            env_name="acme1",
            account_id="999999999999",
            profile=ROLE_NAME,
            region="us-east-1",
            email_from="noreply@acme.com",
            github_repo="acmeco/acme-bom",
            role="staging",
        )
    session.init(
        env_name="acme1",
        account_id="210987654321",
        profile=ROLE_NAME,
        region="us-west-2",
        email_from="noreply@acme.com",
        github_repo="acmeco/acme-bom",
        role="production",
    )
    tenant = load_tenant(session.tenant_path())
    assert tenant.accounts["staging"].id == "123456789012"
    assert tenant.accounts["production"].id == "210987654321"


def test_empty_profile_is_rejected_before_aws(tmp_path: Path) -> None:
    session = OperatorSession(tmp_path)
    with pytest.raises(RengloOpsError, match="profile is required"):
        session.init(
            env_name="acme1",
            account_id="123456789012",
            profile="",
            region="us-east-1",
            email_from="noreply@acme.com",
            github_repo="acmeco/acme-bom",
        )
    with pytest.raises(RengloOpsError, match="profile is required"):
        session.deploy(
            account_id="123456789012",
            profile="  ",
            external_id="ext",
            region="us-east-1",
            dry_run=True,
        )


def test_deploy_dry_run_uses_approval_never(tmp_path: Path) -> None:
    session, _text = _init(tmp_path)
    result = session.deploy(
        account_id="123456789012",
        profile=ROLE_NAME,
        external_id="ext-1",
        region="us-east-1",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert "--require-approval" in result["command"]
    assert result["command"][result["command"].index("--require-approval") + 1] == "never"
    assert result["role_arn"] == "arn:aws:iam::123456789012:role/renglo-deploy"
    assert "acme1-stack-a" in result["command"]
    assert "acme1-stack-b" in result["command"]


def test_trust_policy_names_the_peer_and_external_id() -> None:
    caller = "arn:aws:sts::111122223333:assumed-role/host-peer-ops-ecs-task/session"
    peer = role_arn_from_caller(caller)
    assert peer == "arn:aws:iam::111122223333:role/host-peer-ops-ecs-task"
    user = "arn:aws:iam::339713094352:user/tt-installer"
    assert role_arn_from_caller(user) == user
    assert principal_phrase(user) == f"IAM user {user}"
    assert principal_phrase(peer) == f"IAM role {peer}"
    document = trust_policy(peer, "external-1")
    statement = document["Statement"][0]
    assert statement["Principal"]["AWS"] == peer
    assert statement["Condition"]["StringEquals"]["sts:ExternalId"] == "external-1"
    document = permissions_policy(
        env_name="acme1",
        account_id="123456789012",
        region="us-east-1",
        email_from="noreply@acme.com",
    )
    rendered = json.dumps(document)
    assert "arn:aws:iam::123456789012:policy/acme1-cfn-exec" in rendered
    assert "cdk-acme1-deploy-role-123456789012-us-east-1" in rendered
    assert not any(
        row.get("Effect") == "Allow" and "AdministratorAccess" in json.dumps(row.get("Resource"))
        for row in document["Statement"]
    )
    assert "arn:aws:s3:::acme1-123456789012-us-east-1" not in rendered
    cap = execution_policy(
        env_name="acme1",
        account_id="123456789012",
        region="us-east-1",
        email_from="noreply@acme.com",
    )
    cap_text = json.dumps(cap, separators=(",", ":"))
    assert len(cap_text) < 6144
    assert "arn:aws:cloudformation:us-east-1:123456789012:stack/acme1-*" in cap_text
    assert "arn:aws:cloudformation:us-east-1:123456789012:stack/CDKToolkit-acme1/*" in cap_text
    assert "arn:aws:dynamodb:us-east-1:123456789012:table/acme1_*" in cap_text
    assert "arn:aws:s3:::acme1-123456789012-us-east-1" in cap_text
    assert "arn:aws:s3:::acme1-rag-docs-123456789012" in cap_text
    assert "cdk-acme1-assets-123456789012-us-east-1" in cap_text
    assert "/cdk-bootstrap/acme1/version" in cap_text
    assert "hnb659fds" not in cap_text
    assert "arn:aws:ses:us-east-1:123456789012:identity/noreply@acme.com" in cap_text
    assert "s3:*" in cap_text
    assert "key/*" not in cap_text
    denied = next(row for row in cap["Statement"] if row.get("Sid") == "DenyAdministratorAccess")
    assert denied["Effect"] == "Deny"
    assert denied["Resource"] == "arn:aws:iam::aws:policy/AdministratorAccess"
    roles = next(row for row in cap["Statement"] if row.get("Sid") == "Roles")
    assert "acme1-cfn-exec" not in json.dumps(roles["Resource"])
    assert "arn:aws:iam::123456789012:policy/acme1_*" in roles["Resource"]
    unnamed = next(row for row in document["Statement"] if row.get("Sid") == "ActionsWithoutAName")
    assert unnamed["Action"] == [
        "cloudformation:ListStacks",
        "cloudformation:GetTemplateSummary",
        "cloudformation:ValidateTemplate",
        "ecr:GetAuthorizationToken",
    ]
    text = instructions(
        peer_role_arn=peer,
        external_id="external-1",
        env_name="acme1",
        account_id="123456789012",
        region="us-east-1",
    )
    assert "Before you deploy" in text
    assert "upload this stack" in text
    assert "not the host's account (111122223333)" in text
    assert "renglo-deploy" in text
    assert "AWSTemplateFormatVersion" in text
    assert "EnvironmentId: acme1" in text
    assert peer in text
    assert "external-1" in text


def test_bootstrap_template_bounds_every_role() -> None:
    template = """
  FilePublishingRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName:
        Fn::Sub: cdk-${Qualifier}-file-publishing-role-${AWS::AccountId}-${AWS::Region}
  CloudFormationExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName:
        Fn::Sub: cdk-${Qualifier}-cfn-exec-role-${AWS::AccountId}-${AWS::Region}
      PermissionsBoundary:
        Ref: AWS::NoValue
"""
    rendered = bound_bootstrap_roles(template)
    assert rendered.count("PermissionsBoundary:") == 2


def test_a_task_role_assumes_renglo_installer_before_renglo_deploy(monkeypatch) -> None:
    calls: list[dict] = []

    class _Sts:
        def get_caller_identity(self):
            return {"Arn": "arn:aws:sts::339713094352:assumed-role/stanley0731-peer-ops-ecs-task/session"}

        def assume_role(self, **kwargs):
            calls.append(kwargs)
            return {
                "Credentials": {
                    "AccessKeyId": "ak",
                    "SecretAccessKey": "sk",
                    "SessionToken": "tok",
                }
            }

    class _Session:
        def client(self, service, region_name=None):
            return _Sts()

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: _Sts())
    creds = OperatorSession(Path("."), session=_Session()).assumed_credentials(
        account_id="123456789012",
        profile=ROLE_NAME,
        external_id="ext-1",
        region="us-east-1",
    )
    assert creds["AWS_ACCESS_KEY_ID"] == "ak"
    assert calls[0]["RoleArn"] == "arn:aws:iam::339713094352:role/renglo-installer"
    assert "ExternalId" not in calls[0]
    assert calls[1]["RoleArn"] == "arn:aws:iam::123456789012:role/renglo-deploy"
    assert calls[1]["ExternalId"] == "ext-1"


def test_a_caller_that_is_already_renglo_installer_assumes_the_target_once(monkeypatch) -> None:
    calls: list[dict] = []

    class _Sts:
        def get_caller_identity(self):
            return {"Arn": "arn:aws:sts::339713094352:assumed-role/renglo-installer/laptop"}

        def assume_role(self, **kwargs):
            calls.append(kwargs)
            return {
                "Credentials": {
                    "AccessKeyId": "ak",
                    "SecretAccessKey": "sk",
                    "SessionToken": "tok",
                }
            }

    class _Session:
        def client(self, service, region_name=None):
            return _Sts()

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: _Sts())
    OperatorSession(Path("."), session=_Session()).assumed_credentials(
        account_id="123456789012",
        profile=ROLE_NAME,
        external_id="ext-1",
        region="us-east-1",
    )
    assert len(calls) == 1
    assert calls[0]["RoleArn"] == "arn:aws:iam::123456789012:role/renglo-deploy"


def _resolve_template(node, *, env: str, account: str, region: str, email: str):
    if isinstance(node, dict) and set(node) == {"Fn::Sub"}:
        text = str(node["Fn::Sub"])
        text = text.replace("${AWS::Partition}", "aws")
        text = text.replace("${AWS::AccountId}", account)
        text = text.replace("${AWS::Region}", region)
        text = text.replace("${EnvironmentId}", env)
        text = text.replace("${EmailIdentity}", email)
        return text
    if isinstance(node, list):
        return [_resolve_template(item, env=env, account=account, region=region, email=email) for item in node]
    if isinstance(node, dict):
        return {
            key: _resolve_template(value, env=env, account=account, region=region, email=email)
            for key, value in node.items()
        }
    return node


def test_onboarding_template_uses_the_tenant_namespace() -> None:
    document = onboarding_template("arbitium")
    text = json.dumps(document)
    assert document["Resources"]["DeployRole"]["Properties"]["RoleName"] == "arbitium-deploy"
    assert "arbitium:env" in text
    assert "role/arbitium-installer" in text
    assert "renglo-deploy" not in text
    assert "renglo-installer" not in text
    assert "renglo:env" not in text


def test_onboarding_template_is_the_deploy_door() -> None:
    document = onboarding_template()
    assert "RuntimePolicy" in document["Resources"]
    assert document["Resources"]["RuntimePolicy"]["Properties"]["ManagedPolicyName"] == {
        "Fn::Sub": "${EnvironmentId}-runtime"
    }
    assert document["Resources"]["DeployRole"]["Properties"]["RoleName"] == "renglo-deploy"
    assert document["Parameters"]["EnvironmentId"]["AllowedPattern"] == r"^[a-z][a-z0-9-]{0,9}$"
    trust = document["Resources"]["DeployRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"]["AWS"] == {"Ref": "VendorPrincipalArn"}
    assert trust["Condition"]["StringEquals"]["sts:ExternalId"] == {"Ref": "ExternalId"}
    assert document["Resources"]["DeployRole"]["Properties"]["PermissionsBoundary"] == {"Ref": "ExecutionPolicy"}
    env, account, region, email = "arbtm1019", "982081058012", "us-east-1", "noreply@example.com"
    execution = _resolve_template(
        document["Resources"]["ExecutionPolicy"]["Properties"]["PolicyDocument"],
        env=env,
        account=account,
        region=region,
        email=email,
    )
    deploy = _resolve_template(
        document["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"],
        env=env,
        account=account,
        region=region,
        email=email,
    )
    assert execution == execution_policy(
        env_name=env,
        account_id=account,
        region=region,
        email_from=email,
    )
    assert len(json.dumps(execution, separators=(",", ":"))) < 6144
    widest = execution_policy(
        env_name="abcdefghij",
        account_id=account,
        region=region,
        email_from="a" * 64 + "@example.com",
    )
    assert len(json.dumps(widest, separators=(",", ":"))) < 6144
    rendered = json.dumps(deploy)
    assert len(json.dumps(deploy, separators=(",", ":"))) < 6144
    widest_deploy = permissions_policy(env_name="abcdefghij", account_id=account, region="ap-southeast-2")
    assert len(json.dumps(widest_deploy, separators=(",", ":"))) < 6144
    assert "sts:AssumeRole" in rendered
    assert "cdk-arbtm1019-deploy-role-982081058012-us-east-1" in rendered
    create = next(row for row in deploy["Statement"] if row.get("Sid") == "ToolkitRoleCreate")
    assert create["Condition"]["StringEquals"]["iam:PermissionsBoundary"].endswith(":policy/arbtm1019-cfn-exec")
    modify = next(row for row in deploy["Statement"] if row.get("Sid") == "ToolkitRoleModify")
    assert "Condition" not in modify
    assert "iam:TagRole" in modify["Action"]
    guard = _resolve_template(
        document["Resources"]["RoleGuardPolicy"]["Properties"]["PolicyDocument"],
        env=env,
        account=account,
        region=region,
        email=email,
    )
    assert any(row.get("Sid") == "DenyApplicationRoleWithoutBoundary" for row in guard["Statement"])
    assert any(row.get("Sid") == "DenyToolkitRoleWrongBoundary" for row in guard["Statement"])
    runtime = _resolve_template(
        document["Resources"]["RuntimePolicy"]["Properties"]["PolicyDocument"],
        env=env,
        account=account,
        region=region,
        email=email,
    )
    assert len(json.dumps(runtime, separators=(",", ":"))) < 6144
    widest_runtime = runtime_policy(
        env_name="abcdefghij",
        account_id=account,
        region=region,
        email_from="a" * 64 + "@example.com",
    )
    assert len(json.dumps(widest_runtime, separators=(",", ":"))) < 6144
    runtime_text = json.dumps(runtime)
    assert "states:StartExecution" in runtime_text
    assert "ecr-public:GetAuthorizationToken" in runtime_text
    assert "sts:GetServiceBearerToken" in runtime_text
    assert "ses:SendRawEmail" in runtime_text
    assert "identity/noreply@example.com" in runtime_text
    assert "identity/*" not in runtime_text
    assert "role/renglo-deploy" not in runtime_text
    assert "foundation-model/*" in runtime_text
    assert ":collection/*" in runtime_text
    passed = [
        row
        for row in runtime["Statement"]
        if row.get("Action") in (["iam:PassRole"], "iam:PassRole") and row.get("Effect") != "Deny"
    ]
    passed_resources = [item for row in passed for item in row["Resource"]]
    passed_services = json.dumps([row.get("Condition") for row in passed])
    assert f"role/{env}_tt_role" in "".join(passed_resources)
    assert f"role/{env}-*-ecs-task" in "".join(passed_resources)
    assert f"role/{env}-handlers-role" in "".join(passed_resources)
    assert f"role/{env}-*" not in passed_resources
    assert f"role/{env}_*" not in passed_resources
    assert "lambda.amazonaws.com" in passed_services
    assert "events.amazonaws.com" in passed_services
    assert "ecs-tasks.amazonaws.com" in passed_services
    assert "apigateway.amazonaws.com" not in passed_services
    assert "bedrock.amazonaws.com" not in passed_services
    assert "codedeploy.amazonaws.com" not in passed_services
    assert "CDKToolkit-arbtm1019" in rendered
    assert any(row.get("Sid") == "DenyProtectedPolicyDetach" for row in guard["Statement"])
    assert any(
        row.get("Effect") == "Deny"
        and row.get("Action") in ("iam:DetachRolePolicy", ["iam:DetachRolePolicy"])
        and "iam:PolicyARN" in json.dumps(row.get("Condition", {}))
        for row in execution["Statement"]
    )
    assert any(row.get("Sid") == "DenyProtectedPolicyDetach" for row in deploy["Statement"])
    assert "iam:*" not in rendered
    assert not any(
        row.get("Effect") == "Deny" and row.get("Action") == "sts:AssumeRole"
        for row in deploy["Statement"]
    )
    assert not any(
        row.get("Effect") == "Deny" and row.get("Action") == ["iam:*"]
        for row in execution["Statement"]
    )
