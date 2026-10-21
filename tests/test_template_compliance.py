import json
from pathlib import Path

import yaml

from renglo_ops.compliance.template_check import TemplateComplianceChecker, TemplateKind
from renglo_ops.session import OperatorSession
from renglo_ops.target_role import ROLE_NAME, bound_bootstrap_roles, onboarding_template


def test_onboarding_template_passes_compliance() -> None:
    checker = TemplateComplianceChecker(env_name="acme1")
    report = checker.check(onboarding_template(), TemplateKind.ONBOARDING)
    assert report.compliant, report.to_dict()["tests_failed"]


def test_onboarding_fails_when_deploy_role_trust_is_open() -> None:
    document = onboarding_template()
    document["Resources"]["DeployRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Principal"
    ] = {"AWS": "*"}
    report = TemplateComplianceChecker(env_name="acme1").check(document, TemplateKind.ONBOARDING)
    assert not report.compliant
    assert any(row.rule_id == "SEC-001" for row in report.errors)


def test_bootstrap_template_passes_after_bound_roles(tmp_path: Path) -> None:
    session, _ = _init_session(tmp_path)
    result = session.bootstrap(
        account_id="123456789012",
        profile=ROLE_NAME,
        external_id="ext-1",
        region="us-east-1",
        dry_run=True,
    )
    template_path = Path(result["command"][result["command"].index("--template") + 1])
    checker = TemplateComplianceChecker(env_name="acme1")
    report = checker.check(checker.load(template_path), TemplateKind.BOOTSTRAP, source=str(template_path))
    assert report.compliant, report.to_dict()["tests_failed"]


def test_bootstrap_fails_without_permissions_boundary() -> None:
    raw = """
Resources:
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
      ManagedPolicyArns:
        - Fn::Sub: arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${Qualifier}-cfn-exec
        - Fn::Sub: arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${Qualifier}-role-guard
"""
    bound = bound_bootstrap_roles(raw)
    document = yaml.safe_load(bound)
    document["Resources"]["FilePublishingRole"]["Properties"].pop("PermissionsBoundary", None)
    report = TemplateComplianceChecker(env_name="acme1").check(document, TemplateKind.BOOTSTRAP)
    assert not report.compliant
    assert any("FilePublishingRole" in (row.resource_id or "") for row in report.errors)


def test_application_legacy_env_skips_runtime_boundary() -> None:
    path = Path(__file__).resolve().parents[3] / ".renglo" / "cdk.out" / "stanley0731-stack-a.template.json"
    if not path.is_file():
        return
    checker = TemplateComplianceChecker(env_name="stanley0731")
    report = checker.check(checker.load(path), TemplateKind.APPLICATION, source=str(path))
    assert report.compliant or all(row.severity != "error" for row in report.findings if row.rule_id == "SEC-005")


def test_report_is_machine_readable() -> None:
    report = TemplateComplianceChecker(env_name="acme1").check(onboarding_template(), TemplateKind.ONBOARDING)
    payload = report.to_dict()
    assert payload["compliant"] is True
    assert payload["template_kind"] == "onboarding"
    assert "findings" in payload
    json.dumps(payload)


def _init_session(tmp_path: Path):
    session = OperatorSession(tmp_path)
    session.init(
        env_name="acme1",
        account_id="123456789012",
        profile=ROLE_NAME,
        region="us-east-1",
        email_from="noreply@acme.com",
        github_repo="acmeco/acme-bom",
        role="staging",
    )
    return session, None
