#!/usr/bin/env python3
"""Bring a tenant *-bom repo (and local configs) up to the bom-helper layout.

The expected files and parameters live in ``templates/`` next to this repo.
Pulling bom-helper updates that contract. This script copies structural files,
rewrites ``deploy_targets.yml`` into the current shape, splits pin files, and
fills other local configs listed in ``templates/align/manifest.yml``.

Pins and helper ref are kept. Hub wheels, package slots, registries, and the
tenant key are filled from the workspace and, when the CLI can read it, stack A.
Peer placement is not invented. Unknown values are reported under ``attention``
or ``blocked``.

  python scripts/align_bom.py --helper . --bom ../apollo-bom --ops ..
  python scripts/align_bom.py --helper . --bom ../apollo-bom --ops .. --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


CORE_PACKAGE_SLOTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("renglo-lib", {"python": "renglo-lib"}),
    ("renglo-api", {"python": "renglo-api"}),
    ("console", {"npm": "@renglo/console"}),
)
KNOWN_TOP = {
    "bom",
    "console_bom",
    "hub",
    "helper",
    "registries",
    "registry",
    "packages",
    "peers",
    "tenants",
    "handlers_bom",
    "handlers_compute",
}
PIN_ORDER = ("version", "created_at", "description", "train", "deploy_stage", "python", "npm", "repos")
_ROLE_LINE = re.compile(r"^\s*role\s*=\s*['\"]?([^#'\"]+)")
_PIN_VERSION = re.compile(
    r"^v(?P<ver>\d+(?:\.\d+){0,2}(?:[-.]?(?:rc|a|b|dev)\d*)?)\.json$",
    re.IGNORECASE,
)


def align(
    helper_root: Path,
    bom_root: Path,
    *,
    ops_root: Path | None = None,
    apply: bool = False,
    tenant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a report. Writes files only when ``apply`` is true."""
    helper_root = helper_root.resolve()
    bom_root = bom_root.resolve()
    if ops_root is not None:
        ops_root = ops_root.resolve()
    manifest = _load_manifest(helper_root)
    template_root = helper_root / "templates" / "tenant-bom"
    changes: list[dict[str, Any]] = []
    attention: list[str] = []
    blocked: list[str] = []

    targets_path = bom_root / "deploy_targets.yml"
    model: dict[str, Any] | None = None
    existed = targets_path.is_file()
    if yaml is None:
        blocked.append("PyYAML is required to read deploy_targets.yml")
    else:
        loaded: Any = {}
        prior = ""
        if existed:
            prior = targets_path.read_text(encoding="utf-8")
            try:
                loaded = yaml.safe_load(prior) or {}
            except Exception as exc:  # noqa: BLE001 — surface the file error, do not guess
                loaded = None
                blocked.append(f"deploy_targets.yml: {exc}")
        if loaded is not None and not isinstance(loaded, dict):
            blocked.append("deploy_targets.yml must be a mapping")
            loaded = None
        if isinstance(loaded, dict):
            pin_details = _fill_pin_pointers(loaded, bom_root)
            model, details, extra_attention = _normalize_targets(loaded)
            details = [*pin_details, *details]
            from align_workspace import apply_workspace

            ws_details, ws_attention = apply_workspace(
                model,
                bom_root=bom_root,
                ops_root=ops_root,
                tenant=tenant,
            )
            details.extend(ws_details)
            attention.extend(ws_attention)
            attention.extend(extra_attention)
            blocked.extend(_persistent_blocks(model))
            rendered = _render_targets(model)
            if rendered != prior:
                if not details:
                    details = ["rewrote to the current layout"]
                _record(changes, "rewrite" if existed else "create", "deploy_targets.yml", details)
                if apply:
                    targets_path.write_text(rendered, encoding="utf-8")
            if model.get("bom"):
                pin_changes, pin_attention = _align_pins(bom_root, model, apply=apply)
                changes.extend(pin_changes)
                attention.extend(pin_attention)
            if not model.get("peers") and any((bom_root / "handlers_bom").glob("*.json")):
                attention.append(
                    "handlers_bom/*.json is overflow history. "
                    "Add peers: before the next handlers deploy."
                )

    _align_platform_env(
        bom_root,
        template_root,
        list(manifest.get("platform_env_keys") or []),
        changes,
        attention,
        apply=apply,
    )
    _align_archived(bom_root, template_root, changes, apply=apply)
    _align_copies(
        bom_root,
        template_root,
        list(manifest.get("copies") or []),
        changes,
        blocked,
        apply=apply,
    )
    _align_obsolete(bom_root, list(manifest.get("obsolete") or []), changes, apply=apply)
    _align_gitconvoy(bom_root, changes, apply=apply)
    _align_documents(
        list(manifest.get("documents") or []),
        bom_root,
        ops_root,
        changes,
        attention,
        blocked,
        apply=apply,
    )
    if model is not None:
        from align_workspace import ensure_renglo_domain_owner, publisher_name

        owner_details = ensure_renglo_domain_owner(
            ops_root,
            publisher_name(bom_root),
            apply=apply,
        )
        if owner_details:
            _record(changes, "fill", "launcher/cdk/customer-config.json", owner_details)

    report = {
        "ok": not blocked and (apply or not changes),
        "applied": bool(apply),
        "bom": str(bom_root),
        "changes": changes,
        "attention": attention,
        "blocked": blocked,
    }
    return report


