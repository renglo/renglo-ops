"""Resolve CodeArtifact registry login targets from deploy_targets data."""

from __future__ import annotations

DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_REGISTRY = {
    "domain": "renglo",
    "python_repository": "python-store",
    "npm_repository": "npm-store",
}


def _first_tenant_account_region(data: dict) -> tuple[str, str]:
    tenants = data.get("tenants") or {}
    if not isinstance(tenants, dict):
        return "", ""
    owner = ""
    region = ""
    for tenant_cfg in tenants.values():
        if not isinstance(tenant_cfg, dict):
            continue
        if not owner:
            owner = str(tenant_cfg.get("aws_account") or "").strip()
        if not region:
            region = str(tenant_cfg.get("aws_region") or "").strip()
        if owner and region:
            break
    return owner, region


def _internal_default_registry(
    data: dict,
    *,
    domain_override: str = "",
    owner_override: str = "",
) -> dict[str, object]:
    tenant_owner, tenant_region = _first_tenant_account_region(data)
    owner = (owner_override or tenant_owner).strip()
    if not owner:
        raise RuntimeError(
            "deploy_targets.yml: set tenants.*.aws_account (needed for internal CodeArtifact default)"
        )
    domain = (domain_override or DEFAULT_REGISTRY["domain"]).strip() or DEFAULT_REGISTRY["domain"]
    return {
        "domain": domain,
        "domain_owner": owner,
        "python_repository": DEFAULT_REGISTRY["python_repository"],
        "npm_repository": DEFAULT_REGISTRY["npm_repository"],
        "region": tenant_region or DEFAULT_AWS_REGION,
        "npm_scopes": [],
    }


def _normalize_registry_entry(
    raw: dict,
    *,
    default_owner: str,
    default_region: str,
) -> dict[str, object]:
    domain = str(raw.get("domain") or DEFAULT_REGISTRY["domain"]).strip() or DEFAULT_REGISTRY["domain"]
    python_repository = (
        str(raw.get("python_repository") or DEFAULT_REGISTRY["python_repository"]).strip()
        or DEFAULT_REGISTRY["python_repository"]
    )
    npm_repository = (
        str(raw.get("npm_repository") or DEFAULT_REGISTRY["npm_repository"]).strip()
        or DEFAULT_REGISTRY["npm_repository"]
    )
    owner = str(raw.get("domain_owner") or "").strip() or default_owner
    region = str(raw.get("region") or "").strip() or default_region or DEFAULT_AWS_REGION
    if not owner:
        raise RuntimeError(
            "deploy_targets.yml: set registries[].domain_owner or tenants.*.aws_account"
        )
    scopes_raw = raw.get("npm_scopes") or []
    if scopes_raw is None:
        scopes_raw = []
    if not isinstance(scopes_raw, list):
        raise RuntimeError("deploy_targets.yml: registries[].npm_scopes must be a list")
    npm_scopes = [str(s).strip() for s in scopes_raw if str(s).strip()]
    return {
        "domain": domain,
        "domain_owner": owner,
        "python_repository": python_repository,
        "npm_repository": npm_repository,
        "region": region,
        "npm_scopes": npm_scopes,
    }


def resolve_registries(
    data: dict,
    *,
    domain_override: str = "",
    owner_override: str = "",
) -> list[dict[str, object]]:
    """CodeArtifact login targets for pip/npm (vendor mosaic).

    - Singular ``registry:`` is rejected; use ``registries:`` (list).
    - Missing or empty ``registries`` → one same-account internal default.
    - Foreign-only lists prepend the internal default when no entry targets
      the tenant account.
    """
    if "registry" in data:
        raise RuntimeError(
            "deploy_targets.yml: 'registry' is not supported; use 'registries' (list)"
        )

    tenant_owner, tenant_region = _first_tenant_account_region(data)
    internal = _internal_default_registry(
        data,
        domain_override=domain_override,
        owner_override=owner_override,
    )

    raw = data.get("registries", None)
    if raw is None or raw == []:
        if domain_override:
            internal["domain"] = domain_override.strip() or internal["domain"]
        if owner_override:
            internal["domain_owner"] = owner_override.strip() or internal["domain_owner"]
        return [internal]

    if not isinstance(raw, list):
        raise RuntimeError("deploy_targets.yml: registries must be a list")

    default_owner = tenant_owner
    default_region = tenant_region or DEFAULT_AWS_REGION
    entries: list[dict[str, object]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"deploy_targets.yml: registries[{index}] must be a mapping")
        entries.append(
            _normalize_registry_entry(
                item,
                default_owner=default_owner,
                default_region=default_region,
            )
        )

    has_internal = any(str(e.get("domain_owner") or "") == tenant_owner for e in entries)
    if tenant_owner and not has_internal:
        entries = [internal, *entries]

    if entries and domain_override:
        entries[0] = {**entries[0], "domain": domain_override.strip() or entries[0]["domain"]}
    if entries and owner_override:
        entries[0] = {
            **entries[0],
            "domain_owner": owner_override.strip() or entries[0]["domain_owner"],
        }
    return entries


def resolve_registry(
    data: dict,
    *,
    domain_override: str = "",
    owner_override: str = "",
) -> dict[str, object]:
    """First resolved registry (default pip/npm index)."""
    registries = resolve_registries(
        data,
        domain_override=domain_override,
        owner_override=owner_override,
    )
    return registries[0]
