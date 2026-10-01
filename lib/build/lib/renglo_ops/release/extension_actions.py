"""Extension actions IAM: policy JSON is the contract; catalog decides who gets it.

Hub: ``hub.python`` (via ``packages:`` slot) → installer/infra policy → ``{env}_tt_role``.
Peer: ``peers.<id>.extensions`` → same JSON → ``{env}-peer-{id}-role`` and ``-ecs-task``.

Overflow ``{env}-handlers-*`` roles are not an attach target.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bom_layout import load_placement
from catalog_slots import PackageSlot, load_package_catalog, parse_package_catalog

_MANIFEST = Path("installer") / "infra" / "cdk_extension.json"


@dataclass(frozen=True)
class ExtensionActionsSpec:
    handle: str
    folder: Path
    manifest: dict[str, Any]
    document: dict[str, Any]
    policy_name_template: str
    policy_description: str

    def hub_policy_name(self, env_name: str) -> str:
        return str(self.policy_name_template).replace("{env}", env_name)


def peer_actions_policy_name(env_name: str, peer_id: str, handle: str) -> str:
    """Unique name so peer stacks never collide with Stack B managed policies."""
    return f"{env_name}-peer-{peer_id}-{handle}-actions"


def handle_from_extension_folder(folder: Path) -> str:
    bundle = folder / "bundle.json"
    if bundle.is_file():
        data = json.loads(bundle.read_text(encoding="utf-8"))
        source = str(data.get("source_repo", "")).strip()
        if source:
            return Path(source).name
    name = folder.name.strip()
    if name and name != "extension":
        return name
    return ""


def repo_workspace_root(*, start: Path) -> Path:
    """Monorepo root that contains ``extensions/`` (parent of ``ops/``)."""
    override = os.environ.get("EXTENSIONS_WORKSPACE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    here = start.resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "extensions").is_dir() and (candidate / "ops").is_dir():
            return candidate
    return here


def find_deploy_targets(*, cdk_dir: Path, github_repo: str = "") -> Path | None:
    override = os.environ.get("RENGLO_CATALOG", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path
    packaged = cdk_dir / "deploy_targets.yml"
    if packaged.is_file():
        return packaged
    ops = None
    if len(cdk_dir.parents) >= 2 and cdk_dir.parents[1].name == "launcher":
        ops = cdk_dir.parents[2]
    elif len(cdk_dir.parents) >= 1 and cdk_dir.parent.name == "bom-helper":
        ops = cdk_dir.parent.parent
    if ops is None:
        for parent in cdk_dir.parents:
            if (parent / "bom-helper").is_dir() and (parent / "launcher").is_dir():
                ops = parent
                break
    if ops is None:
        return None
    checkout = github_repo.strip().rstrip("/").split("/")[-1] if github_repo else ""
    if checkout:
        candidate = ops / checkout / "deploy_targets.yml"
        if candidate.is_file():
            return candidate
    matches = sorted(p for p in ops.glob("*-bom/deploy_targets.yml") if p.is_file())
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_extension_folder(
    handle: str,
    *,
    workspace: Path,
    extra_roots: list[Path] | None = None,
    repo: str = "",
) -> Path | None:
    handle = (handle or "").strip()
    if not handle:
        return None
    candidates: list[Path] = []
    if repo.strip():
        rel = repo.strip()
        if "/" in rel and not rel.startswith("extensions/"):
            rel = f"extensions/{rel.split('/', 1)[-1]}"
        candidates.append(workspace / rel)
    candidates.append(workspace / "extensions" / handle)
    candidates.append(workspace / handle)
    for root in extra_roots or []:
        candidates.append(Path(root) / handle)
    seen: set[Path] = set()
    for folder in candidates:
        folder = folder.resolve()
        if folder in seen:
            continue
        seen.add(folder)
        if (folder / _MANIFEST).is_file():
            return folder
    return None


def load_extension_config(folder: Path) -> dict[str, Any]:
    config_path = folder / "installer" / "infra" / "extension_config.json"
    if not config_path.is_file():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_actions_spec(folder: Path, handle: str = "") -> ExtensionActionsSpec | None:
    manifest_path = folder / _MANIFEST
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path}: expected a JSON object")
    policy_rel = str(manifest.get("policy_file", "")).strip()
    if not policy_rel:
        return None
    policy_path = folder / "installer" / "infra" / policy_rel
    if not policy_path.is_file():
        raise FileNotFoundError(f"Extension policy document not found: {policy_path}")
    document = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"{policy_path}: expected a JSON object")
    resolved_handle = handle.strip() or handle_from_extension_folder(folder) or folder.name
    template = str(manifest.get("policy_name") or "{env}-{handle}-actions")
    template = template.replace("{handle}", resolved_handle)
    description = str(
        manifest.get("policy_description") or f"Extension actions policy ({resolved_handle})"
    )
    return ExtensionActionsSpec(
        handle=resolved_handle,
        folder=folder,
        manifest=manifest,
        document=document,
        policy_name_template=template,
        policy_description=description,
    )


def _slot_repo_path(slot: PackageSlot) -> str:
    repo = (slot.repo or "").strip()
    if not repo:
        return ""
    if repo.startswith("extensions/") or repo.startswith("dev/"):
        return repo
    name = repo.split("/", 1)[-1]
    return f"extensions/{name}"


def hub_actions_specs(
    targets_path: Path,
    workspace: Path,
    *,
    extra_roots: list[Path] | None = None,
) -> list[ExtensionActionsSpec]:
    """Specs for extensions whose python dist is on ``hub.python`` and that declare a policy."""
    if not targets_path.is_file():
        return []
    placement = load_placement(targets_path.parent)
    catalog = load_package_catalog(targets_path.parent)
    if catalog is None:
        catalog = parse_package_catalog(targets_path.read_text(encoding="utf-8")) or []
    dist_to_slot: dict[str, PackageSlot] = {
        slot.python: slot for slot in catalog if slot.python
    }
    specs: list[ExtensionActionsSpec] = []
    seen: set[str] = set()
    for dist in placement.hub_python:
        slot = dist_to_slot.get(dist)
        if slot is None:
            continue
        folder = resolve_extension_folder(
            slot.id,
            workspace=workspace,
            extra_roots=extra_roots,
            repo=_slot_repo_path(slot),
        )
        if folder is None:
            continue
        spec = load_actions_spec(folder, slot.id)
        if spec is None or spec.handle in seen:
            continue
        seen.add(spec.handle)
        specs.append(spec)
    return specs


def peer_actions_specs(
    handles: list[str],
    workspace: Path,
    *,
    extra_roots: list[Path] | None = None,
    required: bool = True,
) -> list[ExtensionActionsSpec]:
    """Specs for peer ``extensions:`` handles. Missing installer/infra is an error when required."""
    specs: list[ExtensionActionsSpec] = []
    for handle in handles:
        handle = str(handle).strip()
        if not handle:
            continue
        folder = resolve_extension_folder(
            handle, workspace=workspace, extra_roots=extra_roots
        )
        if folder is None:
            if required:
                raise FileNotFoundError(
                    f"Peer extension {handle!r} has no installer/infra/cdk_extension.json. "
                    f"Peer CDK loads it from the BOM pin (download that package) or from "
                    f"{workspace / 'extensions' / handle} during incubation."
                )
            continue
        spec = load_actions_spec(folder, handle)
        if spec is None:
            if required:
                raise RuntimeError(
                    f"Peer extension {handle!r} is missing policy_file in {folder / _MANIFEST}"
                )
            continue
        specs.append(spec)
    return specs


def _peer_extension_handles(targets_path: Path) -> list[str]:
    """Peer ``extensions:`` handles from the catalog (PyYAML optional)."""
    text = targets_path.read_text(encoding="utf-8")
    try:
        from peers import load_peers
        import yaml
    except ImportError:
        return _peer_extension_handles_from_text(text)
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            return _peer_extension_handles_from_text(text)
        handles: list[str] = []
        seen: set[str] = set()
        for peer in load_peers(data):
            for handle in peer.get("extensions") or []:
                h = str(handle).strip()
                if not h or h in seen:
                    continue
                seen.add(h)
                handles.append(h)
        return handles
    except Exception:
        return _peer_extension_handles_from_text(text)


def _peer_extension_handles_from_text(text: str) -> list[str]:
    handles: list[str] = []
    seen: set[str] = set()
    in_peers = False
    in_list = False
    list_indent: int | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and stripped.rstrip(":") == "peers":
            in_peers = True
            in_list = False
            continue
        if indent == 0:
            in_peers = False
            in_list = False
            continue
        if not in_peers:
            continue
        if in_list:
            if list_indent is not None and indent < list_indent:
                in_list = False
            elif stripped.startswith("-"):
                handle = stripped[1:].strip().strip("'\"")
                if handle and handle not in seen:
                    seen.add(handle)
                    handles.append(handle)
                continue
            else:
                in_list = False
        inline = re.match(r"^extensions:\s*\[([^\]]*)\]\s*$", stripped)
        if inline:
            for part in inline.group(1).split(","):
                handle = part.strip().strip("'\"")
                if handle and handle not in seen:
                    seen.add(handle)
                    handles.append(handle)
            continue
        if re.match(r"^extensions:\s*$", stripped):
            in_list = True
            list_indent = indent + 1
    return handles


def catalog_actions_specs(
    targets_path: Path,
    workspace: Path,
    *,
    extra_roots: list[Path] | None = None,
) -> list[ExtensionActionsSpec]:
    """Hub + peer installer specs (unique by handle)."""
    specs = list(hub_actions_specs(targets_path, workspace, extra_roots=extra_roots))
    seen = {s.handle for s in specs}
    if not targets_path.is_file():
        return specs
    for spec in peer_actions_specs(
        _peer_extension_handles(targets_path),
        workspace,
        extra_roots=extra_roots,
        required=False,
    ):
        if spec.handle in seen:
            continue
        seen.add(spec.handle)
        specs.append(spec)
    return specs


def extra_action_roots(cdk_dir: Path) -> list[Path]:
    bundled = cdk_dir / "extension-actions"
    return [bundled] if bundled.is_dir() else []


def bundle_catalog_installer_infra(
    cdk_dir: Path,
    workspace: Path,
    targets_path: Path | None,
) -> None:
    """Copy hub + peer extension installer/infra into the packaged CDK tree."""
    dest_root = cdk_dir / "extension-actions"
    if dest_root.exists():
        shutil.rmtree(dest_root)
    if targets_path is None or not targets_path.is_file():
        return
    specs = catalog_actions_specs(targets_path, workspace)
    if not specs:
        return
    dest_root.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        infra_src = spec.folder / "installer" / "infra"
        infra_dest = dest_root / spec.handle / "installer" / "infra"
        shutil.copytree(infra_src, infra_dest)


def bundle_hub_actions_infra(
    cdk_dir: Path,
    workspace: Path,
    targets_path: Path | None,
) -> None:
    bundle_catalog_installer_infra(cdk_dir, workspace, targets_path)
