"""Fill deploy_targets defaults from the workspace.

``renglo config align`` calls this after the layout normalize step.
It does not choose hub vs peer placement for a project extension.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Renglo CodeArtifact account. npm @renglo/* is published here, not in the tenant domain.
RENGLO_DOMAIN_OWNER = "339713094352"

HUB_PYTHON_REQUIRED = ("renglo-data", "renglo-schd")

# Always present. Other renglo extensions stay out until someone places them.
DEFAULT_PACKAGE_SLOTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("data", {"python": "renglo-data", "npm": "@renglo/data"}),
    ("schd", {"python": "renglo-schd", "npm": "@renglo/schd"}),
)

_CORE_IDS = ("renglo-lib", "renglo-api", "console")
_PYPROJECT_NAME = re.compile(r"(?m)^name\s*=\s*['\"]([^'\"]+)['\"]")
_GIT_URL = re.compile(r"(?m)^[ \t]*url\s*=\s*(\S+)\s*$")
_ORIGIN = re.compile(r"[:/](?P<org>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?$")
_PLACEHOLDER_IDS = {"example", "example0000"}
_PLACEHOLDER_ACCOUNTS = {"", "000000000000"}


def apply_workspace(
    model: dict[str, Any],
    *,
    bom_root: Path,
    ops_root: Path | None,
    tenant: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Mutate ``model``. Returns ``(details, attention)``."""
    details: list[str] = []
    attention: list[str] = []
    publisher = _publisher_name(bom_root)
    workspace = _workspace_root(ops_root, bom_root)
    customer = _customer_config(ops_root)

    _ensure_hub_python(model, details)
    _ensure_default_packages(model, publisher, workspace, details, attention)
    _ensure_registries(model, publisher, details)
    _sync_tenant(model, customer, tenant or {}, details, attention)
    _attention_unplaced(model, attention)
    return details, attention


def ensure_renglo_domain_owner(
    ops_root: Path | None,
    publisher: str,
    *,
    apply: bool,
) -> list[str]:
    """Tenant IAM must be allowed to read the foreign renglo CodeArtifact domain."""
    if not publisher or publisher == "renglo" or ops_root is None:
        return []
    path = ops_root / "launcher" / "cdk" / "customer-config.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    registry = data.get("package_registry")
    if not isinstance(registry, dict):
        registry = {}
        data["package_registry"] = registry
    owners = registry.get("domain_owners")
    if not isinstance(owners, list):
        owners = []
    texts = [str(item).strip() for item in owners if str(item).strip()]
    if RENGLO_DOMAIN_OWNER in texts:
        return []
    texts.append(RENGLO_DOMAIN_OWNER)
    registry["domain_owners"] = texts
    if apply:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return [f"package_registry.domain_owners includes {RENGLO_DOMAIN_OWNER}"]


def publisher_name(bom_root: Path) -> str:
    return _publisher_name(bom_root)


def _publisher_name(bom_root: Path) -> str:
    name = bom_root.name
    if name.endswith("-bom") and name != "-bom":
        return name[: -len("-bom")]
    return ""


def _workspace_root(ops_root: Path | None, bom_root: Path) -> Path | None:
    if ops_root is not None and (ops_root.parent / "extensions").is_dir():
        return ops_root.parent
    if (bom_root.parent.parent / "extensions").is_dir():
        return bom_root.parent.parent
    return None


def _customer_config(ops_root: Path | None) -> dict[str, Any]:
    if ops_root is None:
        return {}
    path = ops_root / "launcher" / "cdk" / "customer-config.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_hub_python(model: dict[str, Any], details: list[str]) -> None:
    current = [str(name) for name in (model.get("hub_python") or [])]
    missing = [name for name in HUB_PYTHON_REQUIRED if name not in current]
    if not missing:
        return
    model["hub_python"] = [*HUB_PYTHON_REQUIRED, *[name for name in current if name not in HUB_PYTHON_REQUIRED]]
    details.append("hub.python includes " + ", ".join(missing))


