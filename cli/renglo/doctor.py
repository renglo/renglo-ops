"""Health checklist for the operator environment."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.local import find_local, load_local, workspace_root
from renglo_ops.model.registry import load_registry
from renglo_ops.model.tenant import Tenant, find_tenant_file, load_tenant


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    group: str
    label: str
    status: Status
    detail: str = ""


def _origin_slug(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    result = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    url = result.stdout.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if ":" in url and "@" in url and "github.com" in url:
        return url.split(":", 1)[1]
    if "github.com/" in url:
        return url.split("github.com/", 1)[1]
    return ""


def _is_placeholder(tenant: Tenant) -> bool:
    return "CHANGE_ME" in f"{tenant.name} {tenant.github_repo}".upper()


def _profile_region(start: Path) -> tuple[str, str]:
    import os

    local_path = find_local(start)
    local = load_local(local_path) if local_path else None
    profile = os.environ.get("AWS_PROFILE", "").strip() or (local.profile if local else "")
    region = os.environ.get("AWS_REGION", "").strip() or (local.region if local else "")
    return profile, region


def _aws_identity(profile: str) -> tuple[bool, str, str]:
    """Return (ok, account_id, message)."""
    aws = shutil.which("aws")
    if not aws:
        return False, "", "aws CLI not on PATH"
    command = [aws, "sts", "get-caller-identity", "--output", "json"]
    if profile:
        command.extend(["--profile", profile])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, "", "timed out calling sts get-caller-identity"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "sts get-caller-identity failed").strip()
        return False, "", err.splitlines()[0][:200]
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "", "could not parse sts response"
    account = str(data.get("Account") or "").strip()
    if not account:
        return False, "", "sts response missing Account"
    arn = str(data.get("Arn") or "").strip()
    return True, account, arn or account


def _cdk_libraries_ok() -> tuple[bool, str]:
    try:
        import aws_cdk  # noqa: F401

        return True, "installed"
    except Exception as exc:
        return False, f"not installed ({exc.__class__.__name__}); re-run setup_venv.sh"


def run_doctor(start: Path | None = None) -> list[Check]:
    here = (start or Path.cwd()).expanduser().resolve()
    checks: list[Check] = []

    # --- Tooling ---
    checks.append(
        Check(
            "Tooling",
            "Python",
            Status.OK,
            sys.version.split()[0],
        )
    )
    cdk_ok, cdk_detail = _cdk_libraries_ok()
    checks.append(
        Check(
            "Tooling",
            "CDK Python libraries",
            Status.OK if cdk_ok else Status.FAIL,
            cdk_detail,
        )
    )
    cdk_cli = shutil.which("cdk")
    checks.append(
        Check(
            "Tooling",
            "CDK CLI",
            Status.OK if cdk_cli else Status.FAIL,
            cdk_cli or "not on PATH; re-run setup_venv.sh",
        )
    )
    aws_cli = shutil.which("aws")
    checks.append(
        Check(
            "Tooling",
            "AWS CLI",
            Status.OK if aws_cli else Status.WARN,
            aws_cli or "not on PATH; profile checks and deploys need it",
        )
    )

    # --- Workspace & BOM ---
    local_path = find_local(here)
    tenant_path: Path | None = None
    tenant: Tenant | None = None
    bom_root: Path | None = None

    if local_path:
        try:
            local = load_local(local_path)
            bom_candidate = (local_path.parent.parent / local.tenant).resolve()
            checks.append(
                Check(
                    "Workspace",
                    "Machine file (.renglo/local.yaml)",
                    Status.OK,
                    str(local_path),
                )
            )
            if bom_candidate.is_dir():
                bom_root = bom_candidate
                git_ok = (bom_root / ".git").is_dir()
                checks.append(
                    Check(
                        "Workspace",
                        "BOM checkout directory",
                        Status.OK,
                        str(bom_root),
                    )
                )
                checks.append(
                    Check(
                        "Workspace",
                        "BOM is a git repository",
                        Status.OK if git_ok else Status.WARN,
                        "origin present" if git_ok else "no .git; config check and clone workflows need one",
                    )
                )
            else:
                checks.append(
                    Check(
                        "Workspace",
                        "BOM checkout directory",
                        Status.FAIL,
                        f"local.yaml tenant path missing: {bom_candidate}",
                    )
                )
        except RengloOpsError as exc:
            checks.append(
                Check(
                    "Workspace",
                    "Machine file (.renglo/local.yaml)",
                    Status.FAIL,
                    str(exc),
                )
            )
    else:
        checks.append(
            Check(
                "Workspace",
                "Machine file (.renglo/local.yaml)",
                Status.WARN,
                "not found; run from the BOM checkout or run renglo init",
            )
        )

    try:
        tenant_path = find_tenant_file(here)
        bom_root = bom_root or tenant_path.parent
        tenant = load_tenant(tenant_path)
        if _is_placeholder(tenant):
            checks.append(
                Check(
                    "Tenant",
                    "renglo.yaml loads",
                    Status.WARN,
                    f"{tenant_path} is still the template (CHANGE_ME); run renglo init",
                )
            )
        else:
            checks.append(
                Check(
                    "Tenant",
                    "renglo.yaml loads",
                    Status.OK,
                    str(tenant_path),
                )
            )
    except RengloOpsError as exc:
        checks.append(
            Check(
                "Tenant",
                "renglo.yaml loads",
                Status.FAIL,
                str(exc),
            )
        )

    if tenant and tenant.path:
        origin = _origin_slug(tenant.path.parent)
        if origin and origin != tenant.github_repo:
            checks.append(
                Check(
                    "Tenant",
                    "github.repo matches origin",
                    Status.WARN,
                    f"file has {tenant.github_repo}, origin is {origin}",
                )
            )
        elif origin:
            checks.append(
                Check(
                    "Tenant",
                    "github.repo matches origin",
                    Status.OK,
                    tenant.github_repo,
                )
            )
        elif (tenant.path.parent / ".git").is_dir():
            checks.append(
                Check(
                    "Tenant",
                    "github.repo matches origin",
                    Status.WARN,
                    "could not read origin; renglo config check when origin is set",
                )
            )

    profile, region = _profile_region(here)
    if profile:
        checks.append(
            Check(
                "AWS",
                "Profile configured",
                Status.OK,
                profile + (f" ({region})" if region else ""),
            )
        )
    else:
        checks.append(
            Check(
                "AWS",
                "Profile configured",
                Status.WARN,
                "set profile in .renglo/local.yaml or AWS_PROFILE",
            )
        )

    if aws_cli and profile:
        ok, account, detail = _aws_identity(profile)
        if ok:
            status = Status.OK
            if tenant:
                expected = tenant.primary_account().id
                if expected and account != expected:
                    status = Status.WARN
                    detail = f"credentials account {account}, renglo.yaml staging id {expected}"
            checks.append(
                Check(
                    "AWS",
                    "Profile credentials (sts get-caller-identity)",
                    status,
                    detail,
                )
            )
        else:
            checks.append(
                Check(
                    "AWS",
                    "Profile credentials (sts get-caller-identity)",
                    Status.FAIL,
                    detail,
                )
            )
    elif aws_cli and not profile:
        checks.append(
            Check(
                "AWS",
                "Profile credentials (sts get-caller-identity)",
                Status.SKIP,
                "no profile configured",
            )
        )

    ws = workspace_root(here)
    checks.append(
        Check(
            "Workspace",
            "Workspace root",
            Status.OK,
            str(ws),
        )
    )

    # --- Project readiness ---
    if not tenant or _is_placeholder(tenant):
        for project, note in (
            ("Project 1 (greenfield)", "needs a valid renglo.yaml"),
            ("Project 2 (registry)", "needs a valid renglo.yaml"),
            ("Project 3 (extensions)", "needs a valid renglo.yaml"),
            ("Project 4 (pipeline)", "needs a valid renglo.yaml"),
        ):
            checks.append(Check("Projects", project, Status.SKIP, note))
    else:
        primary = tenant.primary_account()
        p1_blockers: list[str] = []
        if not cdk_ok or not cdk_cli:
            p1_blockers.append("CDK libraries and CLI")
        if not primary.id or len(primary.id) != 12:
            p1_blockers.append("accounts.staging.id (12 digits, enabled)")
        if not tenant.platform:
            p1_blockers.append("platform version")
        if not profile:
            p1_blockers.append("AWS profile")
        if p1_blockers:
            checks.append(
                Check(
                    "Projects",
                    "Project 1 (greenfield)",
                    Status.WARN if len(p1_blockers) <= 2 else Status.FAIL,
                    "missing: " + ", ".join(p1_blockers),
                )
            )
        else:
            checks.append(
                Check(
                    "Projects",
                    "Project 1 (greenfield)",
                    Status.OK,
                    f"ready to stack deploy ({tenant.name}, account {primary.id})",
                )
            )

        if not tenant.registries:
            checks.append(
                Check(
                    "Projects",
                    "Project 2 (read registries)",
                    Status.SKIP,
                    "registries is empty; fine if you only use public PyPI/npm",
                )
            )
        else:
            foreign = tenant.foreign_domain_owners()
            detail = f"{len(tenant.registries)} registry row(s)"
            if foreign:
                detail += f"; foreign owners: {', '.join(foreign)}"
            checks.append(
                Check(
                    "Projects",
                    "Project 2 (read registries)",
                    Status.OK,
                    detail,
                )
            )

        registry_path = ""
        if local_path:
            try:
                registry_path = load_local(local_path).registry.strip()
            except RengloOpsError:
                pass
        if registry_path:
            reg_file = Path(registry_path).expanduser()
            try:
                reg = load_registry(reg_file)
                checks.append(
                    Check(
                        "Projects",
                        "Project 2 (host registry)",
                        Status.OK,
                        f"{reg_file} ({reg.name}-publisher)",
                    )
                )
            except RengloOpsError as exc:
                checks.append(
                    Check(
                        "Projects",
                        "Project 2 (host registry)",
                        Status.FAIL,
                        str(exc),
                    )
                )
        else:
            checks.append(
                Check(
                    "Projects",
                    "Project 2 (host registry)",
                    Status.SKIP,
                    "set registry: in .renglo/local.yaml or pass --registry when deploying",
                )
            )

        has_packages = bool(tenant.packages)
        has_placement = bool(tenant.placement_hub) or bool(tenant.placement_peers)
        if not has_packages and not has_placement:
            checks.append(
                Check(
                    "Projects",
                    "Project 3 (extensions)",
                    Status.SKIP,
                    "packages and placement empty until you add extensions",
                )
            )
        else:
            missing_pkg = [
                handle
                for handle in tenant.placement_hub
                if handle not in tenant.packages
            ]
            if missing_pkg:
                checks.append(
                    Check(
                        "Projects",
                        "Project 3 (extensions)",
                        Status.FAIL,
                        "placement.hub lists packages without packages entries: "
                        + ", ".join(missing_pkg),
                    )
                )
            else:
                checks.append(
                    Check(
                        "Projects",
                        "Project 3 (extensions)",
                        Status.OK,
                        f"hub={len(tenant.placement_hub)} peer(s)={len(tenant.placement_peers)}",
                    )
                )

        p4_missing: list[str] = []
        if not tenant.github_owner_id:
            p4_missing.append("github.owner_id")
        if not tenant.github_repo_id:
            p4_missing.append("github.repo_id")
        if not tenant.release_bom:
            p4_missing.append("release.bom")
        if not tenant.release_console:
            p4_missing.append("release.console")
        if bom_root:
            has_bom_manifest = any(bom_root.glob("bom/v*.json"))
            has_console_manifest = any(bom_root.glob("console_bom/v*.json"))
            if not has_bom_manifest:
                p4_missing.append("bom/vX.Y.Z.json manifest")
            if not has_console_manifest:
                p4_missing.append("console_bom/vX.Y.Z.json manifest")
        if p4_missing:
            checks.append(
                Check(
                    "Projects",
                    "Project 4 (pipeline)",
                    Status.WARN,
                    "before CI deploy: " + ", ".join(p4_missing),
                )
            )
        else:
            checks.append(
                Check(
                    "Projects",
                    "Project 4 (pipeline)",
                    Status.OK,
                    "OIDC ids, release pins, and BOM manifests present",
                )
            )

    if tenant and tenant.email_identity == "domain" and not tenant.email_hosted_zone_id:
        checks.append(
            Check(
                "Other",
                "SES domain identity",
                Status.WARN,
                "email.identity is domain but email.hosted_zone_id is unset",
            )
        )

    return checks


def format_doctor(checks: list[Check]) -> str:
    lines: list[str] = ["renglo doctor", ""]
    group: str | None = None
    for check in checks:
        if check.group != group:
            group = check.group
            lines.append(group)
        mark = {
            Status.OK: "ok",
            Status.WARN: "warn",
            Status.FAIL: "fail",
            Status.SKIP: "skip",
        }[check.status]
        suffix = f" — {check.detail}" if check.detail else ""
        lines.append(f"  [{mark}] {check.label}{suffix}")
    fails = sum(1 for c in checks if c.status == Status.FAIL)
    warns = sum(1 for c in checks if c.status == Status.WARN)
    lines.append("")
    if fails or warns:
        parts = []
        if fails:
            parts.append(f"{fails} failed")
        if warns:
            parts.append(f"{warns} warning{'s' if warns != 1 else ''}")
        lines.append(", ".join(parts))
    else:
        lines.append("all checks passed or skipped")
    return "\n".join(lines) + "\n"


def doctor_exit_code(checks: list[Check]) -> int:
    if any(c.status == Status.FAIL for c in checks):
        return 1
    return 0
