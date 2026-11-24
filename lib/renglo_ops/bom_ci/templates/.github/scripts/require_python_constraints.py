#!/usr/bin/env python3
"""Fail unless constraints/vX.Y.Z.txt pins every python package in the BOM.

The backend image used to ``pip download`` the BOM names and let pip pick
the newest transitive release. This script is the check the workflow runs
before ``pip download --no-deps -r`` that file. It stays in the BOM repo so
the check runs with the renglo-ops version already pinned in production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def canonical_name(name: str) -> str:
    return name.split("[", 1)[0].strip().lower().replace("_", "-")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: require_python_constraints.py bom/vX.Y.Z.json", file=sys.stderr)
        return 2
    bom_path = Path(sys.argv[1])
    constraints = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else bom_path.parent.parent / "constraints" / f"{bom_path.stem}.txt"
    )
    if not constraints.is_file():
        print(
            f"Missing {constraints}. Refusing to let pip choose transitive versions.",
            file=sys.stderr,
        )
        return 1
    try:
        bom = json.loads(bom_path.read_text(encoding="utf-8"))
        text = constraints.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    locked: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, rest = line.partition("==")
        version = rest.split(";", 1)[0].strip()
        if not sep or not name.strip() or not version or any(op in version for op in "<>!~ "):
            print(f"{constraints}:{line_number}: not an exact pin: {raw}", file=sys.stderr)
            return 1
        locked[canonical_name(name)] = version

    python_pins = bom.get("python") or {}
    if not isinstance(python_pins, dict):
        print(f"{bom_path}: python must be an object of name → version", file=sys.stderr)
        return 1
    missing = [
        f"{name}=={version}"
        for name, version in python_pins.items()
        if locked.get(canonical_name(str(name))) != str(version).strip()
    ]
    if missing:
        print(
            f"{constraints} does not pin the BOM python packages: {', '.join(missing)}.",
            file=sys.stderr,
        )
        return 1
    print(f"Using {constraints} ({len(locked)} exact pins).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
