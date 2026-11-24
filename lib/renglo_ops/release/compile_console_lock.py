#!/usr/bin/env python3
"""Write ``constraints/console/<version>.package-lock.json`` for a console BOM.

Starts from the console host's package.json and package-lock.json, adds the
BOM npm pins at those exact versions, and asks npm to update the lock without
installing. The host tree stays at the versions already in its lock. Packages
added by the BOM, and everything they depend on, are frozen at this run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bom_manifest import load_bom, package_pins


def _workspace_root(start: Path) -> Path:
    for candidate in (start, *start.resolve().parents):
        if (candidate / "console" / "package.json").is_file() and (candidate / "extensions").is_dir():
            return candidate
    raise SystemExit(
        f"No workspace with console/package.json above {start}. Pass --workspace."
    )


def console_lock_path(bom_file: Path) -> Path:
    """``console_bom/v0.1.17.json`` → ``constraints/console/v0.1.17.package-lock.json``."""
    return bom_file.parent.parent / "constraints" / "console" / f"{bom_file.stem}.package-lock.json"


def compile_console_lock(bom_file: Path, *, workspace: Path, npmrc: Path | None = None) -> Path:
    data = load_bom(bom_file)
    npm_pins = package_pins(data, "npm")
    if not npm_pins:
        raise SystemExit(f"{bom_file} has no npm pins.")
    host_path = workspace / "console" / "package.json"
    host_lock = workspace / "console" / "package-lock.json"
    if not host_path.is_file():
        raise SystemExit(f"missing {host_path}")
    if not host_lock.is_file():
        raise SystemExit(f"missing {host_lock}; the console host tree is not locked")
    host = json.loads(host_path.read_text(encoding="utf-8"))
    host_name = str(host.get("name") or "").strip()
    pinned_host = str(npm_pins.get(host_name) or "").strip()
    if pinned_host and str(host.get("version") or "").strip() != pinned_host:
        raise SystemExit(
            f"{host_name} in the workspace is {host.get('version')}, and the BOM pins {pinned_host}."
        )
    specs = [
        f"{name}@{str(version).strip()}"
        for name, version in npm_pins.items()
        if name != host_name and str(version).strip()
    ]
    dest = console_lock_path(bom_file)
    if not specs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(host_lock, dest)
        return dest
    with tempfile.TemporaryDirectory(prefix="console-lock-") as raw:
        work = Path(raw)
        (work / "package.json").write_text(json.dumps(host, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(host_lock, work / "package-lock.json")
        if npmrc is not None:
            shutil.copyfile(npmrc, work / ".npmrc")
        completed = subprocess.run(
            [
                "npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--save-exact",
                *specs,
            ],
            cwd=work,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            sys.stderr.write(completed.stderr or completed.stdout or "")
            raise SystemExit(completed.returncode or 1)
        produced = work / "package-lock.json"
        if not produced.is_file():
            raise SystemExit("npm did not write package-lock.json")
        written = json.loads((work / "package.json").read_text(encoding="utf-8"))
        locked = json.loads(produced.read_text(encoding="utf-8"))
        locked_root = (locked.get("packages") or {}).get("") or {}
        locked_deps = locked_root.get("dependencies")
        if locked_deps is None:
            locked_deps = locked.get("dependencies") or {}
        written_deps = written.get("dependencies") or {}
        for name, version in npm_pins.items():
            if name == host_name:
                continue
            exact = str(version).strip()
            if written_deps.get(name) != exact or locked_deps.get(name) != exact:
                raise SystemExit(
                    f"{name} resolved to package {written_deps.get(name)!r} "
                    f"and lock {locked_deps.get(name)!r}; expected {exact}."
                )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Write an npm lock for a console BOM")
    parser.add_argument("bom_file", help="Path to console_bom/vX.Y.Z.json")
    parser.add_argument("--workspace", default="", help="Checkout that contains console/")
    parser.add_argument("--output", default="", help="Destination lock file")
    parser.add_argument(
        "--npmrc",
        default="",
        help="npm config to use while resolving. Not copied into the lock.",
    )
    args = parser.parse_args()
    bom_file = Path(args.bom_file)
    workspace = Path(args.workspace) if args.workspace else _workspace_root(bom_file)
    npmrc = Path(args.npmrc) if args.npmrc else None
    try:
        dest = compile_console_lock(bom_file, workspace=workspace, npmrc=npmrc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.output:
        target = Path(args.output)
        if target.resolve() != dest.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, target)
            dest = target
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
