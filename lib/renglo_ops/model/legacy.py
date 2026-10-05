"""Project a Tenant into the dict shape older CDK and release modules still read.

This is an in-memory bridge. It is not a second file format operators edit.
"""

from __future__ import annotations

from typing import Any

from renglo_ops.model.tenant import Tenant


def customer_config_dict(tenant: Tenant) -> dict[str, Any]:
    staging = tenant.accounts.get("staging")
    return {
        "env_name": tenant.name,
        "github_repo": tenant.github_repo,
        "github_owner_id": tenant.github_owner_id,
        "github_repo_id": tenant.github_repo_id,
        "enable_staging": bool(staging and staging.enabled),
        "email_from": tenant.email_from,
        "email_identity_type": tenant.email_identity,
        "email_hosted_zone_id": tenant.email_hosted_zone_id,
        "package_registry": {"domain_owners": tenant.foreign_domain_owners()},
    }


def deploy_targets_dict(tenant: Tenant, *, stage: str = "") -> dict[str, Any]:
    primary = tenant.primary_account()
    staging = tenant.accounts.get("staging")
    production = tenant.accounts.get("production")
    registries = []
    for item in tenant.registries:
        row: dict[str, Any] = {
            "domain": item.domain,
            "python_repository": item.python,
            "npm_repository": item.npm,
        }
        if item.account:
            row["domain_owner"] = item.account
        if item.scopes:
            row["npm_scopes"] = list(item.scopes)
        registries.append(row)
    projected = {
        "bom": tenant.release_bom,
        "console_bom": tenant.release_console,
        "hub": {"python": list(tenant.placement_hub)},
        "helper": {"package": "renglo-ops", "version": tenant.platform},
        "registries": registries,
        "packages": tenant.packages,
        "peers": tenant.placement_peers,
        "tenants": {
            tenant.name: {
                "aws_account": primary.id,
                "aws_region": primary.region,
                "stages": {
                    "staging": {"enabled": bool(staging and staging.enabled)},
                    "production": {"enabled": bool(production and production.enabled)},
                },
            }
        },
    }
    pins = _staging_pins(tenant)
    if pins:
        projected["staging_pins"] = pins
    _apply_stage(projected, stage)
    return projected


def _staging_pins(tenant: Tenant) -> dict[str, Any]:
    pins = tenant.staging
    if pins is None or not pins.active():
        return {}
    return {
        "bom": pins.bom,
        "console_bom": pins.console,
        "platform": pins.platform,
        "peers": dict(pins.peers),
    }


def _apply_stage(projected: dict[str, Any], stage: str) -> None:
    """Point the catalog's release fields at the staging block for a staging run."""
    if stage != "staging":
        return
    pins = projected.get("staging_pins")
    if not isinstance(pins, dict) or not pins:
        return
    if pins.get("bom"):
        projected["bom"] = pins["bom"]
    if pins.get("console_bom"):
        projected["console_bom"] = pins["console_bom"]
    if pins.get("platform"):
        helper = projected.get("helper")
        if isinstance(helper, dict):
            helper["version"] = pins["platform"]
