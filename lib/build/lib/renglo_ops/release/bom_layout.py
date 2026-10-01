#!/usr/bin/env python3
"""Three-BOM layout: Console, Hub (backend), and Peer surfaces.

Placement lives in ``deploy_targets.yml`` (``hub.python``, ``peers.<id>.python``).
Adopt fills a **master** pin set, then this module splits it into:

- ``bom/vX.Y.Z.json`` — hub/backend python (+ git repos for hub)
- ``console_bom/vX.Y.Z.json`` — console npm (+ git repos for console)
- ``peers_bom/<id>/vX.Y.Z.json`` — peer python (+ optional repos)

Core packages (by convention, not in placement lists):

- Hub python: ``renglo-lib``, ``renglo-api``, tenant ``*-wl`` python
- Peer python: ``renglo-lib``, tenant ``*-wl`` python
- Console npm: ``@renglo/console``, tenant ``@*/wl`` npm
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - bom-helper CI installs pyyaml
    yaml = None  # type: ignore[assignment]

from catalog_slots import PackageSlot, load_package_catalog, parse_package_catalog

HUB_PYTHON_CORE = ("renglo-lib", "renglo-api")
PEER_PYTHON_CORE = ("renglo-lib",)
CONSOLE_NPM_HOST = "@renglo/console"
_WL_NPM = re.compile(r"^@[^/]+/wl$")
_DIST_LINE = re.compile(r"^\s*-\s*(?P<q>['\"]?)(?P<name>[^'\"#\s]+)\1\s*(?:#.*)?$")


@dataclass(frozen=True)
class Placement:
    hub_python: tuple[str, ...] = ()
    peers: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _strip_quotes(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _parse_dist_list(lines: list[str], start: int, base_indent: int) -> tuple[tuple[str, ...], int]:
    names: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= base_indent:
            break
        match = _DIST_LINE.match(line)
        if match:
            name = _strip_quotes(match.group("name").strip())
            if name:
                names.append(name)
        index += 1
    return tuple(names), index


def parse_placement_text(text: str) -> Placement:
    """Parse ``hub.python`` and ``peers.<id>.python`` without requiring PyYAML."""
    lines = text.splitlines()
    hub_python: tuple[str, ...] = ()
    peers: dict[str, tuple[str, ...]] = {}

    hub_start = None
    for index, line in enumerate(lines):
        if re.match(r"^hub:\s*(?:#.*)?$", line):
            hub_start = index + 1
            break
    if hub_start is not None:
        for index in range(hub_start, len(lines)):
            line = lines[index]
            if re.match(r"^\S", line) and not line.startswith("#"):
                if index > hub_start:
                    break
            if re.match(r"^\s+python:\s*(?:#.*)?$", line):
                indent = len(line) - len(line.lstrip(" "))
                hub_python, _ = _parse_dist_list(lines, index + 1, indent)
                break

    peers_start = None
    for index, line in enumerate(lines):
        if re.match(r"^peers:\s*(?:#.*)?$", line):
            peers_start = index + 1
            break
    if peers_start is not None:
        peer_indent: int | None = None
        current_peer = ""
        index = peers_start
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                index += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                break
            peer_match = re.match(r"^(\s*)([a-z][a-z0-9-]{0,31})\s*:\s*(?:#.*)?$", line)
            if peer_match and (peer_indent is None or indent == peer_indent):
                current_peer = peer_match.group(2).strip()
                peer_indent = len(peer_match.group(1))
                index += 1
                continue
            if current_peer and re.match(r"^\s+python:\s*(?:#.*)?$", line):
                py_indent = len(line) - len(line.lstrip(" "))
                dists, index = _parse_dist_list(lines, index + 1, py_indent)
                peers[current_peer] = dists
                continue
            index += 1

    return Placement(hub_python=hub_python, peers=peers)


def load_placement(root: Path) -> Placement:
    from renglo_ops.release.catalog import legacy_text

    text = legacy_text(root)
    if text is None:
        return Placement()
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        hub_raw = (data.get("hub") or {}).get("python") or []
        hub = tuple(str(x).strip() for x in hub_raw if str(x).strip())
        peers: dict[str, tuple[str, ...]] = {}
        for peer_id, cfg in (data.get("peers") or {}).items():
            if not isinstance(cfg, dict):
                continue
            raw = cfg.get("python") or []
            if isinstance(raw, list):
                peers[str(peer_id).strip()] = tuple(str(x).strip() for x in raw if str(x).strip())
        return Placement(hub_python=hub, peers=peers)
    return parse_placement_text(text)


def tenant_wl_dist(catalog: list[PackageSlot] | None) -> str:
    if not catalog:
        return ""
    for slot in catalog:
        if slot.python and slot.python.endswith("-wl"):
            return slot.python
    return ""


def tenant_wl_npm(catalog: list[PackageSlot] | None) -> str:
    if not catalog:
        return ""
    for slot in catalog:
        if slot.npm and _WL_NPM.match(slot.npm):
            return slot.npm
    return ""


def _repo_for_python(dist: str, catalog: list[PackageSlot] | None) -> str:
    if catalog:
        for slot in catalog:
            if slot.python == dist and slot.repo:
                return slot.repo
    if dist.startswith("renglo-"):
        short = dist.removeprefix("renglo-")
        if short in ("lib", "api"):
            return f"renglo/renglo-{short}"
        return f"renglo/{short}"
    return ""


def _repo_for_npm(name: str, catalog: list[PackageSlot] | None) -> str:
    if name == CONSOLE_NPM_HOST:
        return "renglo/console"
    if catalog:
        for slot in catalog:
            if slot.npm == name and slot.repo:
                return slot.repo
    if name.startswith("@") and "/" in name:
        scope, pkg = name[1:].split("/", 1)
        if _WL_NPM.match(name):
            return f"{scope}/{pkg}" if pkg.endswith("-wl") else f"{scope}/wl"
        return f"{scope}/{pkg}"
    return ""


def _filter_pins(pins: dict[str, str], allowed: set[str]) -> dict[str, str]:
    return {name: version for name, version in pins.items() if name in allowed}


def _filter_repos(repos: dict[str, Any], repo_keys: set[str]) -> dict[str, Any]:
    if not repo_keys:
        return {}
    return {key: value for key, value in repos.items() if key in repo_keys}


def console_npm_for_placement(
    master_npm: dict[str, str],
    *,
    placement: Placement,
    catalog: list[PackageSlot] | None,
) -> dict[str, str]:
    """Console npm = host + wl + extension npm on hub or any peer."""
    placed_python: set[str] = set(placement.hub_python)
    for dists in placement.peers.values():
        placed_python.update(dists)

    out: dict[str, str] = {}
    host = master_npm.get(CONSOLE_NPM_HOST, "").strip()
    if host:
        out[CONSOLE_NPM_HOST] = host
    wl = tenant_wl_npm(catalog)
    if wl and wl in master_npm:
        out[wl] = master_npm[wl]

    if catalog:
        for slot in catalog:
            if not slot.npm or not slot.python:
                continue
            if slot.python in placed_python and slot.npm in master_npm:
                out[slot.npm] = master_npm[slot.npm]
    else:
        for name, version in master_npm.items():
            if name in out or name == CONSOLE_NPM_HOST:
                continue
            if _WL_NPM.match(name):
                continue
            out[name] = version
    return out


def split_master_bom(
    master: dict[str, Any],
    *,
    placement: Placement,
    catalog: list[PackageSlot] | None = None,
    peer_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Return ``(hub_bom, console_bom, {peer_id: peer_bom})``."""
    meta_keys = ("version", "created_at", "description", "train")
    meta = {key: master[key] for key in meta_keys if key in master}

    all_python = master.get("python") if isinstance(master.get("python"), dict) else {}
    all_npm = master.get("npm") if isinstance(master.get("npm"), dict) else {}
    all_repos = master.get("repos") if isinstance(master.get("repos"), dict) else {}

    wl_dist = tenant_wl_dist(catalog)
    hub_python_names = set(HUB_PYTHON_CORE)
    hub_python_names.update(placement.hub_python)
    if wl_dist:
        hub_python_names.add(wl_dist)

    hub_python = _filter_pins(all_python, hub_python_names)
    hub_repo_keys: set[str] = set()
    for dist in hub_python:
        repo = _repo_for_python(dist, catalog)
        if repo:
            hub_repo_keys.add(repo)
    hub = {**meta, "python": hub_python, "repos": _filter_repos(all_repos, hub_repo_keys)}

    console_npm = console_npm_for_placement(all_npm, placement=placement, catalog=catalog)
    console_repo_keys: set[str] = set()
    for npm_name in console_npm:
        repo = _repo_for_npm(npm_name, catalog)
        if repo and repo in all_repos:
            console_repo_keys.add(repo)
    console = {**meta, "npm": console_npm, "repos": _filter_repos(all_repos, console_repo_keys)}

    peer_boms: dict[str, dict[str, Any]] = {}
    deploy_stage = str(master.get("deploy_stage", "")).strip() or "staging"
    target_peers = {peer_id: placement.peers[peer_id]} if peer_id else placement.peers
    for pid, dists in target_peers.items():
        peer_names = set(PEER_PYTHON_CORE)
        peer_names.update(dists)
        if wl_dist:
            peer_names.add(wl_dist)
        peer_python = _filter_pins(all_python, peer_names)
        peer_repo_keys: set[str] = set()
        for dist in peer_python:
            repo = _repo_for_python(dist, catalog)
            if repo:
                peer_repo_keys.add(repo)
        peer_meta = dict(meta)
        peer_meta["deploy_stage"] = deploy_stage
        peer_meta["description"] = peer_meta.get("description") or f"Peer {pid}."
        peer_boms[pid] = {
            **peer_meta,
            "python": peer_python,
            "repos": _filter_repos(all_repos, peer_repo_keys),
        }

    return hub, console, peer_boms


