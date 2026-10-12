"""Day-to-day operations against a running environment.

Desired state stays in renglo.yaml. These helpers read CloudFormation, SES,
Cognito, and SSM, or delete a stack that yaml no longer wants. Stack deploy
already writes platform-vars; there is no second publisher.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from renglo_ops.cdk.hub.stack_names import stack_a_id, stack_b_id
from renglo_ops.cdk.shared.config_builder import ssm_platform_vars_path
from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.placement import resolve_package_handle
from renglo_ops.model.registry import Registry, sanitize_domain_name
from renglo_ops.model.tenant import Tenant
from renglo_ops.release.peers import peer_stack_name

_URL_KEYS = ("BASE_URL", "FE_BASE_URL", "AMPLIFY_CONSOLE_URL", "FROM_EMAIL")
_LOCAL_API = "http://127.0.0.1:5001"
_LOCAL_CONSOLE = "http://127.0.0.1:5174/"


def parse_stacks(raw: str, *, default_both: bool = True) -> list[str]:
    """a, b, or a,b. Empty means both when default_both is set."""
    text = (raw or "").strip().lower()
    if not text:
        if default_both:
            return ["a", "b"]
        raise RengloOpsError("pass --stack a, b, or a,b")
    if text in {"a,b", "ab", "all"}:
        return ["a", "b"]
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts or any(part not in {"a", "b"} for part in parts):
        raise RengloOpsError("stack must be a, b, or a,b")
    ordered: list[str] = []
    for part in ("a", "b"):
        if part in parts and part not in ordered:
            ordered.append(part)
    return ordered


def hub_stack_name(env_name: str, which: str) -> str:
    if which == "a":
        return stack_a_id(env_name)
    if which == "b":
        return stack_b_id(env_name)
    raise RengloOpsError(f"unknown stack {which}")


def peer_ids(tenant: Tenant) -> list[str]:
    return sorted(str(key) for key in tenant.placement_peers)


def _package_name(spec: Any) -> str:
    if isinstance(spec, dict):
        return str(spec.get("python") or spec.get("npm") or "").strip()
    return str(spec or "").strip()


def extension_rows(tenant: Tenant) -> list[tuple[str, str, str]]:
    """Handle, place (hub or peer:id), package name."""
    place: dict[str, str] = {}
    for item in tenant.placement_hub:
        handle = resolve_package_handle(tenant.packages, str(item))
        place[handle] = "hub"
    for peer_id, peer in tenant.placement_peers.items():
        extensions = peer.get("extensions") if isinstance(peer, dict) else []
        for item in extensions or []:
            handle = resolve_package_handle(tenant.packages, str(item))
            place[handle] = f"peer:{peer_id}"

    rows: list[tuple[str, str, str]] = []
    for handle in sorted(place):
        rows.append((handle, place.get(handle, "(unplaced)"), _package_name(tenant.packages.get(handle))))
    return rows


def format_extension_tree(tenant: Tenant) -> str:
    lines = [tenant.name]
    for handle, where, package in extension_rows(tenant):
        lines.append(f"  {handle:<20} {where:<16} {package}")
    return "\n".join(lines) + "\n"


def format_extension_show(tenant: Tenant, handle: str) -> str:
    handle = handle.strip()
    match = [row for row in extension_rows(tenant) if row[0] == handle]
    if not match:
        raise RengloOpsError(f"no extension {handle} in placement")
    name, where, package = match[0]
    spec = tenant.packages.get(name)
    npm = ""
    if isinstance(spec, dict):
        npm = str(spec.get("npm") or "").strip()
    lines = [
        f"handle: {name}",
        f"place: {where}",
        f"python: {package or '(unset)'}",
    ]
    if npm:
        lines.append(f"npm: {npm}")
    return "\n".join(lines) + "\n"


def format_peer_list(tenant: Tenant) -> str:
    lines = [tenant.name]
    if not tenant.placement_peers:
        lines.append("  (no peers)")
        return "\n".join(lines) + "\n"
    for peer_id in peer_ids(tenant):
        peer = tenant.placement_peers[peer_id]
        compute = "fargate"
        handles: list[str] = []
        if isinstance(peer, dict):
            compute = str(peer.get("compute") or "fargate")
            handles = [str(item) for item in (peer.get("extensions") or [])]
        lines.append(f"  {peer_id:<16} {compute:<12} {', '.join(handles)}")
    return "\n".join(lines) + "\n"


def format_peer_show(tenant: Tenant, peer_id: str) -> str:
    peer_id = peer_id.strip()
    peer = tenant.placement_peers.get(peer_id)
    if not isinstance(peer, dict):
        raise RengloOpsError(f"no peer {peer_id} in placement.peers")
    handles = [str(item) for item in (peer.get("extensions") or [])]
    lines = [
        f"peer: {peer_id}",
        f"stack: {peer_stack_name(tenant.name, peer_id)}",
        f"compute: {peer.get('compute') or 'fargate'}",
        f"task_size: {peer.get('task_size') or 'medium'}",
        f"extensions: {', '.join(handles) or '(none)'}",
        f"peers_bom: {peer.get('peers_bom') or '(unset)'}",
    ]
    if peer.get("aws_region"):
        lines.append(f"region: {peer['aws_region']}")
    return "\n".join(lines) + "\n"


def publisher_stack_name(registry_name: str) -> str:
    return f"{registry_name.strip()}-publisher"


def format_registry_show(
    registry: Registry,
    cfn: Any,
    *,
    account_id: str,
    region: str,
    profile: str,
) -> str:
    """CloudFormation outputs for the publisher stack (same profile as deploy)."""
    stack = publisher_stack_name(registry.name)
    lines = [
        f"registry file: {registry.path}",
        f"publisher name: {registry.name}",
        f"cloudformation stack: {stack}",
        f"aws profile: {profile}",
        f"aws region: {region}",
        f"caller account: {account_id}",
        "",
    ]
    try:
        response = cfn.describe_stacks(StackName=stack)
    except Exception as exc:
        message = str(exc)
        code = ""
        err_response = getattr(exc, "response", None)
        if isinstance(err_response, dict):
            code = str((err_response.get("Error") or {}).get("Code") or "")
        if code in {"ValidationError", "StackNotFoundException"} or "does not exist" in message:
            lines.append("stack: ABSENT in this account and region")
            lines.append("")
            lines.append(
                "If you already deployed, use the same AWS profile as "
                "renglo registry deploy (not the hub profile)."
            )
            return "\n".join(lines) + "\n"
        raise
    stacks = response.get("Stacks") or []
    if not stacks:
        lines.append("stack: ABSENT in this account and region")
        return "\n".join(lines) + "\n"
    row = stacks[0]
    lines.append(f"stack status: {row.get('StackStatus') or 'UNKNOWN'}")
    outputs = {
        str(item.get("OutputKey") or ""): str(item.get("OutputValue") or "")
        for item in (row.get("Outputs") or [])
        if item.get("OutputKey")
    }
    if outputs:
        lines.append("")
        lines.append("outputs:")
        for key in sorted(outputs):
            lines.append(f"  {key}: {outputs[key]}")
    role = outputs.get("OidcPublishRoleArn", "")
    publisher = outputs.get("PublisherName", registry.name)
    lines.append("")
    lines.append("GitHub Actions variables (per product repo):")
    lines.append(f"  AWS_PUBLISH_ROLE_ARN: {role or '(missing output)'}")
    lines.append(f"  PUBLISHER_NAME: {publisher}")
    lines.append(f"  AWS_REGION: {region}")
    return "\n".join(lines) + "\n"


def canonical_package_version(version: str) -> str:
    """Version string as published. A leading v on a numeric version is the git tag, not the artifact."""
    text = version.strip()
    if len(text) >= 2 and text[0] in "vV" and text[1].isdigit():
        return text[1:]
    return text


def resolve_package_format(package: str, fmt: str) -> str:
    chosen = (fmt or "").strip().lower()
    if chosen in {"python", "pypi"}:
        return "python"
    if chosen == "npm":
        return "npm"
    if chosen:
        raise RengloOpsError("--format must be python or npm")
    if package.strip().startswith("@"):
        return "npm"
    return "python"


def package_coordinates(package: str, fmt: str) -> tuple[str, str, str]:
    """Return CodeArtifact (format, namespace, package) for a distribution name."""
    kind = resolve_package_format(package, fmt)
    name = package.strip()
    if not name:
        raise RengloOpsError("package name is required")
    if kind == "npm":
        namespace = ""
        pkg = name
        if name.startswith("@"):
            if "/" not in name[1:]:
                raise RengloOpsError(f"npm package must look like @scope/name, got {name}")
            namespace, pkg = name[1:].split("/", 1)
            namespace = namespace.strip()
            pkg = pkg.strip()
            if not namespace or not pkg or "/" in pkg:
                raise RengloOpsError(f"npm package must look like @scope/name, got {name}")
        elif "/" in name:
            raise RengloOpsError(f"npm package must look like @scope/name, got {name}")
        return "npm", namespace, pkg
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    if not normalized:
        raise RengloOpsError("package name is required")
    return "pypi", "", normalized


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


def format_registry_check(
    registry: Registry,
    codeartifact: Any,
    *,
    package: str,
    version: str,
    fmt: str,
    account_id: str,
    region: str,
) -> tuple[str, bool]:
    """Look up one package version in this registry's CodeArtifact repository.

    The bool is true only when a revision of that version is Published.
    """
    artifact_format, namespace, pkg = package_coordinates(package, fmt)
    looked_up = canonical_package_version(version)
    if not looked_up:
        raise RengloOpsError("version is required")
    domain = sanitize_domain_name(registry.name)
    repository = registry.npm if artifact_format == "npm" else registry.python
    lines = [
        f"registry file: {registry.path}",
        f"domain: {domain}",
        f"repository: {repository}",
        f"format: {artifact_format}",
        f"package: {pkg}",
    ]
    if namespace:
        lines.append(f"namespace: {namespace}")
    if pkg != package.strip():
        lines.append(f"requested: {package.strip()}")
    lines.append(f"version: {looked_up}")
    if looked_up != version.strip():
        lines.append(f"requested version: {version.strip()}")
    lines.append(f"account: {account_id}")
    lines.append(f"region: {region}")

    kwargs: dict[str, str] = {
        "domain": domain,
        "domainOwner": account_id,
        "repository": repository,
        "format": artifact_format,
        "package": pkg,
    }
    if namespace:
        kwargs["namespace"] = namespace
    versions: list[dict[str, Any]] = []
    token = ""
    try:
        while True:
            call = dict(kwargs)
            if token:
                call["nextToken"] = token
            page = codeartifact.list_package_versions(**call)
            versions.extend(page.get("versions") or [])
            token = str(page.get("nextToken") or "")
            if not token:
                break
    except Exception as exc:
        code = _error_code(exc)
        message = str(exc)
        if code == "ResourceNotFoundException" and "package" in message.lower():
            lines.append("result: absent")
            return "\n".join(lines) + "\n", False
        if code:
            raise RengloOpsError(message) from exc
        raise

    matches = [row for row in versions if str(row.get("version") or "") == looked_up]
    statuses = sorted({str(row.get("status") or "UNKNOWN") for row in matches})
    if "Published" in statuses:
        lines.append("status: Published")
        lines.append("result: published")
        return "\n".join(lines) + "\n", True
    if statuses:
        lines.append(f"status: {', '.join(statuses)}")
        lines.append("result: not published")
        return "\n".join(lines) + "\n", False
    lines.append("result: absent")
    return "\n".join(lines) + "\n", False


def stack_state(cfn: Any, name: str) -> str:
    try:
        response = cfn.describe_stacks(StackName=name)
    except Exception as exc:
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str((response.get("Error") or {}).get("Code") or "")
        message = str(exc)
        if code in {"ValidationError", "StackNotFoundException"} or "does not exist" in message:
            return "ABSENT"
        raise
    stacks = response.get("Stacks") or []
    if not stacks:
        return "ABSENT"
    return str(stacks[0].get("StackStatus") or "UNKNOWN")


def format_hub_status(tenant: Tenant, cfn: Any, which: list[str]) -> str:
    lines = []
    for part in which:
        name = hub_stack_name(tenant.name, part)
        lines.append(f"{part} {name} {stack_state(cfn, name)}")
    return "\n".join(lines) + "\n"


def format_peer_status(tenant: Tenant, cfn: Any, peer_id: str = "") -> str:
    ids = [peer_id] if peer_id else peer_ids(tenant)
    if peer_id and peer_id not in tenant.placement_peers:
        raise RengloOpsError(f"no peer {peer_id} in placement.peers")
    if not ids:
        return "(no peers)\n"
    lines = []
    for item in ids:
        name = peer_stack_name(tenant.name, item)
        lines.append(f"{item} {name} {stack_state(cfn, name)}")
    return "\n".join(lines) + "\n"


def format_live_status(tenant: Tenant, cfn: Any, ssm: Any) -> str:
    parts = ["a", "b"]
    hub = " ".join(
        f"{part}={stack_state(cfn, hub_stack_name(tenant.name, part))}" for part in parts
    )
    lines = [f"stacks: {hub}"]
    if tenant.placement_peers:
        peers = " ".join(
            f"{peer_id}={stack_state(cfn, peer_stack_name(tenant.name, peer_id))}"
            for peer_id in peer_ids(tenant)
        )
        lines.append(f"peers: {peers}")
    else:
        lines.append("peers: (none)")
    prod = _ssm_summary(ssm, tenant.name, "production")
    staging = _ssm_summary(ssm, tenant.name, "staging")
    if prod:
        lines.append(f"ssm: {prod}")
    if staging and staging != prod:
        lines.append(f"ssm: {staging}")
    return "\n".join(lines) + "\n"


def _load_vars(ssm: Any, env_name: str, stage: str) -> dict[str, str]:
    name = ssm_platform_vars_path(env_name, stage)
    try:
        response = ssm.get_parameter(Name=name)
    except Exception as exc:
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str((response.get("Error") or {}).get("Code") or "")
        if code == "ParameterNotFound" or "ParameterNotFound" in str(exc):
            return {}
        raise
    raw = str((response.get("Parameter") or {}).get("Value") or "")
    if not raw:
        return {}
    payload = json.loads(raw)
    block = payload.get("VARS") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return {}
    return {str(key): str(value) for key, value in block.items()}


def _ssm_summary(ssm: Any, env_name: str, stage: str) -> str:
    vars_block = _load_vars(ssm, env_name, stage)
    if not vars_block:
        return f"{stage} (no platform-vars)"
    sender = vars_block.get("FROM_EMAIL") or "(no FROM_EMAIL)"
    base = vars_block.get("BASE_URL") or "(no BASE_URL)"
    return f"{stage} FROM_EMAIL={sender} BASE_URL={base}"


def _cloud_console_url(vars_block: dict[str, str]) -> str:
    return vars_block.get("FE_BASE_URL") or vars_block.get("AMPLIFY_CONSOLE_URL") or "(no FE_BASE_URL)"


def _format_url_stage(label: str, api: str, console: str) -> list[str]:
    return [f"urls {label}:", f"  api: {api}", f"  console: {console}"]


def _cloud_urls(ssm: Any | None, env_name: str, stage: str) -> tuple[str, str]:
    if ssm is None:
        missing = "(profile required for cloud URLs)"
        return missing, missing
    vars_block = _load_vars(ssm, env_name, stage)
    if not vars_block:
        missing = "(no platform-vars)"
        return missing, missing
    api = vars_block.get("BASE_URL") or "(no BASE_URL)"
    return api, _cloud_console_url(vars_block)


def format_status_urls(ssm: Any | None, env_name: str) -> str:
    """Cloud URLs from SSM (staging, production) and local development for renglo status."""
    lines: list[str] = []
    for stage in ("staging", "production"):
        api, console = _cloud_urls(ssm, env_name, stage)
        lines.extend(_format_url_stage(stage, api, console))
    lines.extend(_format_url_stage("development", _LOCAL_API, _LOCAL_CONSOLE))
    return "\n".join(lines) + "\n"


def format_state_live(ssm: Any, env_name: str) -> str:
    lines: list[str] = []
    for stage in ("staging", "production"):
        path = ssm_platform_vars_path(env_name, stage)
        vars_block = _load_vars(ssm, env_name, stage)
        lines.append(f"{stage} {path}")
        if not vars_block:
            lines.append("  (absent)")
            continue
        keys = [key for key in _URL_KEYS if key in vars_block]
        keys.extend(sorted(key for key in vars_block if key not in _URL_KEYS))
        for key in keys:
            lines.append(f"  {key}={vars_block[key]}")
    return "\n".join(lines) + "\n"


def refuse_destroy_a_while_b_exists(cfn: Any, env_name: str, which: list[str]) -> None:
    if "a" in which and "b" not in which:
        status = stack_state(cfn, hub_stack_name(env_name, "b"))
        if status not in {"ABSENT", "DELETE_COMPLETE"}:
            raise RengloOpsError(
                f"stack B is {status}. Destroy B first, or pass --stack a,b"
            )


def delete_stacks(cfn: Any, names: list[str]) -> list[str]:
    started: list[str] = []
    for name in names:
        status = stack_state(cfn, name)
        if status in {"ABSENT", "DELETE_COMPLETE"}:
            started.append(f"{name} {status}")
            continue
        cfn.delete_stack(StackName=name)
        started.append(f"{name} DELETE_IN_PROGRESS")
    return started


def ses_account(sesv2: Any) -> tuple[bool, str]:
    """Return (sandbox, detail)."""
    account = sesv2.get_account()
    production = bool(account.get("ProductionAccessEnabled"))
    return (not production), "production" if production else "sandbox"


def identity_status(ses: Any, identity: str) -> str:
    response = ses.get_identity_verification_attributes(Identities=[identity])
    attrs = (response.get("VerificationAttributes") or {}).get(identity) or {}
    return str(attrs.get("VerificationStatus") or "NotStarted")


def list_identities(ses: Any) -> list[tuple[str, str, str]]:
    """(identity, type, verification status)."""
    rows: list[tuple[str, str, str]] = []
    for kind, label in (("EmailAddress", "email"), ("Domain", "domain")):
        token = ""
        while True:
            kwargs: dict[str, Any] = {"IdentityType": kind, "MaxItems": 100}
            if token:
                kwargs["NextToken"] = token
            response = ses.list_identities(**kwargs)
            names = list(response.get("Identities") or [])
            if names:
                attrs = ses.get_identity_verification_attributes(Identities=names)
                table = attrs.get("VerificationAttributes") or {}
                for name in names:
                    status = str((table.get(name) or {}).get("VerificationStatus") or "NotStarted")
                    rows.append((name, label, status))
            token = str(response.get("NextToken") or "")
            if not token:
                break
    return rows


def verify_sender(ses: Any, tenant: Tenant) -> str:
    if tenant.email_identity == "domain":
        domain = tenant.email_from.split("@", 1)[-1]
        response = ses.verify_domain_identity(Domain=domain)
        token = str(response.get("VerificationToken") or "")
        detail = f"domain {domain}"
        if token:
            detail += f" token {token}"
        return detail
    ses.verify_email_identity(EmailAddress=tenant.email_from)
    return f"verification mail sent to {tenant.email_from}"


def allow_recipient(ses: Any, sesv2: Any, address: str) -> str:
    sandbox, mode = ses_account(sesv2)
    if not sandbox:
        return f"account is {mode}; allow is a no-op"
    ses.verify_email_identity(EmailAddress=address)
    return f"verification mail sent to {address}"


def platform_vars(ssm: Any, env_name: str, stage: str) -> dict[str, str]:
    return _load_vars(ssm, env_name, stage)


def console_base(stage_vars: dict[str, str], console: str) -> str:
    if console == "local":
        return _LOCAL_CONSOLE
    base = stage_vars.get("FE_BASE_URL") or stage_vars.get("AMPLIFY_CONSOLE_URL") or ""
    if not base:
        return _LOCAL_CONSOLE
    return base if base.endswith("/") else base + "/"


def admin_setup_url(base: str, email: str) -> str:
    return f"{base}invite?setup=admin&email={email}"


def cognito_admin_create(cognito: Any, pool_id: str, email: str) -> str:
    attributes = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
    ]
    try:
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=attributes,
            DesiredDeliveryMediums=["EMAIL"],
        )
        return "created"
    except Exception as exc:
        if "UsernameExistsException" not in type(exc).__name__ and "UsernameExistsException" not in str(exc):
            raise
    user = cognito.admin_get_user(UserPoolId=pool_id, Username=email)
    status = str(user.get("UserStatus") or "")
    if status == "FORCE_CHANGE_PASSWORD":
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            MessageAction="RESEND",
            DesiredDeliveryMediums=["EMAIL"],
        )
        return "resent"
    cognito.admin_reset_user_password(UserPoolId=pool_id, Username=email)
    return "reset"


def cognito_admin_show(cognito: Any, pool_id: str, email: str) -> str:
    try:
        user = cognito.admin_get_user(UserPoolId=pool_id, Username=email)
    except Exception as exc:
        if "UserNotFoundException" in type(exc).__name__ or "UserNotFoundException" in str(exc):
            return f"{email} absent"
        raise
    enabled = "enabled" if user.get("Enabled", True) else "disabled"
    return f"{email} {user.get('UserStatus') or 'UNKNOWN'} {enabled}"


def cognito_id_token(cognito: Any, client_id: str, email: str, password: str) -> str:
    response = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
        ClientId=client_id,
    )
    token = str((response.get("AuthenticationResult") or {}).get("IdToken") or "")
    if not token:
        raise RengloOpsError("Cognito did not return an IdToken")
    return token


def invite_user(api_url: str, token: str, email: str, team: str, portfolio: str) -> str:
    base = api_url.rstrip("/")
    url = f"{base}/_auth/user/invite"
    body = json.dumps({"email": email, "team_id": team, "portfolio_id": portfolio}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode()
            code = response.status
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        code = exc.code
    if code >= 400:
        raise RengloOpsError(f"invite failed ({code}): {payload.strip()[:300]}")
    return payload.strip() or f"invited {email}"