def _ensure_default_packages(
    model: dict[str, Any],
    publisher: str,
    workspace: Path | None,
    details: list[str],
    attention: list[str],
) -> None:
    packages: dict[str, dict[str, str]] = dict(model.get("packages") or {})
    if publisher:
        _ensure_slot(packages, _wl_slot(publisher, workspace), details)
    for slot_id, fields in DEFAULT_PACKAGE_SLOTS:
        _ensure_slot(packages, {"id": slot_id, **fields}, details)
    if workspace is not None:
        for slot in _project_slots(workspace):
            _ensure_slot(packages, slot, details)
            scope = _npm_scope(slot.get("npm") or "")
            npm = slot.get("npm") or ""
            if scope == "@renglo" and not str(slot.get("python") or "").startswith("renglo-"):
                attention.append(
                    f"packages.{slot['id']} npm is {npm}; that scope is fetched from the renglo registry"
                )
            elif scope and publisher and scope not in {f"@{publisher}", "@renglo"}:
                attention.append(f"packages.{slot['id']} npm scope {scope} is not @{publisher}")
    model["packages"] = _order_packages(packages, publisher)


def _ensure_slot(packages: dict[str, dict[str, str]], slot: dict[str, str], details: list[str]) -> None:
    slot_id = slot["id"]
    fields = {key: slot[key] for key in ("python", "npm", "repo") if slot.get(key)}
    current = packages.get(slot_id)
    if current is None:
        packages[slot_id] = fields
        details.append(f"packages.{slot_id} added")
        return
    for key, value in fields.items():
        if key not in current:
            current[key] = value
            details.append(f"packages.{slot_id}.{key} set to {value}")


def _order_packages(packages: dict[str, dict[str, str]], publisher: str) -> dict[str, dict[str, str]]:
    preferred = list(_CORE_IDS)
    if publisher:
        preferred.append(f"{publisher}-wl")
    preferred.extend(slot_id for slot_id, _fields in DEFAULT_PACKAGE_SLOTS)
    ordered: dict[str, dict[str, str]] = {}
    for slot_id in preferred:
        if slot_id in packages:
            ordered[slot_id] = packages[slot_id]
    for slot_id, fields in packages.items():
        if slot_id not in ordered:
            ordered[slot_id] = fields
    return ordered


def _wl_slot(publisher: str, workspace: Path | None) -> dict[str, str]:
    slot_id = f"{publisher}-wl"
    slot = {"id": slot_id, "python": slot_id, "npm": f"@{publisher}/wl"}
    if workspace is None:
        return slot
    for folder in (workspace / "dev" / slot_id, workspace / slot_id):
        package_json = folder / "package.json"
        if not package_json.is_file():
            continue
        npm = _package_json_name(package_json)
        if npm:
            slot["npm"] = npm
        pyproject = folder / "pyproject.toml"
        if pyproject.is_file():
            name = _pyproject_name(pyproject)
            if name:
                slot["python"] = name
        break
    return slot


def _project_slots(workspace: Path) -> list[dict[str, str]]:
    root = workspace / "extensions"
    if not root.is_dir():
        return []
    slots: list[dict[str, str]] = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        if _origin_org(folder) == "renglo":
            continue
        py_name = _pyproject_name(folder / "package" / "pyproject.toml")
        npm_name = _package_json_name(folder / "ui" / "package.json")
        if not py_name and not npm_name:
            continue
        if py_name.startswith("renglo-") and (_origin_org(folder) in {"", "renglo"}):
            continue
        slot: dict[str, str] = {"id": folder.name}
        if py_name:
            slot["python"] = py_name
        if npm_name:
            slot["npm"] = npm_name
        slots.append(slot)
    return slots


def _origin_org(folder: Path) -> str:
    config = folder / ".git" / "config"
    if not config.is_file():
        return ""
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    in_origin = False
    url = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped == '[remote "origin"]'
            continue
        if not in_origin:
            continue
        match = _GIT_URL.match(line)
        if match:
            url = match.group(1).strip().strip('"')
            break
    found = _ORIGIN.search(url)
    if not found:
        return ""
    return found.group("org").lower()


