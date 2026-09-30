"""One-time import from customer-config, deploy_targets, platform env, and publisher-config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.registry import Registry, dump_registry
from renglo_ops.model.tenant import Account, RegistryRef, Tenant, dump_tenant


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RengloOpsError(f"{path} must be a JSON object")
    return {key: value for key, value in data.items() if not str(key).startswith("_")}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RengloOpsError(f"{path} must be a mapping")
    return data


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RengloOpsError("expected a list")
    return [str(item).strip() for item in value if str(item).strip()]


def import_legacy(
    *,
    customer_config: Path | None = None,
    deploy_targets: Path | None = None,
    platform_env: Path | None = None,
    platform_defaults: Path | None = None,
    publisher_config: Path | None = None,
) -> tuple[Tenant, Registry | None]:
    customer = _load_json(customer_config) if customer_config else {}
    targets = _load_yaml(deploy_targets) if deploy_targets else {}
    env_file = _load_yaml(platform_env) if platform_env else {}
    defaults_file = _load_json(platform_defaults) if platform_defaults else {}

    name = str(customer.get("env_name") or "").strip()
    tenants = targets.get("tenants") or {}
    if not isinstance(tenants, dict):
        raise RengloOpsError("deploy_targets.yml tenants must be a mapping")
    tenant_row = tenants.get(name) if name else None
    if not isinstance(tenant_row, dict) and len(tenants) == 1:
        name = next(iter(tenants))
        tenant_row = tenants[name]
    if not isinstance(tenant_row, dict):
        tenant_row = {}
    if not name:
        raise RengloOpsError("could not resolve tenant name from env_name or tenants")

    github_repo = str(customer.get("github_repo") or "").strip()
    if not github_repo:
        raise RengloOpsError("customer-config.json github_repo is required")

    stages = tenant_row.get("stages") or {}
    if not isinstance(stages, dict):
        stages = {}
    staging_cfg = stages.get("staging") if isinstance(stages.get("staging"), dict) else {}
    production_cfg = stages.get("production") if isinstance(stages.get("production"), dict) else {}
    enable_staging = customer.get("enable_staging", staging_cfg.get("enabled", True))
    account = str(tenant_row.get("aws_account") or "").strip()
    region = str(tenant_row.get("aws_region") or "us-east-1").strip() or "us-east-1"

    domain_owners = _string_list((customer.get("package_registry") or {}).get("domain_owners"))
    registries = []
    seen_accounts: set[str] = set()
    for raw in targets.get("registries") or []:
        if not isinstance(raw, dict):
            continue
        account_id = str(raw.get("domain_owner") or raw.get("account") or "").strip()
        if account_id:
            seen_accounts.add(account_id)
        registries.append(
            {
                "domain": str(raw.get("domain") or "").strip(),
                "python": str(raw.get("python_repository") or raw.get("python") or "python-store"),
                "npm": str(raw.get("npm_repository") or raw.get("npm") or "npm-store"),
                "account": account_id,
                "scopes": _string_list(raw.get("npm_scopes") or raw.get("scopes")),
            }
        )
    for owner in domain_owners:
        if owner not in seen_accounts:
            registries.append(
                {
                    "domain": "",
                    "python": "python-store",
                    "npm": "npm-store",
                    "account": owner,
                    "scopes": [],
                }
            )
    registries = [row for row in registries if row["domain"] or row["account"]]

    hub = (targets.get("hub") or {}).get("python") or []
    if not isinstance(hub, list):
        hub = []
    peers = targets.get("peers") or {}
    if not isinstance(peers, dict):
        peers = {}
    cognito = defaults_file.get("cognito") or {}
    hours = 24
    if isinstance(cognito, dict) and cognito.get("token_validity_hours"):
        hours = int(cognito["token_validity_hours"])
    helper = targets.get("helper") or {}
    platform = str(helper.get("ref") or helper.get("version") or "").strip()

    tenant = Tenant(
        name=str(name),
        github_repo=github_repo,
        github_owner_id=str(customer.get("github_owner_id") or "").strip(),
        github_repo_id=str(customer.get("github_repo_id") or "").strip(),
        email_from=str(customer.get("email_from") or "").strip(),
        email_identity=str(customer.get("email_identity_type") or "email").strip(),
        email_hosted_zone_id=str(customer.get("email_hosted_zone_id") or "").strip(),
        platform=platform,
        accounts={
            "staging": Account(
                id=account,
                region=region,
                enabled=enable_staging is not False,
            ),
            "production": Account(
                id=account if production_cfg.get("enabled") else "",
                region=region,
                enabled=production_cfg.get("enabled") is True,
            ),
        },
        registries=[
            RegistryRef(
                domain=row["domain"] or row["account"],
                python=row["python"],
                npm=row["npm"],
                account=row["account"],
                scopes=row["scopes"],
            )
            for row in registries
        ],
    )
    tenant.placement_hub = [str(item).strip() for item in hub if str(item).strip()]
    tenant.placement_peers = peers
    packages = targets.get("packages") or {}
    tenant.packages = packages if isinstance(packages, dict) else {}
    tenant.release_bom = str(targets.get("bom") or "").strip()
    tenant.release_console = str(targets.get("console_bom") or "").strip()
    tenant.env = {str(key): str(value) for key, value in env_file.items() if value is not None}
    tenant.architecture = str(defaults_file.get("architecture") or "x86_64").strip() or "x86_64"
    tenant.cognito_token_hours = hours

    registry = None
    if publisher_config and publisher_config.is_file():
        raw = _load_json(publisher_config)
        registry = Registry(
            name=str(raw.get("publisher_name") or "").strip(),
            github_org=str(raw.get("github_org") or "").strip(),
            publish_repos=_string_list(raw.get("github_publish_repos")) or ["*"],
            reader_accounts=_string_list(raw.get("reader_aws_accounts")),
            python=str(raw.get("python_repository") or "python-store").strip() or "python-store",
            npm=str(raw.get("npm_repository") or "npm-store").strip() or "npm-store",
        )
        if not registry.name or not registry.github_org:
            raise RengloOpsError("publisher-config.json requires publisher_name and github_org")
    return tenant, registry


def write_import(
    tenant: Tenant,
    registry: Registry | None,
    *,
    tenant_path: Path,
    registry_path: Path | None = None,
) -> list[Path]:
    tenant_path.parent.mkdir(parents=True, exist_ok=True)
    tenant_path.write_text(dump_tenant(tenant), encoding="utf-8")
    written = [tenant_path]
    if registry is not None and registry_path is not None:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(dump_registry(registry), encoding="utf-8")
        written.append(registry_path)
    return written