def format_report(report: dict[str, Any]) -> str:
    """Human-readable report. ``ok`` is false when a rewrite is still pending or a value is blocked."""
    lines = [Path(str(report.get("bom") or "bom")).name]
    changes = list(report.get("changes") or [])
    attention = list(report.get("attention") or [])
    blocked = list(report.get("blocked") or [])
    if not changes and not attention and not blocked:
        lines.append("layout matches bom-helper")
    elif not changes and not blocked:
        lines.append("no automatic updates")
    for change in changes:
        lines.append(f"  {change.get('action', ''):8} {change.get('path', '')}")
        for detail in change.get("details") or []:
            lines.append(f"           {detail}")
    if attention:
        lines.append("attention:")
        for item in attention:
            lines.append(f"  {item}")
    if blocked:
        lines.append("blocked:")
        for item in blocked:
            lines.append(f"  {item}")
    if report.get("applied") and changes:
        lines.append("updated")
    elif changes:
        lines.append("next: renglo config align")
    elif blocked:
        lines.append("next: fill the blocked values, then renglo config align")
    return "\n".join(lines)


def _load_manifest(helper_root: Path) -> dict[str, Any]:
    path = helper_root / "templates" / "align" / "manifest.yml"
    if not path.is_file():
        raise FileNotFoundError(f"align manifest not found: {path}")
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: manifest must be a mapping")
    return data


def _record(changes: list[dict[str, Any]], action: str, path: str, details: list[str] | None = None) -> None:
    changes.append({"action": action, "path": path, "details": list(details or [])})


def _fill_pin_pointers(data: dict[str, Any], bom_root: Path) -> list[str]:
    """When bom: or console_bom: is absent, use the newest vX.Y.Z.json in that folder."""
    details: list[str] = []
    if not _version(data.get("bom")):
        latest = _latest_pin_version(bom_root / "bom")
        if latest:
            data["bom"] = latest
            details.append(f"bom set to {latest} from bom/")
    if not _version(data.get("console_bom")):
        latest = _latest_pin_version(bom_root / "console_bom")
        if latest:
            data["console_bom"] = latest
            details.append(f"console_bom set to {latest} from console_bom/")
    return details


def _latest_pin_version(folder: Path) -> str:
    if not folder.is_dir():
        return ""
    found: list[str] = []
    for path in folder.glob("v*.json"):
        match = _PIN_VERSION.match(path.name)
        if match:
            found.append(match.group("ver"))
    if not found:
        return ""
    return max(found, key=_pin_sort_key)


def _pin_sort_key(version: str) -> tuple[int, int, int, int, int]:
    text = version[1:] if version.startswith("v") else version
    match = re.match(
        r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.]?(rc|a|b|dev)(\d*))?$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return (0, 0, 0, -1, 0)
    major, minor, patch, tag, num = match.groups()
    rank = {None: 3, "rc": 2, "b": 1, "a": 0, "dev": 0}
    tag_name = tag.lower() if tag else None
    return (int(major), int(minor or 0), int(patch or 0), rank.get(tag_name, 3), int(num or 0))


def _version(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"none", "null"}:
        return ""
    return text[1:] if text.startswith("v") else text


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _yaml_str(value: object) -> str:
    text = str(value)
    if (
        text == ""
        or text.lower() in {"true", "false", "null", "yes", "no", "~"}
        or text[:1].isdigit()
        or any(ch in text for ch in ":#{}[]&*!|>%@`',\"")
        or text != text.strip()
    ):
        return json.dumps(text)
    return text


def _flow(items: list[Any]) -> str:
    return "[" + ", ".join(_yaml_str(item) for item in items) + "]"