def merge_union_bom(root: Path, version: str) -> dict[str, Any]:
    """Rebuild master pins for adopt/split (hub + console npm + peer placement dists)."""
    ver = _v(version)
    file_name = f"{ver}.json"

    def _read(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    hub = _read(root / "bom" / file_name)
    console = _read(root / "console_bom" / file_name)
    placement = load_placement(root)
    catalog = load_package_catalog(root)

    python: dict[str, str] = {}
    for name, pin in (hub.get("python") or {}).items():
        python[str(name)] = str(pin)

    npm: dict[str, str] = {}
    for name, pin in (console.get("npm") or {}).items():
        npm[str(name)] = str(pin)

    repos: dict[str, Any] = {}
    for block in (hub, console):
        for key, entry in (block.get("repos") or {}).items():
            repos[str(key)] = entry

    wl = tenant_wl_dist(catalog)
    for peer_id, dists in placement.peers.items():
        peer = _read(root / "peers_bom" / peer_id / file_name)
        peer_python = peer.get("python") if isinstance(peer.get("python"), dict) else {}
        allowed = set(dists) | set(PEER_PYTHON_CORE)
        if wl:
            allowed.add(wl)
        for name in allowed:
            if name in peer_python:
                python[str(name)] = str(peer_python[name])
        for key, entry in (peer.get("repos") or {}).items():
            repos[str(key)] = entry

    meta = hub or console or {}
    return {
        "version": meta.get("version") or ver,
        "created_at": meta.get("created_at", ""),
        "description": meta.get("description", ""),
        "train": meta.get("train", ""),
        "deploy_stage": meta.get("deploy_stage", ""),
        "python": python,
        "npm": npm,
        "repos": repos,
    }


def write_split_boms(
    root: Path,
    version: str,
    master: dict[str, Any],
    *,
    catalog: list[PackageSlot] | None = None,
    placement: Placement | None = None,
) -> dict[str, Any]:
    """Write hub, console, and peer BOM files; return paths written."""
    placement = placement or load_placement(root)
    catalog = catalog if catalog is not None else load_package_catalog(root)
    hub, console, peer_boms = split_master_bom(master, placement=placement, catalog=catalog)

    ver = _v(version)
    file_name = f"{ver}.json"
    (root / "bom").mkdir(parents=True, exist_ok=True)
    (root / "console_bom").mkdir(parents=True, exist_ok=True)

    hub_path = root / "bom" / file_name
    hub_path.write_text(json.dumps(hub, indent=2) + "\n", encoding="utf-8")

    console_path = root / "console_bom" / file_name
    console_path.write_text(json.dumps(console, indent=2) + "\n", encoding="utf-8")

    peer_paths: dict[str, str] = {}
    for peer_id, payload in peer_boms.items():
        peer_dir = root / "peers_bom" / peer_id
        peer_dir.mkdir(parents=True, exist_ok=True)
        peer_path = peer_dir / file_name
        peer_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        peer_paths[peer_id] = str(peer_path)

    return {
        "hub": str(hub_path),
        "console": str(console_path),
        "peers": peer_paths,
    }


def _v(version: str) -> str:
    text = (version or "").strip()
    if text.startswith("v"):
        return text.split(".json")[0] if text.endswith(".json") else text
    return f"v{text.split('.json')[0]}"


def validate_split(
    root: Path,
    version: str,
    *,
    catalog: list[PackageSlot] | None = None,
    placement: Placement | None = None,
) -> list[str]:
    """Return human-readable errors when on-disk BOMs disagree with placement."""
    errors: list[str] = []
    placement = placement or load_placement(root)
    catalog = catalog if catalog is not None else load_package_catalog(root)
    ver = _v(version)
    hub_path = root / "bom" / f"{ver}.json"
    console_path = root / "console_bom" / f"{ver}.json"

    if hub_path.is_file():
        on_disk = json.loads(hub_path.read_text(encoding="utf-8"))
        expected_hub, _, _ = split_master_bom(
            {**on_disk, "python": on_disk.get("python") or {}, "npm": {}},
            placement=placement,
            catalog=catalog,
        )
        if on_disk.get("python") != expected_hub.get("python"):
            errors.append(f"{hub_path}: python pins differ from placement split")
    else:
        errors.append(f"missing hub BOM: {hub_path}")

    if console_path.is_file():
        on_disk = json.loads(console_path.read_text(encoding="utf-8"))
        union = merge_union_bom(root, version)
        _, expected_console, _ = split_master_bom(union, placement=placement, catalog=catalog)
        if on_disk.get("npm") != expected_console.get("npm"):
            errors.append(f"{console_path}: npm pins differ from placement split")
    else:
        errors.append(f"missing console BOM: {console_path}")

    for peer_id, dists in placement.peers.items():
        peer_path = root / "peers_bom" / peer_id / f"{ver}.json"
        if not peer_path.is_file():
            errors.append(f"missing peer BOM: {peer_path}")
            continue
        on_disk = json.loads(peer_path.read_text(encoding="utf-8"))
        _, _, expected_peers = split_master_bom(
            {**on_disk, "python": on_disk.get("python") or {}, "npm": {}},
            placement=placement,
            catalog=catalog,
        )
        expected = expected_peers.get(peer_id, {})
        if on_disk.get("python") != expected.get("python"):
            errors.append(f"{peer_path}: python pins differ from placement split")
    return errors
