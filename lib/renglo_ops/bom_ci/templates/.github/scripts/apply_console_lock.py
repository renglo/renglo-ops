#!/usr/bin/env python3
"""Point a console tree at the BOM npm lock and its exact package pins.

The console host lock freezes the host's own ranges. This replaces
``package-lock.json`` with the lock written for the console BOM and adds
that BOM's other npm pins to ``package.json``, so ``npm ci`` installs both
without asking the registry to choose versions.

Usage:
    apply_console_lock.py console_bom/vX.json constraints/console/vX.package-lock.json console
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: apply_console_lock.py console_bom/vX.json LOCK.json console/",
            file=sys.stderr,
        )
        return 2
    bom_path = Path(sys.argv[1])
    lock_path = Path(sys.argv[2])
    console_dir = Path(sys.argv[3])
    package_path = console_dir / "package.json"
    if not lock_path.is_file():
        print(
            f"Missing {lock_path}. Refusing to let npm choose versions for console BOM pins.",
            file=sys.stderr,
        )
        return 1
    try:
        bom = json.loads(bom_path.read_text(encoding="utf-8"))
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    npm_pins = bom.get("npm") or {}
    if not isinstance(npm_pins, dict) or not npm_pins:
        print(f"{bom_path} has no npm pins.", file=sys.stderr)
        return 1
    host_name = str(package.get("name") or "").strip()
    pinned_host = str(npm_pins.get(host_name) or "").strip()
    if pinned_host and str(package.get("version") or "").strip() != pinned_host:
        print(
            f"{package_path} is {host_name}@{package.get('version')}; "
            f"the BOM pins {pinned_host}.",
            file=sys.stderr,
        )
        return 1
    dependencies = package.get("dependencies")
    if not isinstance(dependencies, dict):
        dependencies = {}
        package["dependencies"] = dependencies
    for name, version in npm_pins.items():
        if str(name) == host_name:
            continue
        dependencies[str(name)] = str(version).strip()
    package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(lock_path, console_dir / "package-lock.json")
    print(f"Applied {lock_path} to {console_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