def _normalize_targets(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    details: list[str] = []
    attention: list[str] = []
    bom = _version(data.get("bom"))
    console = _version(data.get("console_bom"))
    if bom and not console:
        console = bom
        details.append(f"console_bom set to {console}")

    registries, reg_details, reg_attention = _normalize_registries(data)
    details.extend(reg_details)
    attention.extend(reg_attention)

    helper_raw = data.get("helper") if isinstance(data.get("helper"), dict) else {}
    repository = str(helper_raw.get("repository") or "").strip()
    ref = str(helper_raw.get("ref") or "").strip()
    if not repository:
        repository = "renglo/bom-helper"
        details.append("helper.repository defaulted to renglo/bom-helper")
    if not ref:
        ref = "main"
        details.append("helper.ref defaulted to main")

    if "hub" not in data:
        details.append("added hub.python")
    hub_raw = data.get("hub") if isinstance(data.get("hub"), dict) else {}
    hub_python = _str_list(hub_raw.get("python"))

    packages_raw = data.get("packages") if isinstance(data.get("packages"), dict) else {}
    packages, pkg_details = _ensure_packages(packages_raw)
    if "packages" not in data or pkg_details:
        details.extend(pkg_details)

    default_peer_bom = console or bom
    peers_raw = data.get("peers") if isinstance(data.get("peers"), dict) else {}
    peers, peer_details, peer_attention = _normalize_peers(peers_raw, default_peer_bom)
    details.extend(peer_details)
    attention.extend(peer_attention)

    for key in ("handlers_compute", "handlers_bom"):
        if key in data:
            shown = data.get(key)
            details.append(f"dropped {key}" + (f" ({shown})" if shown not in (None, "") else ""))

    tenants, tenant_details, tenant_blocked = _normalize_tenants(data.get("tenants"))
    details.extend(tenant_details)
    attention.extend(tenant_blocked)

    extra = {key: value for key, value in data.items() if key not in KNOWN_TOP}
    for key in extra:
        details.append(f"kept unknown key {key}")

    model = {
        "bom": bom,
        "console_bom": console,
        "hub_python": hub_python,
        "helper": {"repository": repository, "ref": ref},
        "registries": registries,
        "packages": packages,
        "peers": peers,
        "tenants": tenants,
        "extra": extra,
    }
    return model, details, attention


def _persistent_blocks(model: dict[str, Any]) -> list[str]:
    """Problems that remain after a rewrite. A check stays failed until these are filled in."""
    blocked: list[str] = []
    if not model.get("bom"):
        blocked.append("deploy_targets.yml: bom version is missing")
    if not model.get("tenants"):
        blocked.append("deploy_targets.yml: tenants is missing")
    for tenant_id, row in (model.get("tenants") or {}).items():
        if not str(row.get("aws_account") or "").strip():
            blocked.append(f"tenants.{tenant_id}: aws_account is required")
    for peer_id, row in (model.get("peers") or {}).items():
        compute = str(row.get("compute") or "")
        if compute not in {"lambda_only", "fargate", "ec2"}:
            blocked.append(f"peers.{peer_id}: compute {compute!r} is not lambda_only, fargate, or ec2")
        task_size = str(row.get("task_size") or "")
        if task_size not in {"small", "medium", "large"}:
            blocked.append(f"peers.{peer_id}: task_size {task_size!r} is not small, medium, or large")
        if not row.get("extensions"):
            blocked.append(f"peers.{peer_id}: extensions is required")
        if compute == "ec2":
            for key in ("ec2_instance_type", "ec2_min_instances", "ec2_desired_instances", "ec2_max_instances"):
                if key not in row:
                    blocked.append(f"peers.{peer_id}: {key} is required when compute is ec2")
    return blocked


def _normalize_registries(
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, list[str], list[str]]:
    details: list[str] = []
    attention: list[str] = []
    if "registry" in data and "registries" not in data:
        raw = data.get("registry")
        if isinstance(raw, dict):
            entry = _registry_entry(raw, details, index=0)
            details.append("renamed registry: to registries:")
            return [entry], details, attention
        details.append("dropped registry: (it was not a mapping)")
        return None, details, attention
    if "registry" in data:
        details.append("dropped singular registry:")
    if "registries" not in data:
        return None, details, attention
    raw_list = data.get("registries")
    if raw_list is None:
        return None, details, attention
        if not isinstance(raw_list, list):
            details.append("dropped registries: (it was not a list)")
            return None, details, attention
    entries = []
    for index, item in enumerate(raw_list):
        if not isinstance(item, dict):
            details.append(f"dropped registries[{index}] (not a mapping)")
            continue
        entries.append(_registry_entry(item, details, index=index))
    return entries, details, attention


def _registry_entry(raw: dict[str, Any], details: list[str], *, index: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "domain": str(raw.get("domain") or "renglo").strip() or "renglo",
        "python_repository": str(raw.get("python_repository") or "python-store").strip() or "python-store",
        "npm_repository": str(raw.get("npm_repository") or "npm-store").strip() or "npm-store",
    }
    owner = str(raw.get("domain_owner") or "").strip()
    if owner:
        entry["domain_owner"] = owner
    if "npm_scopes" not in raw or raw.get("npm_scopes") is None:
        entry["npm_scopes"] = []
        details.append(f"registries[{index}].npm_scopes set to []")
    else:
        entry["npm_scopes"] = _str_list(raw.get("npm_scopes"))
    region = str(raw.get("region") or "").strip()
    if region:
        entry["region"] = region
    for key, value in raw.items():
        if key not in entry and key not in {"domain", "python_repository", "npm_repository", "npm_scopes"}:
            entry[key] = value
    return entry


def _ensure_packages(packages: dict[str, Any]) -> tuple[dict[str, dict[str, str]], list[str]]:
    details: list[str] = []
    cleaned: dict[str, dict[str, str]] = {}
    for slot_id, fields in packages.items():
        if not isinstance(fields, dict):
            continue
        kept = {
            key: str(fields[key]).strip()
            for key in ("python", "npm", "repo")
            if str(fields.get(key) or "").strip()
        }
        cleaned[str(slot_id).strip()] = kept
    for slot_id, fields in CORE_PACKAGE_SLOTS:
        current = cleaned.get(slot_id)
        if current is None:
            cleaned[slot_id] = dict(fields)
            details.append(f"packages.{slot_id} added")
            continue
        for key, value in fields.items():
            if key not in current:
                current[key] = value
                details.append(f"packages.{slot_id}.{key} set to {value}")
    ordered: dict[str, dict[str, str]] = {}
    for slot_id, _fields in CORE_PACKAGE_SLOTS:
        if slot_id in cleaned:
            ordered[slot_id] = cleaned[slot_id]
    for slot_id, fields in cleaned.items():
        if slot_id not in ordered:
            ordered[slot_id] = fields
    return ordered, details


def _normalize_peers(
    raw: dict[str, Any],
    default_bom: str,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    details: list[str] = []
    attention: list[str] = []
    peers: dict[str, dict[str, Any]] = {}
    for peer_id, cfg in raw.items():
        if not isinstance(cfg, dict):
            attention.append(f"peers.{peer_id} is not a mapping")
            continue
        compute = str(cfg.get("compute") or "").strip().lower()
        if not compute:
            compute = "fargate"
            details.append(f"peers.{peer_id}.compute defaulted to fargate")
        task_size = str(cfg.get("task_size") or "").strip().lower()
        if not task_size:
            task_size = "medium"
            details.append(f"peers.{peer_id}.task_size defaulted to medium")
        if cfg.get("extensions") is None:
            extensions = []
        else:
            extensions = _str_list(cfg.get("extensions"))
        peers_bom = str(cfg.get("peers_bom") or "").strip()
        legacy = str(cfg.get("handlers_bom") or "").strip()
        if not peers_bom and legacy:
            peers_bom = legacy
            details.append(f"peers.{peer_id}.handlers_bom renamed to peers_bom")
        if not peers_bom and default_bom:
            peers_bom = default_bom
            details.append(f"peers.{peer_id}.peers_bom set to {default_bom}")
        row: dict[str, Any] = {
            "compute": compute,
            "task_size": task_size,
            "extensions": extensions,
            "python": _str_list(cfg.get("python")),
            "peers_bom": _version(peers_bom),
        }
        for key in ("aws_region", "iam_profile", "bom_path", "ec2_instance_type"):
            value = str(cfg.get(key) or "").strip()
            if value:
                row[key] = value
        for key in ("ec2_min_instances", "ec2_desired_instances", "ec2_max_instances"):
            if cfg.get(key) is not None:
                row[key] = int(cfg[key])
        peers[str(peer_id).strip()] = row
    return peers, details, attention


def _normalize_tenants(raw: Any) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    details: list[str] = []
    attention: list[str] = []
    if not isinstance(raw, dict) or not raw:
        return {}, details, attention
    tenants: dict[str, dict[str, Any]] = {}
    for key, cfg in raw.items():
        if not isinstance(cfg, dict):
            attention.append(f"tenants.{key} is not a mapping")
            continue
        row = dict(cfg)
        nested = str(row.pop("id", "") or "").strip()
        new_key = str(key).strip()
        if nested and nested != new_key:
            details.append(f"tenants.{new_key} renamed to {nested} (id is the key)")
            new_key = nested
        elif nested:
            details.append(f"dropped tenants.{new_key}.id (the key is the AWS prefix)")
        account = str(row.get("aws_account") or "").strip()
        region = str(row.get("aws_region") or "").strip()
        if not region:
            region = "us-east-1"
            details.append(f"tenants.{new_key}.aws_region defaulted to us-east-1")
        stages = row.get("stages") if isinstance(row.get("stages"), dict) else {}
        normalized_stages: dict[str, dict[str, Any]] = {}
        for stage in ("staging", "production"):
            stage_cfg = stages.get(stage) if isinstance(stages.get(stage), dict) else None
            if stage_cfg is None:
                enabled = stage == "staging"
                details.append(f"tenants.{new_key}.stages.{stage}.enabled set to {str(enabled).lower()}")
            else:
                enabled = stage_cfg.get("enabled", True) is not False
            normalized_stages[stage] = {"enabled": enabled}
        tenants[new_key] = {
            "aws_account": account,
            "aws_region": region,
            "stages": normalized_stages,
        }
    return tenants, details, attention


def _render_targets(model: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Hub/backend BOM (Stack A/B API image). Omit to use the latest bom/vX.Y.Z.json.",
    ]
    if model.get("bom"):
        lines.append(f"bom: {model['bom']}")
    lines.append("")
    lines.append("# Console BOM (Amplify host + extension npm). Usually the same version as bom:.")
    if model.get("console_bom"):
        lines.append(f"console_bom: {model['console_bom']}")
    lines.append("")
    lines.append("# Python dists on the hub API image. Console npm follows packages: slots.")
    lines.append("# renglo-lib, renglo-api, and the packages slot whose python name ends")
    lines.append("# with -wl are installed on the hub image as well (not repeated here).")
    lines.append("hub:")
    hub_python = list(model.get("hub_python") or [])
    if hub_python:
        lines.append("  python:")
        lines.extend(f"    - {name}" for name in hub_python)
    else:
        lines.append("  python: []")
    lines.append("")
    lines.append("# Shared deploy scripts / Dockerfile (checked out by CI).")
    helper = model.get("helper") or {}
    lines.append("helper:")
    lines.append(f"  repository: {helper.get('repository') or 'renglo/bom-helper'}")
    lines.append(f"  ref: {_yaml_str(helper.get('ref') or 'main')}")
    lines.append("")
    lines.append("# CodeArtifact publishers. Omit registries for the same-account internal default.")
    lines.append("# Entries without domain_owner use the first tenant aws_account.")
    registries = model.get("registries")
    if registries is None:
        pass
    elif not registries:
        lines.append("registries: []")
    else:
        lines.append("registries:")
        for entry in registries:
            lines.append(f"  - domain: {_yaml_str(entry.get('domain') or 'renglo')}")
            if entry.get("domain_owner"):
                lines.append(f"    domain_owner: {_yaml_str(entry['domain_owner'])}")
            lines.append(f"    python_repository: {_yaml_str(entry.get('python_repository') or 'python-store')}")
            lines.append(f"    npm_repository: {_yaml_str(entry.get('npm_repository') or 'npm-store')}")
            if "npm_scopes" in entry:
                lines.append(f"    npm_scopes: {_flow(list(entry.get('npm_scopes') or []))}")
            if entry.get("region"):
                lines.append(f"    region: {_yaml_str(entry['region'])}")
            for key, value in entry.items():
                if key in {"domain", "domain_owner", "python_repository", "npm_repository", "npm_scopes", "region"}:
                    continue
                lines.append(f"    {key}: {_yaml_str(value)}")
    lines.append("")
    lines.append("# Slot id is the git-convoy repo id. Names only — adopt fills versions.")
    lines.append("packages:")
    for slot_id, fields in (model.get("packages") or {}).items():
        lines.append(f"  {slot_id}:")
        for key in ("python", "npm", "repo"):
            if fields.get(key):
                lines.append(f"    {key}: {_yaml_str(fields[key])}")
    peers = model.get("peers") or {}
    lines.append("")
    if not peers:
        lines.append("# peers: add a peer when handlers leave the hub. compute is lambda_only, fargate, or ec2.")
    else:
        lines.append("# Peers: independently provisioned handler services. Pins live in peers_bom/<id>/.")
        lines.append("peers:")
        for peer_id, row in peers.items():
            lines.append(f"  {peer_id}:")
            lines.append(f"    compute: {row.get('compute') or 'fargate'}")
            lines.append(f"    task_size: {row.get('task_size') or 'medium'}")
            lines.append(f"    extensions: {_flow(list(row.get('extensions') or []))}")
            python = list(row.get("python") or [])
            if python:
                lines.append("    python:")
                lines.extend(f"      - {name}" for name in python)
            else:
                lines.append("    python: []")
            if row.get("peers_bom"):
                lines.append(f"    peers_bom: {row['peers_bom']}")
            for key in ("aws_region", "iam_profile", "bom_path", "ec2_instance_type"):
                if row.get(key):
                    lines.append(f"    {key}: {_yaml_str(row[key])}")
            for key in ("ec2_min_instances", "ec2_desired_instances", "ec2_max_instances"):
                if key in row:
                    lines.append(f"    {key}: {int(row[key])}")
    lines.append("")
    lines.append("# Tenants: the key is the AWS prefix (customer-config env_name).")
    tenants = model.get("tenants") or {}
    if tenants:
        lines.append("tenants:")
        for tenant_id, row in tenants.items():
            lines.append(f"  {tenant_id}:")
            lines.append(f"    aws_account: {_yaml_str(row.get('aws_account') or '')}")
            lines.append(f"    aws_region: {_yaml_str(row.get('aws_region') or 'us-east-1')}")
            lines.append("    stages:")
            stages = row.get("stages") or {}
            for stage in ("staging", "production"):
                enabled = bool((stages.get(stage) or {}).get("enabled"))
                lines.append(f"      {stage}:")
                lines.append(f"        enabled: {str(enabled).lower()}")
    extra = model.get("extra") or {}
    if extra and yaml is not None:
        dumped = yaml.safe_dump(extra, sort_keys=False).rstrip()
        lines.append("")
        lines.append("# Keys outside the current layout (kept):")
        lines.extend(dumped.splitlines())
    lines.append("")
    return "\n".join(lines)


def _dump_pin(data: dict[str, Any]) -> str:
    ordered: dict[str, Any] = {}
    for key in PIN_ORDER:
        if key in data:
            ordered[key] = data[key]
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value
    return json.dumps(ordered, indent=2) + "\n"


def _pin_meta(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in ("version", "created_at", "description", "train") if key in data}


def _console_repos(repos: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, entry in repos.items():
        name = str(key).split("/", 1)[-1]
        if name == "console" or name == "wl" or name.endswith("-wl"):
            kept[str(key)] = entry
    return kept


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _align_pins(
    bom_root: Path,
    model: dict[str, Any],
    *,
    apply: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    changes: list[dict[str, Any]] = []
    attention: list[str] = []
    version = str(model.get("bom") or "")
    ver = version if version.startswith("v") else f"v{version}"
    hub_path = bom_root / "bom" / f"{ver}.json"
    if not hub_path.is_file():
        attention.append(f"missing hub BOM bom/{ver}.json")
        return changes, attention
    try:
        hub = _read_json(hub_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        attention.append(str(exc))
        return changes, attention

    console_path = bom_root / "console_bom" / f"{ver}.json"
    npm = hub.get("npm") if isinstance(hub.get("npm"), dict) else None
    working = dict(hub)
    if not console_path.is_file():
        if npm:
            console = _pin_meta(hub)
            console["npm"] = npm
            console["repos"] = _console_repos(hub.get("repos") if isinstance(hub.get("repos"), dict) else {})
            _write_text(console_path, _dump_pin(console), apply=apply)
            _record(changes, "create", f"console_bom/{ver}.json", ["moved npm pins out of bom/"])
            working.pop("npm", None)
            repos = dict(working.get("repos") or {})
            for key in console["repos"]:
                repos.pop(key, None)
            working["repos"] = repos
            rewritten = _dump_pin(working)
            if rewritten != hub_path.read_text(encoding="utf-8"):
                _write_text(hub_path, rewritten, apply=apply)
                _record(changes, "rewrite", f"bom/{ver}.json", ["removed npm pins now stored in console_bom/"])
        else:
            attention.append(f"console_bom/{ver}.json is missing and bom/{ver}.json has no npm pins")
    elif npm:
        try:
            console_doc = _read_json(console_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            attention.append(str(exc))
            console_doc = {}
        if console_doc.get("npm") == npm:
            working.pop("npm", None)
            rewritten = _dump_pin(working)
            if rewritten != hub_path.read_text(encoding="utf-8"):
                _write_text(hub_path, rewritten, apply=apply)
                _record(changes, "rewrite", f"bom/{ver}.json", ["removed npm pins duplicated in console_bom/"])
        else:
            attention.append(f"bom/{ver}.json npm pins differ from console_bom/{ver}.json; left both in place")

    hub_python = working.get("python") if isinstance(working.get("python"), dict) else {}
    placed = set(model.get("hub_python") or [])
    for peer in (model.get("peers") or {}).values():
        placed.update(peer.get("python") or [])
    extra = [
        name
        for name in hub_python
        if name not in placed and name not in {"renglo-lib", "renglo-api"} and not str(name).endswith("-wl")
    ]
    if extra and (model.get("hub_python") or model.get("peers")):
        attention.append(
            "python pins not listed on hub.python or peers.*.python: " + ", ".join(sorted(extra))
        )

    for peer_id, peer in (model.get("peers") or {}).items():
        rel_dir = str(peer.get("bom_path") or f"peers_bom/{peer_id}").strip("/")
        peer_path = bom_root / rel_dir / f"{ver}.json"
        if peer_path.is_file():
            continue
        wanted = set(peer.get("python") or []) | {"renglo-lib"}
        wanted.update(name for name in hub_python if str(name).endswith("-wl"))
        pinned = {name: hub_python[name] for name in wanted if name in hub_python}
        missing = [name for name in peer.get("python") or [] if name not in pinned]
        if missing:
            attention.append(f"{rel_dir}/{ver}.json missing pins for {', '.join(missing)}")
        if not pinned:
            attention.append(f"{rel_dir}/{ver}.json not created (no matching python pins in bom/{ver}.json)")
            continue
        doc = _pin_meta(working)
        doc["deploy_stage"] = str(working.get("deploy_stage") or "staging")
        doc["python"] = pinned
        doc["repos"] = {}
        _write_text(peer_path, _dump_pin(doc), apply=apply)
        _record(changes, "create", f"{rel_dir}/{ver}.json", ["python pins copied from the hub BOM"])
    return changes, attention


def _write_text(path: Path, text: str, *, apply: bool) -> None:
    if not apply:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _align_platform_env(
    bom_root: Path,
    template_root: Path,
    keys: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    attention: list[str],
    *,
    apply: bool,
) -> None:
    path = bom_root / "platform_env.yml"
    template = template_root / "platform_env.yml"
    if not path.is_file():
        if not template.is_file():
            attention.append("platform_env.yml template is missing from bom-helper")
            return
        _write_text(path, template.read_text(encoding="utf-8"), apply=apply)
        _record(changes, "create", "platform_env.yml", [key["name"] for key in keys if key.get("name")])
        return
    if yaml is None:
        return
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:  # noqa: BLE001
        attention.append(f"platform_env.yml: {exc}")
        return
    if not isinstance(data, dict):
        attention.append("platform_env.yml is not a mapping")
        return
    missing = []
    for key in keys:
        name = str(key.get("name") or "").strip()
        if not name or name in data:
            continue
        if re.search(rf"(?m)^\s*#\s*{re.escape(name)}\s*:", text):
            continue
        missing.append(key)
    if not missing:
        return
    addition = "\n" + "\n".join(
        f"# {key['name']}:  # {key.get('comment') or ''}".rstrip() for key in missing
    )
    if not text.endswith("\n"):
        addition = "\n" + addition
    updated = text + addition + "\n"
    _write_text(path, updated, apply=apply)
    _record(changes, "fill", "platform_env.yml", [str(key["name"]) for key in missing])


def _align_archived(bom_root: Path, template_root: Path, changes: list[dict[str, Any]], *, apply: bool) -> None:
    handlers = bom_root / "handlers_bom"
    if not handlers.is_dir() or not any(handlers.glob("*.json")):
        return
    template = template_root / "handlers_bom" / "ARCHIVED.md"
    if not template.is_file():
        return
    dest = handlers / "ARCHIVED.md"
    text = template.read_text(encoding="utf-8")
    if dest.is_file() and dest.read_text(encoding="utf-8") == text:
        return
    _write_text(dest, text, apply=apply)
    _record(changes, "create" if not dest.is_file() else "rewrite", "handlers_bom/ARCHIVED.md")


def _copy_pair(item: Any) -> tuple[str, str]:
    """Manifest entries are either ``path`` (same relative path) or ``{from, to}``."""
    if isinstance(item, str):
        return item, item
    if isinstance(item, dict):
        source = str(item.get("from") or item.get("source") or "").strip()
        dest = str(item.get("to") or item.get("dest") or source).strip()
        return source, dest
    return "", ""


def _align_copies(
    bom_root: Path,
    template_root: Path,
    copies: list[Any],
    changes: list[dict[str, Any]],
    blocked: list[str],
    *,
    apply: bool,
) -> None:
    for item in copies:
        source_rel, dest_rel = _copy_pair(item)
        if not source_rel or not dest_rel:
            continue
        rel_path = Path(dest_rel)
        src = template_root / Path(source_rel)
        dest = bom_root / rel_path
        if not src.is_file():
            blocked.append(f"bom-helper template missing {source_rel}")
            continue
        blob = src.read_bytes()
        if dest.is_file() and dest.read_bytes() == blob:
            continue
        action = "create" if not dest.is_file() else "copy"
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        _record(changes, action, rel_path.as_posix())


def _align_obsolete(
    bom_root: Path,
    obsolete: list[str],
    changes: list[dict[str, Any]],
    *,
    apply: bool,
) -> None:
    for rel in obsolete:
        dest = bom_root / Path(str(rel))
        if not dest.is_file():
            continue
        if apply:
            dest.unlink()
        _record(changes, "remove", Path(str(rel)).as_posix())


def _align_gitconvoy(bom_root: Path, changes: list[dict[str, Any]], *, apply: bool) -> None:
    path = bom_root / "gitconvoy.toml"
    role = "product"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            match = _ROLE_LINE.match(raw.split("#", 1)[0].strip())
            if match:
                role = match.group(1).strip()
                break
    if role == "bom":
        return
    text = (
        "# git-convoy membership marker (repo root).\n"
        "# role: product | ops | bom | incubating | registry\n"
        'role = "bom"\n'
    )
    _write_text(path, text, apply=apply)
    _record(changes, "rewrite", "gitconvoy.toml", ['role set to "bom"'])


def _align_documents(
    documents: list[dict[str, Any]],
    bom_root: Path,
    ops_root: Path | None,
    changes: list[dict[str, Any]],
    attention: list[str],
    blocked: list[str],
    *,
    apply: bool,
) -> None:
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        root_name = str(doc.get("root") or "bom")
        if root_name == "ops":
            if ops_root is None:
                continue
            root = ops_root
        else:
            root = bom_root
        rel = Path(str(doc.get("path") or ""))
        path = root / rel
        display = _display(path, bom_root, ops_root)
        if not path.parent.is_dir():
            continue
        if not path.is_file():
            attention.append(f"{display} is missing")
            continue
        fmt = str(doc.get("format") or "json")
        try:
            current = _load_mapping(path, fmt)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blocked.append(f"{display}: {exc}")
            continue
        updated = copy.deepcopy(current)
        details = _fill_missing(updated, doc.get("ensure") if isinstance(doc.get("ensure"), dict) else {})
        for key in doc.get("drop") or []:
            if key in updated:
                updated.pop(key, None)
                details.append(f"dropped {key}")
        prefixes = tuple(str(item) for item in (doc.get("ignore_prefixes") or []))
        example_rel = str(doc.get("example") or "").strip()
        if example_rel:
            example_path = root / example_rel
            if example_path.is_file():
                try:
                    example = _load_mapping(example_path, "json" if example_path.suffix == ".json" else fmt)
                except (OSError, ValueError, json.JSONDecodeError):
                    example = {}
                for key in example:
                    if prefixes and str(key).startswith(prefixes):
                        continue
                    if key not in updated:
                        attention.append(f"{display}: missing {key}")
        for key in doc.get("required") or []:
            value = updated.get(key)
            if value is None or (isinstance(value, str) and not str(value).strip()):
                blocked.append(f"{display}: {key} is required")
        if updated == current:
            continue
        _write_text(path, _dump_mapping(updated, fmt), apply=apply)
        _record(changes, "fill", display, details)


def _fill_missing(dest: dict[str, Any], ensure: dict[str, Any], prefix: str = "") -> list[str]:
    details: list[str] = []
    for key, value in ensure.items():
        if key not in dest:
            dest[key] = copy.deepcopy(value)
            details.append(f"added {prefix}{key}")
        elif isinstance(value, dict) and isinstance(dest.get(key), dict):
            details.extend(_fill_missing(dest[key], value, prefix=f"{prefix}{key}."))
    return details


def _load_mapping(path: Path, fmt: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if fmt == "json":
        data = json.loads(text)
    else:
        if yaml is None:
            raise RuntimeError("PyYAML is required")
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("expected a mapping")
    return data


def _dump_mapping(data: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2) + "\n"
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    return yaml.safe_dump(data, sort_keys=False)


def _display(path: Path, bom_root: Path, ops_root: Path | None) -> str:
    try:
        return path.relative_to(bom_root).as_posix()
    except ValueError:
        pass
    if ops_root is not None:
        try:
            return path.relative_to(ops_root.parent).as_posix()
        except ValueError:
            return path.relative_to(ops_root).as_posix()
    return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and update a tenant *-bom repo to the bom-helper layout")
    parser.add_argument("--helper", required=True, help="bom-helper checkout")
    parser.add_argument("--bom", required=True, help="tenant *-bom checkout")
    parser.add_argument("--ops", default="", help="ops directory that holds launcher/ (optional local configs)")
    parser.add_argument("--apply", action="store_true", help="Write updates. Default is a dry run.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    ops = Path(args.ops).expanduser() if args.ops else None
    try:
        report = align(Path(args.helper), Path(args.bom), ops_root=ops, apply=bool(args.apply))
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
