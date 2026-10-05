"""Tenant desired state: renglo.yaml in the tenant repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from renglo_ops.model.errors import RengloOpsError

_IDENTITIES = ("email", "domain")


@dataclass
class Account:
    id: str = ""
    region: str = "us-east-1"
    enabled: bool = False


@dataclass
class RegistryRef:
    domain: str
    python: str = "python-store"
    npm: str = "npm-store"
    account: str = ""
    scopes: list[str] = field(default_factory=list)


@dataclass
class StagingPins:
    """Candidate release. Production keys stay in place until this block is promoted."""

    platform: str = ""
    bom: str = ""
    console: str = ""
    peers: dict[str, str] = field(default_factory=dict)

    def active(self) -> bool:
        return bool(self.platform or self.bom or self.console or self.peers)


@dataclass
class Tenant:
    name: str
    github_repo: str
    email_from: str
    email_identity: str
    github_owner_id: str = ""
    github_repo_id: str = ""
    email_hosted_zone_id: str = ""
    platform: str = ""
    staging: StagingPins | None = None
    accounts: dict[str, Account] = field(default_factory=dict)
    registries: list[RegistryRef] = field(default_factory=list)
    placement_hub: list[str] = field(default_factory=list)
    placement_peers: dict[str, Any] = field(default_factory=dict)
    packages: dict[str, Any] = field(default_factory=dict)
    release_bom: str = ""
    release_console: str = ""
    env: dict[str, str] = field(default_factory=dict)
    architecture: str = "x86_64"
    cognito_token_hours: int = 24
    path: Path | None = None

    def account_ids(self) -> set[str]:
        return {item.id for item in self.accounts.values() if item.id}

    def foreign_domain_owners(self) -> list[str]:
        """Publisher accounts this tenant pulls from, excluding its own accounts."""
        own = self.account_ids()
        owners: list[str] = []
        for registry in self.registries:
            account = registry.account.strip()
            if account and account not in own and account not in owners:
                owners.append(account)
        return owners

    def primary_account(self) -> Account:
        for key in ("staging", "production"):
            item = self.accounts.get(key)
            if item and item.enabled and item.id:
                return item
        for item in self.accounts.values():
            if item.id:
                return item
        return Account()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RengloOpsError(f"{label} must be a mapping")
    return value


def _account(raw: Any, label: str) -> Account:
    data = _mapping(raw, label)
    return Account(
        id=str(data.get("id") or "").strip(),
        region=str(data.get("region") or "us-east-1").strip() or "us-east-1",
        enabled=bool(data.get("enabled", False)),
    )


def _staging_pins(raw: Any) -> StagingPins | None:
    if raw is None:
        return None
    data = _mapping(raw, "staging")
    if not data:
        return None
    peers_raw = data.get("peers") or {}
    if not isinstance(peers_raw, dict):
        raise RengloOpsError("staging.peers must be a mapping")
    peers: dict[str, str] = {}
    for key, value in peers_raw.items():
        peer_id = str(key).strip()
        if not peer_id:
            continue
        if isinstance(value, dict):
            version = str(value.get("peers_bom") or "").strip()
        else:
            version = str(value or "").strip()
        if version:
            peers[peer_id] = version
    pins = StagingPins(
        platform=str(data.get("platform") or "").strip(),
        bom=str(data.get("bom") or "").strip(),
        console=str(data.get("console") or "").strip(),
        peers=peers,
    )
    if not pins.active():
        return None
    return pins


def tenant_from_dict(data: dict[str, Any], *, path: Path | None = None) -> Tenant:
    if not isinstance(data, dict):
        raise RengloOpsError("renglo.yaml must be a mapping")
    name = str(data.get("name") or "").strip()
    github = _mapping(data.get("github"), "github")
    repo = str(github.get("repo") or "").strip()
    email = _mapping(data.get("email"), "email")
    email_from = str(email.get("from") or "").strip()
    identity = str(email.get("identity") or "").strip()
    missing = [
        label
        for label, value in (
            ("name", name),
            ("github.repo", repo),
            ("email.from", email_from),
            ("email.identity", identity),
        )
        if not value
    ]
    if missing:
        raise RengloOpsError("renglo.yaml missing " + ", ".join(missing))
    if identity not in _IDENTITIES:
        raise RengloOpsError("email.identity must be email or domain")
    accounts = {
        str(key): _account(value, f"accounts.{key}")
        for key, value in _mapping(data.get("accounts"), "accounts").items()
    }
    registries: list[RegistryRef] = []
    raw_registries = data.get("registries") or []
    if not isinstance(raw_registries, list):
        raise RengloOpsError("registries must be a list")
    for index, item in enumerate(raw_registries):
        row = _mapping(item, f"registries[{index}]")
        domain = str(row.get("domain") or "").strip()
        if not domain:
            raise RengloOpsError(f"registries[{index}].domain is required")
        scopes = row.get("scopes") or []
        if not isinstance(scopes, list):
            raise RengloOpsError(f"registries[{index}].scopes must be a list")
        registries.append(
            RegistryRef(
                domain=domain,
                python=str(row.get("python") or "python-store").strip() or "python-store",
                npm=str(row.get("npm") or "npm-store").strip() or "npm-store",
                account=str(row.get("account") or "").strip(),
                scopes=[str(scope).strip() for scope in scopes if str(scope).strip()],
            )
        )
    placement = _mapping(data.get("placement"), "placement")
    hub = placement.get("hub") or []
    if not isinstance(hub, list):
        raise RengloOpsError("placement.hub must be a list")
    peers = placement.get("peers") or {}
    if not isinstance(peers, dict):
        raise RengloOpsError("placement.peers must be a mapping")
    release = _mapping(data.get("release"), "release")
    staging = _staging_pins(data.get("staging"))
    defaults = _mapping(data.get("defaults"), "defaults")
    env = _mapping(data.get("env"), "env")
    hours = int(defaults.get("cognito_token_hours") or 24)
    if hours < 1 or hours > 24:
        raise RengloOpsError("defaults.cognito_token_hours must be between 1 and 24")
    return Tenant(
        name=name,
        github_repo=repo,
        github_owner_id=str(github.get("owner_id") or "").strip(),
        github_repo_id=str(github.get("repo_id") or "").strip(),
        email_from=email_from,
        email_identity=identity,
        email_hosted_zone_id=str(email.get("hosted_zone_id") or "").strip(),
        platform=str(data.get("platform") or "").strip(),
        staging=staging,
        accounts=accounts,
        registries=registries,
        placement_hub=[str(item).strip() for item in hub if str(item).strip()],
        placement_peers=peers,
        packages=_mapping(data.get("packages"), "packages"),
        release_bom=str(release.get("bom") or "").strip(),
        release_console=str(release.get("console") or "").strip(),
        env={str(key): str(value) for key, value in env.items()},
        architecture=str(defaults.get("architecture") or "x86_64").strip() or "x86_64",
        cognito_token_hours=hours,
        path=path,
    )


def tenant_to_dict(tenant: Tenant) -> dict[str, Any]:
    github: dict[str, str] = {"repo": tenant.github_repo}
    if tenant.github_owner_id:
        github["owner_id"] = tenant.github_owner_id
    if tenant.github_repo_id:
        github["repo_id"] = tenant.github_repo_id
    email: dict[str, str] = {
        "from": tenant.email_from,
        "identity": tenant.email_identity,
    }
    if tenant.email_hosted_zone_id:
        email["hosted_zone_id"] = tenant.email_hosted_zone_id
    accounts = {
        key: {"id": item.id, "region": item.region, "enabled": item.enabled}
        for key, item in tenant.accounts.items()
    }
    registries = []
    for item in tenant.registries:
        row: dict[str, Any] = {
            "domain": item.domain,
            "python": item.python,
            "npm": item.npm,
        }
        if item.account:
            row["account"] = item.account
        if item.scopes:
            row["scopes"] = list(item.scopes)
        registries.append(row)
    document = {
        "name": tenant.name,
        "github": github,
        "email": email,
        "platform": tenant.platform,
        "accounts": accounts,
        "registries": registries,
        "placement": {"hub": list(tenant.placement_hub), "peers": tenant.placement_peers},
        "packages": tenant.packages,
        "release": {"bom": tenant.release_bom, "console": tenant.release_console},
        "env": tenant.env,
        "defaults": {
            "architecture": tenant.architecture,
            "cognito_token_hours": tenant.cognito_token_hours,
        },
    }
    if tenant.staging and tenant.staging.active():
        staging: dict[str, Any] = {}
        if tenant.staging.platform:
            staging["platform"] = tenant.staging.platform
        if tenant.staging.bom:
            staging["bom"] = tenant.staging.bom
        if tenant.staging.console:
            staging["console"] = tenant.staging.console
        if tenant.staging.peers:
            staging["peers"] = dict(tenant.staging.peers)
        document["staging"] = staging
    return document


def dump_tenant(tenant: Tenant) -> str:
    return yaml.safe_dump(tenant_to_dict(tenant), sort_keys=False)


def load_tenant(path: Path) -> Tenant:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise RengloOpsError(f"renglo.yaml not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tenant_from_dict(data, path=path)


def find_tenant_file(start: Path) -> Path:
    """Walk up for renglo.yaml, then for .renglo/local.yaml's tenant path."""
    from renglo_ops.model.local import load_local

    here = start.expanduser().resolve()
    for candidate in [here, *here.parents]:
        direct = candidate / "renglo.yaml"
        if direct.is_file():
            return direct
        local_path = candidate / ".renglo" / "local.yaml"
        if local_path.is_file():
            local = load_local(local_path)
            tenant = (candidate / local.tenant).resolve() / "renglo.yaml"
            if tenant.is_file():
                return tenant
            raise RengloOpsError(f"tenant file not found: {tenant}")
    raise RengloOpsError(
        "renglo.yaml not found. Run from the tenant repo or a workspace with .renglo/local.yaml."
    )
