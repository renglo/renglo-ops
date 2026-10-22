"""Static CloudFormation template checks for deployment security (CI-002, SEC-003–SEC-012, SEC-019).

Validates synthesized or hand-authored templates. Live IAM simulation and negative
authorization tests (SEC-024) are out of scope here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml

from renglo_ops.target_role import ROLE_NAME, is_deploy_role_name, uses_named_qualifier


def _deploy_role_name(template: dict[str, Any]) -> str:
    resources = template.get("Resources") or {}
    if not isinstance(resources, dict):
        return ""
    deploy = resources.get("DeployRole")
    if not isinstance(deploy, dict):
        return ""
    name = (deploy.get("Properties") or {}).get("RoleName")
    return name if isinstance(name, str) else ""


Severity = Literal["error", "warning", "info"]


class TemplateKind(str, Enum):
    ONBOARDING = "onboarding"
    BOOTSTRAP = "bootstrap"
    APPLICATION = "application"
    AUTO = "auto"


@dataclass(frozen=True)
class ComplianceFinding:
    rule_id: str
    message: str
    severity: Severity = "error"
    resource_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "severity": self.severity,
            "resource_id": self.resource_id,
        }


@dataclass
class ComplianceReport:
    template_kind: TemplateKind
    env_name: str | None
    findings: list[ComplianceFinding] = field(default_factory=list)
    source: str | None = None

    @property
    def compliant(self) -> bool:
        return not any(row.severity == "error" for row in self.findings)

    @property
    def errors(self) -> list[ComplianceFinding]:
        return [row for row in self.findings if row.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        passed = [row.rule_id for row in self.findings if row.severity != "error"]
        failed = [row.to_dict() for row in self.errors]
        return {
            "template_kind": self.template_kind.value,
            "env_name": self.env_name,
            "source": self.source,
            "compliant": self.compliant,
            "findings": [row.to_dict() for row in self.findings],
            "tests_passed": passed,
            "tests_failed": failed,
        }


def _dump_for_scan(document: Any) -> str:
    return json.dumps(document, default=str)


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _policy_documents(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    resources = document.get("Resources") or {}
    if not isinstance(resources, dict):
        return out
    for logical_id, resource in resources.items():
        if not isinstance(resource, dict):
            continue
        props = resource.get("Properties") or {}
        if not isinstance(props, dict):
            continue
        rtype = resource.get("Type", "")
        if rtype in {"AWS::IAM::ManagedPolicy", "AWS::IAM::Policy"}:
            doc = props.get("PolicyDocument")
            if isinstance(doc, dict):
                out.append((logical_id, doc))
        elif rtype == "AWS::IAM::Role":
            for key in ("AssumeRolePolicyDocument", "Policies"):
                block = props.get(key)
                if isinstance(block, dict) and "Statement" in block:
                    out.append((f"{logical_id}:trust", block))
                if isinstance(block, list):
                    for index, row in enumerate(block):
                        if isinstance(row, dict) and isinstance(row.get("PolicyDocument"), dict):
                            out.append((f"{logical_id}:inline:{index}", row["PolicyDocument"]))
    return out


def _boundary_text(value: Any) -> str:
    return _dump_for_scan(value)


def _policy_allows_administrator_access(policy: dict[str, Any]) -> bool:
    for statement in policy.get("Statement") or []:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        if "AdministratorAccess" in _dump_for_scan(statement):
            return True
    return False


def _contains_wildcard_resource_only(statement: dict[str, Any]) -> bool:
    resource = statement.get("Resource")
    if resource == "*":
        return True
    if isinstance(resource, list) and resource == ["*"]:
        return True
    return False


class TemplateComplianceChecker:
    """Run mandatory static checks against CloudFormation templates."""

    _TOOLKIT_ROLE_NAME = re.compile(r"cdk-.+-role-.+", re.I)
    _RUNTIME_BOUNDARY = re.compile(r"-runtime\b")
    _CFN_EXEC_BOUNDARY = re.compile(r"-cfn-exec\b")

    def __init__(
        self,
        *,
        env_name: str | None = None,
        fail_unknown_iam_roles: bool = True,
    ) -> None:
        self.env_name = str(env_name or "").strip() or None
        self.fail_unknown_iam_roles = fail_unknown_iam_roles

    @staticmethod
    def load(source: Path | str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(source, dict):
            return source
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            loaded = yaml.safe_load(text)
        else:
            loaded = json.loads(text)
        if not isinstance(loaded, dict):
            raise ValueError(f"template root must be a mapping: {path}")
        return loaded

    def check(
        self,
        template: dict[str, Any],
        kind: TemplateKind = TemplateKind.AUTO,
        *,
        source: str | None = None,
    ) -> ComplianceReport:
        resolved = kind
        if kind == TemplateKind.AUTO:
            resolved = self._detect_kind(template)
        report = ComplianceReport(template_kind=resolved, env_name=self.env_name, source=source)
        self._check_global_forbidden(template, report)
        if resolved == TemplateKind.ONBOARDING:
            self._check_onboarding(template, report)
        elif resolved == TemplateKind.BOOTSTRAP:
            self._check_bootstrap(template, report)
        elif resolved == TemplateKind.APPLICATION:
            self._check_application(template, report)
            self._check_application_resource_scope(template, report)
        else:
            report.findings.append(
                ComplianceFinding(
                    "CI-002",
                    "Could not classify template as onboarding, bootstrap, or application.",
                    severity="error",
                )
            )
        return report

    def check_paths(
        self,
        *,
        onboarding: Path | str | None = None,
        bootstrap: Path | str | None = None,
        application: Path | str | None = None,
    ) -> list[ComplianceReport]:
        reports: list[ComplianceReport] = []
        if onboarding is not None:
            document = self.load(onboarding)
            reports.append(
                self.check(document, TemplateKind.ONBOARDING, source=str(onboarding))
            )
        if bootstrap is not None:
            document = self.load(bootstrap)
            reports.append(self.check(document, TemplateKind.BOOTSTRAP, source=str(bootstrap)))
        if application is not None:
            document = self.load(application)
            reports.append(
                self.check(document, TemplateKind.APPLICATION, source=str(application))
            )
        return reports

    def _detect_kind(self, template: dict[str, Any]) -> TemplateKind:
        resources = template.get("Resources") or {}
        if isinstance(resources, dict):
            if "DeployRole" in resources:
                role = resources["DeployRole"]
                if isinstance(role, dict) and role.get("Type") == "AWS::IAM::Role":
                    return TemplateKind.ONBOARDING
            if "CloudFormationExecutionRole" in resources:
                return TemplateKind.BOOTSTRAP
            for resource in resources.values():
                if not isinstance(resource, dict):
                    continue
                if resource.get("Type") == "AWS::Lambda::Function":
                    return TemplateKind.APPLICATION
                if resource.get("Type") == "AWS::S3::Bucket":
                    return TemplateKind.APPLICATION
        text = _dump_for_scan(template)
        if "CDKToolkit" in text and "CloudFormationExecutionRole" in text:
            return TemplateKind.BOOTSTRAP
        if is_deploy_role_name(_deploy_role_name(template)) and "ExecutionPolicy" in text:
            return TemplateKind.ONBOARDING
        return TemplateKind.APPLICATION

    def _add(self, report: ComplianceReport, finding: ComplianceFinding) -> None:
        report.findings.append(finding)

    def _check_global_forbidden(self, template: dict[str, Any], report: ComplianceReport) -> None:
        text = _dump_for_scan(template)
        if "ExplicitAllowAll" in text:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-003",
                    "Bootstrap example policy ExplicitAllowAll must not appear in generated templates.",
                ),
            )
        for logical_id, policy in _policy_documents(template):
            if _policy_allows_administrator_access(policy):
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-007",
                        "IAM policy must not allow AdministratorAccess.",
                        resource_id=logical_id,
                    ),
                )
        resources = template.get("Resources") or {}
        if isinstance(resources, dict):
            for logical_id, resource in resources.items():
                if not isinstance(resource, dict) or resource.get("Type") != "AWS::IAM::Role":
                    continue
                props = resource.get("Properties") or {}
                managed = _dump_for_scan(props.get("ManagedPolicyArns"))
                if (
                    "arn:aws:iam::aws:policy/AdministratorAccess" in managed
                    or "iam::aws:policy/AdministratorAccess" in managed
                ):
                    self._add(
                        report,
                        ComplianceFinding(
                            "SEC-007",
                            "IAM role must not attach AdministratorAccess.",
                            resource_id=logical_id,
                        ),
                    )

    def _check_application_resource_scope(
        self, template: dict[str, Any], report: ComplianceReport
    ) -> None:
        for logical_id, policy in _policy_documents(template):
            for statement in policy.get("Statement") or []:
                if not isinstance(statement, dict):
                    continue
                if statement.get("Effect") != "Allow":
                    continue
                if _contains_wildcard_resource_only(statement):
                    actions = statement.get("Action")
                    action_text = _dump_for_scan(actions)
                    if any(
                        token in action_text
                        for token in ("iam:", "sts:AssumeRole", "s3:", "dynamodb:", "lambda:")
                    ):
                        self._add(
                            report,
                            ComplianceFinding(
                                "SEC-014",
                                "Allow statement uses Resource '*' with sensitive actions; "
                                "resource scope must be environment-specific.",
                                resource_id=logical_id,
                            ),
                        )

    def _check_onboarding(self, template: dict[str, Any], report: ComplianceReport) -> None:
        resources = template.get("Resources") or {}
        if not isinstance(resources, dict):
            self._add(report, ComplianceFinding("CI-002", "Onboarding template has no Resources map."))
            return
        required = ("ExecutionPolicy", "RuntimePolicy", "RoleGuardPolicy", "DeployPolicy", "DeployRole")
        for name in required:
            if name not in resources:
                self._add(
                    report,
                    ComplianceFinding(
                        "CI-002",
                        f"Onboarding template is missing required resource {name}.",
                    ),
                )
        deploy = resources.get("DeployRole")
        if isinstance(deploy, dict):
            props = deploy.get("Properties") or {}
            if not is_deploy_role_name(str(props.get("RoleName") or "")):
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-001",
                        "Deploy role name must be the namespace followed by -deploy.",
                        resource_id="DeployRole",
                    ),
                )
            boundary = props.get("PermissionsBoundary")
            if boundary != {"Ref": "ExecutionPolicy"}:
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-003",
                        "The deploy role must use ExecutionPolicy as its permissions boundary.",
                        resource_id="DeployRole",
                    ),
                )
            trust = props.get("AssumeRolePolicyDocument") or {}
            statements = trust.get("Statement") or []
            if len(statements) != 1:
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-001",
                        "Deploy role trust policy must contain exactly one statement.",
                        resource_id="DeployRole",
                    ),
                )
            elif isinstance(statements[0], dict):
                stmt = statements[0]
                principal = stmt.get("Principal") or {}
                if principal.get("AWS") != {"Ref": "VendorPrincipalArn"}:
                    self._add(
                        report,
                        ComplianceFinding(
                            "SEC-001",
                            "Deploy role must trust only VendorPrincipalArn (no wildcards).",
                            resource_id="DeployRole",
                        ),
                    )
                condition = stmt.get("Condition") or {}
                ext = condition.get("StringEquals") or {}
                if ext.get("sts:ExternalId") != {"Ref": "ExternalId"}:
                    self._add(
                        report,
                        ComplianceFinding(
                            "SEC-001",
                            "Deploy role must require sts:ExternalId.",
                            resource_id="DeployRole",
                        ),
                    )
        for policy_key, suffix in (
            ("ExecutionPolicy", "-cfn-exec"),
            ("RuntimePolicy", "-runtime"),
            ("RoleGuardPolicy", "-role-guard"),
            ("DeployPolicy", "-deploy"),
        ):
            row = resources.get(policy_key)
            if not isinstance(row, dict):
                continue
            props = row.get("Properties") or {}
            name = props.get("ManagedPolicyName")
            expected = {"Fn::Sub": f"${{EnvironmentId}}{suffix}"}
            if name != expected:
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-006",
                        f"{policy_key} must be named ${{EnvironmentId}}{suffix}.",
                        resource_id=policy_key,
                    ),
                )

    def _check_bootstrap(self, template: dict[str, Any], report: ComplianceReport) -> None:
        text = _dump_for_scan(template)
        resources = template.get("Resources") or {}
        if isinstance(resources, dict) and "InputPermissionsBoundary" in resources:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-004",
                    "Optional InputPermissionsBoundary resource must not remain in the bootstrap template.",
                    resource_id="InputPermissionsBoundary",
                ),
            )
        if "policy/${Qualifier}-cfn-exec" not in text and "${Qualifier}-cfn-exec" not in text:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-011",
                    "Bootstrap template must attach the environment cfn-exec policy to toolkit roles.",
                ),
            )
        if "policy/${Qualifier}-role-guard" not in text and "${Qualifier}-role-guard" not in text:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-006",
                    "Bootstrap template must attach the role-guard policy to the CloudFormation execution role.",
                ),
            )
        for fragment in (
            "stack/CDKToolkit-${Qualifier}/*",
            "stack/${Qualifier}-stack-a/*",
            "stack/${Qualifier}-stack-b/*",
        ):
            if fragment not in text:
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-010",
                        f"Bootstrap deploy permissions must include authorized stack ARN pattern {fragment!r}.",
                    ),
                )
        if "Default: AWS_MANAGED_KEY" not in text and "AWS_MANAGED_KEY" not in text:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-019",
                    "Bootstrap should default asset encryption to AWS_MANAGED_KEY.",
                    severity="warning",
                ),
            )
        if "key/${FileAssetsBucketKmsKeyId}" in text:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-019",
                    "Bootstrap must not require a customer-managed KMS key for asset buckets.",
                ),
            )
        self._check_iam_roles(
            template,
            report,
            boundary_matcher=self._CFN_EXEC_BOUNDARY,
            role_context="toolkit",
        )
        exec_role = resources.get("CloudFormationExecutionRole") if isinstance(resources, dict) else None
        if isinstance(exec_role, dict):
            props = exec_role.get("Properties") or {}
            managed = _dump_for_scan(props.get("ManagedPolicyArns"))
            if "-role-guard" not in managed or "-cfn-exec" not in managed:
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-011",
                        "CloudFormationExecutionRole must attach cfn-exec and role-guard managed policies.",
                        resource_id="CloudFormationExecutionRole",
                    ),
                )

    def _check_application(self, template: dict[str, Any], report: ComplianceReport) -> None:
        env = self.env_name
        if env and uses_named_qualifier(env):
            self._check_iam_roles(
                template,
                report,
                boundary_matcher=self._RUNTIME_BOUNDARY,
                role_context="application",
            )
        elif env:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-005",
                    f"Environment {env!r} uses the shared CDK qualifier; runtime boundary checks are skipped.",
                    severity="info",
                ),
            )
        else:
            self._add(
                report,
                ComplianceFinding(
                    "SEC-005",
                    "Application template check: set env_name to enforce runtime permissions boundaries.",
                    severity="warning",
                ),
            )
        self._check_runtime_cannot_assume_deploy(template, report)

    def _check_iam_roles(
        self,
        template: dict[str, Any],
        report: ComplianceReport,
        *,
        boundary_matcher: re.Pattern[str],
        role_context: str,
    ) -> None:
        resources = template.get("Resources") or {}
        if not isinstance(resources, dict):
            return
        for logical_id, resource in resources.items():
            if not isinstance(resource, dict) or resource.get("Type") != "AWS::IAM::Role":
                continue
            props = resource.get("Properties") or {}
            role_name_raw = _dump_for_scan(props.get("RoleName"))
            classification = self._classify_role(logical_id, role_name_raw, role_context)
            boundary = props.get("PermissionsBoundary")
            if boundary is None:
                self._add(
                    report,
                    ComplianceFinding(
                        "CI-003",
                        f"IAM role {logical_id!r} ({classification}) is missing PermissionsBoundary.",
                        resource_id=logical_id,
                    ),
                )
                continue
            boundary_text = _boundary_text(boundary)
            if not boundary_matcher.search(boundary_text):
                self._add(
                    report,
                    ComplianceFinding(
                        "SEC-004" if role_context == "toolkit" else "SEC-005",
                        f"IAM role {logical_id!r} ({classification}) must use the approved "
                        f"{role_context} permissions boundary.",
                        resource_id=logical_id,
                    ),
                )
            if self.fail_unknown_iam_roles and classification == "unknown":
                self._add(
                    report,
                    ComplianceFinding(
                        "CI-003",
                        f"IAM role {logical_id!r} is not classified; fail closed until reviewed.",
                        resource_id=logical_id,
                    ),
                )

    def _check_runtime_cannot_assume_deploy(
        self, template: dict[str, Any], report: ComplianceReport
    ) -> None:
        resources = template.get("Resources") or {}
        if not isinstance(resources, dict):
            return
        for logical_id, resource in resources.items():
            if not isinstance(resource, dict) or resource.get("Type") != "AWS::IAM::Role":
                continue
            props = resource.get("Properties") or {}
            role_name = _dump_for_scan(props.get("RoleName"))
            deploy_name = _deploy_role_name(template) or ROLE_NAME
            if (
                deploy_name in role_name
                or role_name.endswith("-installer")
                or "Installer" in logical_id
            ):
                continue
            for _pid, policy in _policy_documents(template):
                if not _pid.startswith(logical_id):
                    continue
                for statement in policy.get("Statement") or []:
                    if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
                        continue
                    actions = statement.get("Action")
                    action_text = _dump_for_scan(actions)
                    if "sts:AssumeRole" not in action_text:
                        continue
                    resource_text = _dump_for_scan(statement.get("Resource"))
                    if deploy_name in resource_text or f"role/{deploy_name}" in resource_text:
                        self._add(
                            report,
                            ComplianceFinding(
                                "SEC-002",
                                "Application runtime roles must not be allowed to assume the deploy role.",
                                resource_id=logical_id,
                            ),
                        )
                        break

    def _classify_role(self, logical_id: str, role_name_text: str, role_context: str) -> str:
        if is_deploy_role_name(role_name_text) or logical_id == "DeployRole":
            return "deploy"
        if role_context == "toolkit":
            if "CloudFormationExecution" in logical_id or "cfn-exec" in role_name_text:
                return "execution"
            if "DeploymentAction" in logical_id:
                return "deploy-action"
            return "toolkit"
        if role_context == "application":
            return "application"
        return "unknown"
