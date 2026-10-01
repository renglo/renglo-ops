#!/usr/bin/env python3
"""Unpack a pinned @renglo/console tarball into console/.

The console job builds the host app from a tree (npm ci && npm run build).
When the BOM pins @renglo/console instead of cloning renglo/console, this
script fetches that package and writes it to console/. CodeArtifact login
must already be configured for npm.

Usage:
    python scripts/materialize_console.py bom/v0.1.8.json
    python scripts/materialize_console.py bom/v0.1.8.json --tarball /tmp/pkg.tgz
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from bom_manifest import CONSOLE_NPM_PACKAGE, console_host_spec, load_bom


def _is_unused(dest: Path) -> bool:
    if not dest.exists():
        return True
    names = {p.name for p in dest.iterdir()}
    return names <= {".keep"}


def unpack_tarball(tarball: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="console-unpack-") as raw:
        work = Path(raw)
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(work, filter="data")
        inner = work / "package"
        if not inner.is_dir():
            raise RuntimeError(f"{tarball}: npm tarball missing package/ directory")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(inner), str(dest))


def _verify_host(dest: Path) -> None:
    pkg_file = dest / "package.json"
    if not pkg_file.is_file():
        raise RuntimeError(f"{dest}: unpacked tree has no package.json")
    try:
        name = str(json.loads(pkg_file.read_text(encoding="utf-8")).get("name", "")).strip()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{pkg_file}: invalid JSON") from exc
    if name != CONSOLE_NPM_PACKAGE:
        raise RuntimeError(f"{pkg_file}: expected name {CONSOLE_NPM_PACKAGE!r}, got {name!r}")


def pack_from_registry(spec: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"+ npm pack {spec}")
    subprocess.run(
        ["npm", "pack", spec, "--pack-destination", str(dest_dir)],
        check=True,
    )
    tarballs = sorted(dest_dir.glob("*.tgz"))
    if len(tarballs) != 1:
        raise RuntimeError(f"npm pack {spec} produced {len(tarballs)} tarball(s) in {dest_dir}")
    return tarballs[0]


def materialize(dest_root: Path, spec: str, tarball: Path | None = None) -> str:
    dest = dest_root / "console"
    if (dest / "package.json").is_file():
        print(f"console/ already present; skipping unpack of {spec}")
        return "skipped"
    if not _is_unused(dest):
        raise RuntimeError(f"console/ exists but is not a package: {dest}")

    if tarball is not None:
        unpack_tarball(tarball, dest)
    else:
        with tempfile.TemporaryDirectory(prefix="console-pack-") as raw:
            packed = pack_from_registry(spec, Path(raw))
            unpack_tarball(packed, dest)

    _verify_host(dest)
    print(f"  unpacked {spec} -> console/")
    return "unpacked"


def main() -> int:
    parser = argparse.ArgumentParser(description="Unpack @renglo/console into console/")
    parser.add_argument("bom_file", help="Path to a BOM JSON file")
    parser.add_argument(
        "--dest",
        default=".",
        help="Workspace root (default: current directory)",
    )
    parser.add_argument(
        "--tarball",
        default="",
        help="Use this .tgz instead of npm pack (tests / offline)",
    )
    args = parser.parse_args()

    bom_file = Path(args.bom_file)
    dest_root = Path(args.dest).resolve()
    tarball = Path(args.tarball).resolve() if args.tarball else None
    try:
        data = load_bom(bom_file)
        spec = console_host_spec(data)
        if not spec:
            print(f"No {CONSOLE_NPM_PACKAGE} pin in {bom_file}; nothing to unpack.")
            return 0
        materialize(dest_root, spec, tarball=tarball)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
