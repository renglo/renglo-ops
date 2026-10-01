#!/usr/bin/env python3
"""Peer catalog parser for *-bom deploy_targets.yml.

A Peer is the service unit (IAM, image, BOM, optional region). An ECS cluster is
only capacity inside a fargate/ec2 peer — never a synonym for the architecture.

Catalog lives in *-bom. CDK/packager live in bom-helper. customer-config stays API-only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

VALID_COMPUTE = ("lambda_only", "fargate", "ec2")
VALID_TASK_SIZE = ("small", "medium", "large")
_PEER_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_HANDLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def peer_unit_name(env_name: str, peer_id: str) -> str:
    """Peer stack / Lambda / ECS stem: ``{env}-peer-{peerId}``."""
    env = (env_name or "").strip()
    peer = (peer_id or "").strip()
    if not env:
        raise ValueError("env_name is required")
    if not peer:
        raise ValueError("peer_id is required")
    return f"{env}-peer-{peer}"


def handlers_unit_name(env_name: str, peer_id: str | None = None) -> str:
    """CloudFormation / Lambda / ECS resource stem.

    Overflow (no peer): ``{env}-handlers``.
    Peer: ``{env}-peer-{peerId}`` (see ``peer_unit_name``).
    """
    env = (env_name or "").strip()
    peer = (peer_id or "").strip()
    if not env:
        raise ValueError("env_name is required")
    if peer:
        return peer_unit_name(env, peer)
    return f"{env}-handlers"


def peer_stack_name(env_name: str, peer_id: str) -> str:
    return peer_unit_name(env_name, peer_id)


def oidc_handlers_role_name(env_name: str, stage: str, peer_id: str | None = None) -> str:
    env = (env_name or "").strip()
    stage = (stage or "").strip()
    peer = (peer_id or "").strip()
    if peer:
        return f"GitHubActionsHandlersRole-{env}-{peer}-{stage}"
    return f"GitHubActionsHandlersRole-{env}-{stage}"


def _as_mapping(data: Any, label: str) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} must be a mapping")
    return data


def _optional_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_peer_id(peer_id: str) -> str:
    if not _PEER_ID_RE.match(peer_id):
        raise RuntimeError(
            f"peers.{peer_id}: id must match {_PEER_ID_RE.pattern} (lowercase, hyphen ok)"
        )
    return peer_id


def _parse_extensions(raw: Any, peer_id: str) -> list[str]:
    if raw is None:
        raise RuntimeError(f"peers.{peer_id}: extensions is required")
    if isinstance(raw, str):
        items = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        raise RuntimeError(f"peers.{peer_id}: extensions must be a list or comma-separated string")
    if not items:
        raise RuntimeError(f"peers.{peer_id}: extensions must not be empty")
    for handle in items:
        if not _HANDLE_RE.match(handle):
            raise RuntimeError(f"peers.{peer_id}: invalid extension handle {handle!r}")
    return items


def _parse_int(value: Any, *, field: str, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise RuntimeError(f"{field} is required")
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field} must be an integer") from exc


def parse_peer(peer_id: str, raw: Any, *, default_handlers_bom: str = "") -> dict[str, Any]:
    """Normalize one catalog row. Raises RuntimeError on invalid schema."""
    peer_id = _require_peer_id(str(peer_id).strip())
    cfg = _as_mapping(raw, f"peers.{peer_id}")
    compute = _optional_str(cfg.get("compute")).lower() or "fargate"
    if compute not in VALID_COMPUTE:
        raise RuntimeError(
            f"peers.{peer_id}: compute must be one of {', '.join(VALID_COMPUTE)} (got {compute!r})"
        )

    peers_bom = (
        _optional_str(cfg.get("peers_bom"))
        or _optional_str(cfg.get("handlers_bom"))
        or default_handlers_bom
    )
    if not peers_bom:
        raise RuntimeError(f"peers.{peer_id}: peers_bom is required")

    bom_path = _optional_str(cfg.get("bom_path")) or f"peers_bom/{peer_id}"
    task_size = _optional_str(cfg.get("task_size")).lower() or "medium"
    if task_size not in VALID_TASK_SIZE:
        raise RuntimeError(
            f"peers.{peer_id}: task_size must be one of {', '.join(VALID_TASK_SIZE)}"
        )

    row: dict[str, Any] = {
        "id": peer_id,
        "compute": compute,
        "extensions": _parse_extensions(cfg.get("extensions"), peer_id),
        "peers_bom": peers_bom,
        "handlers_bom": peers_bom,
        "bom_path": bom_path.rstrip("/"),
        "iam_profile": _optional_str(cfg.get("iam_profile")),
        "aws_region": _optional_str(cfg.get("aws_region")),
        "task_size": task_size,
        "ec2_instance_type": _optional_str(cfg.get("ec2_instance_type")) or "t3.medium",
        "ec2_min_instances": _parse_int(
            cfg.get("ec2_min_instances"), field=f"peers.{peer_id}.ec2_min_instances", default=0
        ),
        "ec2_desired_instances": _parse_int(
            cfg.get("ec2_desired_instances"),
            field=f"peers.{peer_id}.ec2_desired_instances",
            default=1,
        ),
        "ec2_max_instances": _parse_int(
            cfg.get("ec2_max_instances"), field=f"peers.{peer_id}.ec2_max_instances", default=2
        ),
    }
    if compute == "ec2":
        if not _optional_str(cfg.get("ec2_instance_type")):
            raise RuntimeError(f"peers.{peer_id}: ec2_instance_type is required when compute is ec2")
        for key in ("ec2_min_instances", "ec2_desired_instances", "ec2_max_instances"):
            if cfg.get(key) is None:
                raise RuntimeError(f"peers.{peer_id}: {key} is required when compute is ec2")
    return row


def load_peers(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized peer rows (empty if catalog omitted — overflow-only dual-run)."""
    raw = data.get("peers")
    if raw is None:
        return []
    mapping = _as_mapping(raw, "peers")
    default_bom = _optional_str(data.get("handlers_bom"))
    peers = [parse_peer(peer_id, cfg, default_handlers_bom=default_bom) for peer_id, cfg in mapping.items()]
    seen_handles: dict[str, str] = {}
    for peer in peers:
        for handle in peer["extensions"]:
            prev = seen_handles.get(handle)
            if prev:
                raise RuntimeError(
                    f"extension handle {handle!r} is listed on peers {prev!r} and {peer['id']!r}"
                )
            seen_handles[handle] = peer["id"]
    return peers