def _pyproject_name(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _PYPROJECT_NAME.search(text)
    return match.group(1).strip() if match else ""


def _package_json_name(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("name") or "").strip()


def _npm_scope(name: str) -> str:
    if name.startswith("@") and "/" in name:
        return name.split("/", 1)[0]
    return ""


def _blank_registry(domain: str, scopes: list[str]) -> dict[str, Any]:
    return {
        "domain": domain,
        "python_repository": "python-store",
        "npm_repository": "npm-store",
        "npm_scopes": list(scopes),
    }


def _merge_scopes(entry: dict[str, Any], scopes: list[str], details: list[str], label: str) -> None:
    current = [str(item) for item in (entry.get("npm_scopes") or [])]
    added = [scope for scope in scopes if scope not in current]
    if not added and "npm_scopes" in entry:
        return
    entry["npm_scopes"] = [*current, *added]
    if added:
        details.append(f"registries {label} npm_scopes includes " + ", ".join(added))


def _ensure_registries(model: dict[str, Any], publisher: str, details: list[str]) -> None:
    if not publisher:
        return
    raw = model.get("registries")
    entries: list[dict[str, Any]] = [dict(item) for item in raw] if isinstance(raw, list) else []
    if raw is None:
        details.append("added registries")

    if publisher == "renglo":
        renglo = _take_domain(entries, "renglo")
        if renglo is None:
            renglo = _blank_registry("renglo", ["@renglo"])
            details.append("registries domain renglo added")
        _merge_scopes(renglo, ["@renglo"], details, "renglo")
        model["registries"] = [renglo, *entries]
        return

    own = _take_domain(entries, publisher)
    if own is None:
        own = _blank_registry(publisher, [f"@{publisher}"])
        details.append(f"registries domain {publisher} added")
    _merge_scopes(own, [f"@{publisher}"], details, publisher)
    renglo = _take_domain(entries, "renglo")
    if renglo is None:
        renglo = _blank_registry("renglo", ["@renglo"])
        details.append("registries domain renglo added")
    _merge_scopes(renglo, ["@renglo"], details, "renglo")
    if not str(renglo.get("domain_owner") or "").strip():
        renglo["domain_owner"] = RENGLO_DOMAIN_OWNER
        details.append(f"registries renglo domain_owner set to {RENGLO_DOMAIN_OWNER}")
    model["registries"] = [own, renglo, *entries]


def _take_domain(entries: list[dict[str, Any]], domain: str) -> dict[str, Any] | None:
    for index, entry in enumerate(entries):
        if str(entry.get("domain") or "") == domain:
            return entries.pop(index)
    return None


def _sync_tenant(
    model: dict[str, Any],
    customer: dict[str, Any],
    tenant: dict[str, Any],
    details: list[str],
    attention: list[str],
) -> None:
    env = str(tenant.get("env_name") or customer.get("env_name") or "").strip()
    if not env:
        return
    account = str(tenant.get("aws_account") or "").strip()
    region = str(tenant.get("aws_region") or "").strip()
    if "enable_staging" in tenant:
        staging = bool(tenant.get("enable_staging"))
    else:
        staging = bool(customer.get("enable_staging", True))

    tenants: dict[str, dict[str, Any]] = dict(model.get("tenants") or {})
    for key, row in list(tenants.items()):
        acct = str(row.get("aws_account") or "").strip()
        if key in _PLACEHOLDER_IDS and key != env and acct in _PLACEHOLDER_ACCOUNTS:
            tenants.pop(key, None)
            details.append(f"dropped placeholder tenants.{key}")

    row = tenants.get(env)
    if row is None:
        if not region:
            region = "us-east-1"
        tenants[env] = {
            "aws_account": account,
            "aws_region": region,
            "stages": {
                "staging": {"enabled": staging},
                "production": {"enabled": False},
            },
        }
        details.append(f"tenants.{env} added")
    else:
        current = str(row.get("aws_account") or "").strip()
        if account and current in _PLACEHOLDER_ACCOUNTS:
            row["aws_account"] = account
            details.append(f"tenants.{env}.aws_account set from stack A")
        elif account and current and current != account:
            attention.append(
                f"tenants.{env}.aws_account is {current}; stack A reports {account}"
            )
        if region and not str(row.get("aws_region") or "").strip():
            row["aws_region"] = region
            details.append(f"tenants.{env}.aws_region set to {region}")
    model["tenants"] = tenants

    filled = str((model.get("tenants") or {}).get(env, {}).get("aws_account") or "").strip()
    note = str(tenant.get("note") or "").strip()
    if note and filled in _PLACEHOLDER_ACCOUNTS:
        attention.append(note)


def _attention_unplaced(model: dict[str, Any], attention: list[str]) -> None:
    placed = set(model.get("hub_python") or [])
    for peer in (model.get("peers") or {}).values():
        placed.update(peer.get("python") or [])
    for slot_id, fields in (model.get("packages") or {}).items():
        dist = str(fields.get("python") or "")
        if not dist or dist.endswith("-wl"):
            continue
        if dist.startswith("renglo-") or dist in {"renglo-lib", "renglo-api"}:
            continue
        if dist not in placed:
            attention.append(
                f"packages.{slot_id} ({dist}) is not on hub.python or a peer"
            )
