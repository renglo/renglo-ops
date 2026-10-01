#!/usr/bin/env python3
"""Resolve extension installer/infra from a BOM pin, not a git clone.

Catalog handle → packages.<id>.python → peers_bom (or hub bom) version →
download that artifact → copy installer/infra (or /infra) into a cache CDK reads.

Local ``extensions/<handle>/installer/infra`` still wins (incubation).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from catalog_slots import PackageSlot, load_package_catalog
from extension_actions import resolve_extension_folder
from peers import handlers_bom_file, load_peers

_PIN_MARKER = ".bom-pin"
_MANIFEST = "cdk_extension.json"


def slot_for_handle(catalog: list[PackageSlot], handle: str) -> PackageSlot | None:
    want = (handle or "").strip()
    if not want:
        return None
    for slot in catalog:
        if slot.id == want:
            return slot
    norm = want.replace("_", "-")
    for slot in catalog:
        if slot.python and slot.python.replace("_", "-") == norm:
            return slot
    return None


def python_pins(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("python") if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}


def handle_pin(
    bom_root: Path,
    handle: str,
    *,
    peer_id: str = "",
    catalog: list[PackageSlot] | None = None,
) -> tuple[str, str, Path]:
    """Return ``(dist, version, pin_file)`` for one catalog handle."""
    catalog = catalog if catalog is not None else load_package_catalog(bom_root) or []
    slot = slot_for_handle(catalog, handle)
    if slot is None or not slot.python:
        raise FileNotFoundError(
            f"{handle!r} has no packages.*.python slot in {bom_root / 'deploy_targets.yml'}"
        )
    if peer_id:
        data = _load_targets(bom_root)
        peer = next((row for row in load_peers(data) if row["id"] == peer_id), None)
        if peer is None:
            raise FileNotFoundError(f"peers.{peer_id} not in deploy_targets.yml")
        pin_path = handlers_bom_file(bom_root, peer)
    else:
        pin_path = _hub_pin_file(bom_root)
    if not pin_path.is_file():
        raise FileNotFoundError(f"BOM pin file not found: {pin_path}")
    version = python_pins(pin_path).get(slot.python, "")
    if not version:
        raise FileNotFoundError(
            f"{slot.python} is not pinned in {pin_path} (needed for {handle} installer/infra)"
        )
    return slot.python, version, pin_path


def find_infra_root(tree: Path) -> Path | None:
    """Folder that contains ``installer/infra/cdk_extension.json`` or ``infra/cdk_extension.json``."""
    matches: list[Path] = []
    for path in tree.rglob(_MANIFEST):
        if any(part.endswith(".dist-info") or part.endswith(".egg-info") for part in path.parts):
            continue
        parent = path.parent
        if parent.name == "infra" and parent.parent.name == "installer":
            matches.append(parent.parent.parent)
        elif parent.name == "infra":
            matches.append(parent.parent)
    if not matches:
        return None
    matches.sort(key=lambda p: (0 if (p / "installer" / "infra" / _MANIFEST).is_file() else 1, len(p.parts)))
    return matches[0]


def extract_installer(artifact: Path, dest_folder: Path) -> Path:
    """Unpack a wheel/sdist and copy installer/infra into ``dest_folder``."""
    artifact = Path(artifact)
    if not artifact.is_file():
        raise FileNotFoundError(f"package artifact not found: {artifact}")
    with tempfile.TemporaryDirectory(prefix="installer-pkg-") as raw:
        root = Path(raw)
        _unpack_artifact(artifact, root)
        found = find_infra_root(root)
        if found is None:
            raise FileNotFoundError(
                f"{artifact.name} has no installer/infra (or /infra) "
                f"with {_MANIFEST}; republish after staging installer/infra into the package"
            )
        infra_src = found / "installer" / "infra"
        if not infra_src.is_dir():
            # Package shipped a top-level /infra tree.
            infra_src = found / "infra"
        dest_infra = dest_folder / "installer" / "infra"
        if dest_infra.exists():
            shutil.rmtree(dest_infra)
        dest_infra.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(infra_src, dest_infra)
        if not (dest_infra / _MANIFEST).is_file():
            raise FileNotFoundError(f"extracted infra missing {_MANIFEST}: {dest_infra}")
    return dest_folder


def materialize_handle(
    bom_root: Path,
    handle: str,
    dest_root: Path,
    *,
    peer_id: str = "",
    workspace: Path | None = None,
    from_artifacts: Path | None = None,
    download: Callable[[str, str, Path], Path] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Ensure ``dest_root/<handle>/installer/infra`` exists. Local clone wins."""
    handle = (handle or "").strip()
    dest_folder = dest_root / handle
    if workspace is not None and not force:
        local = resolve_extension_folder(handle, workspace=workspace)
        if local is not None:
            return {"handle": handle, "source": "local", "folder": str(local)}

    dist, version, pin_path = handle_pin(bom_root, handle, peer_id=peer_id)
    spec = f"{dist}=={version}"
    marker = dest_folder / _PIN_MARKER
    if (
        not force
        and (dest_folder / "installer" / "infra" / _MANIFEST).is_file()
        and marker.is_file()
        and marker.read_text(encoding="utf-8").strip() == spec
    ):
        return {"handle": handle, "source": "cache", "folder": str(dest_folder), "pin": spec}

    artifact = _find_artifact(from_artifacts, dist) if from_artifacts else None
    if artifact is None:
        downloader = download or download_pin
        with tempfile.TemporaryDirectory(prefix="installer-dl-") as raw:
            artifact = downloader(dist, version, Path(raw))
            extract_installer(artifact, dest_folder)
    else:
        extract_installer(artifact, dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    marker.write_text(spec + "\n", encoding="utf-8")
    return {
        "handle": handle,
        "source": "package",
        "folder": str(dest_folder),
        "pin": spec,
        "pin_file": str(pin_path),
    }


def materialize_peer_installers(
    bom_root: Path,
    dest_root: Path,
    *,
    peer_id: str = "",
    workspace: Path | None = None,
    from_artifacts: Path | None = None,
    download: Callable[[str, str, Path], Path] | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    data = _load_targets(bom_root)
    peers = load_peers(data)
    selected = [row for row in peers if not peer_id or row["id"] == peer_id]
    if peer_id and not selected:
        raise FileNotFoundError(f"peers.{peer_id} not in deploy_targets.yml")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for peer in selected:
        for handle in peer.get("extensions") or []:
            handle = str(handle).strip()
            if not handle or handle in seen:
                continue
            seen.add(handle)
            rows.append(
                materialize_handle(
                    bom_root,
                    handle,
                    dest_root,
                    peer_id=peer["id"],
                    workspace=workspace,
                    from_artifacts=from_artifacts,
                    download=download,
                    force=force,
                )
            )
    return rows


def download_pin(dist: str, version: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    spec = f"{dist}=={version}"
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "-d",
        str(dest_dir),
        "--no-deps",
        spec,
    ]
    print(f"+ pip download --no-deps {spec}", file=sys.stderr)
    subprocess.run(cmd, check=True)
    found = _find_artifact(dest_dir, dist)
    if found is None:
        raise FileNotFoundError(f"pip download produced no artifact for {spec} in {dest_dir}")
    return found


def configure_pip_from_bom(bom_root: Path) -> None:
    """Login pip to CodeArtifact using resolved ``deploy_targets.yml`` registries."""
    if os.environ.get("INSTALLER_FROM_BOM_SKIP_LOGIN", "").strip() in {"1", "true", "yes"}:
        return
    data = _load_targets(bom_root)
    from configure_codeartifact import configure_pip  # noqa: PLC0415
    from registry_targets import resolve_registries  # noqa: PLC0415

    domain_override = os.environ.get("CODEARTIFACT_DOMAIN", "").strip()
    owner_override = os.environ.get("CODEARTIFACT_DOMAIN_OWNER", "").strip()
    registries = resolve_registries(
        data,
        domain_override=domain_override,
        owner_override=owner_override,
    )
    if not registries:
        return
    configure_pip(registries)


def _load_targets(bom_root: Path) -> dict[str, Any]:
    path = bom_root / "deploy_targets.yml"
    if not path.is_file():
        raise FileNotFoundError(f"deploy_targets.yml not found: {path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required to read deploy_targets.yml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: expected a mapping")
    return data


def _hub_pin_file(bom_root: Path) -> Path:
    data = _load_targets(bom_root)
    version = str(data.get("bom") or "").strip().lstrip("v")
    if not version:
        raise FileNotFoundError(f"{bom_root / 'deploy_targets.yml'}: top-level bom: version is empty")
    return bom_root / "bom" / f"v{version}.json"


def _find_artifact(folder: Path | None, dist: str) -> Path | None:
    if folder is None or not folder.is_dir():
        return None
    want = dist.replace("-", "_").lower()
    found: list[Path] = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        if not (name.endswith(".whl") or name.endswith(".tar.gz") or name.endswith(".zip")):
            continue
        stem = name.split("-", 1)[0].replace("-", "_")
        if stem == want:
            found.append(path)
    if not found:
        return None
    found.sort(key=lambda p: (0 if p.suffix == ".whl" else 1, p.name))
    return found[0]


def _unpack_artifact(artifact: Path, dest: Path) -> None:
    name = artifact.name.lower()
    if name.endswith(".whl") or name.endswith(".zip"):
        with zipfile.ZipFile(artifact) as zf:
            zf.extractall(dest)
        return
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(artifact, "r:gz") as tf:
            tf.extractall(dest)
        return
    raise ValueError(f"unsupported package artifact: {artifact.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bom", required=True, help="*-bom checkout (deploy_targets.yml parent)")
    parser.add_argument("--dest", required=True, help="Cache root (cdk/extension-actions)")
    parser.add_argument("--peer-id", default="", help="One catalog peer; omit for every peer")
    parser.add_argument("--workspace", default="", help="Optional monorepo/platform root (local override)")
    parser.add_argument("--from-artifacts", default="", help="Reuse already-downloaded wheels/sdists")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-login", action="store_true")
    args = parser.parse_args()

    bom_root = Path(args.bom).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    artifacts = Path(args.from_artifacts).expanduser().resolve() if args.from_artifacts else None
    if not args.skip_login and artifacts is None:
        configure_pip_from_bom(bom_root)
    rows = materialize_peer_installers(
        bom_root,
        dest,
        peer_id=args.peer_id,
        workspace=workspace,
        from_artifacts=artifacts,
        force=bool(args.force),
    )
    for row in rows:
        pin = f" {row['pin']}" if row.get("pin") else ""
        print(f"{row['handle']}: {row['source']}{pin} → {row['folder']}")
    if not rows:
        print("No peer extensions to materialize.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