def peer_for_handle(peers: list[dict[str, Any]], handle: str) -> dict[str, Any] | None:
    want = (handle or "").strip().lower()
    for peer in peers:
        if want in {h.lower() for h in peer["extensions"]}:
            return peer
    return None


def external_handlers_csv(peers: list[dict[str, Any]]) -> str:
    """Union of catalog .extensions — source of truth for EXTERNAL_HANDLERS membership."""
    names: list[str] = []
    seen: set[str] = set()
    for peer in peers:
        for handle in peer["extensions"]:
            key = handle.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(handle)
    return ",".join(names)


def handlers_bom_file(repo_root: Path, peer: dict[str, Any]) -> Path:
    """Resolve ``peers_bom/<peer>/vX.Y.Z.json`` (or custom ``bom_path``)."""
    version = str(peer.get("peers_bom") or peer["handlers_bom"]).lstrip("v")
    base = repo_root / str(peer["bom_path"])
    return base / f"v{version}.json"


def apply_external_handlers_from_peers(vars_block: dict[str, Any], peers: list[dict[str, Any]]) -> None:
    """Overwrite EXTERNAL_HANDLERS with the catalog union when peers are declared."""
    if not peers:
        return
    csv = external_handlers_csv(peers)
    if csv:
        vars_block["EXTERNAL_HANDLERS"] = csv
