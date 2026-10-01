#!/usr/bin/env python3
"""Generate or validate Console / Hub / Peer BOM files from deploy_targets placement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bom_layout import (
    load_placement,
    merge_union_bom,
    validate_split,
    write_split_boms,
)
from catalog_slots import load_package_catalog


def _load_master(root: Path, version: str, master_file: Path | None) -> dict:
    if master_file is not None:
        return json.loads(master_file.read_text(encoding="utf-8"))
    return merge_union_bom(root, version)


def main() -> int:
    parser = argparse.ArgumentParser(description="Split a master BOM into hub/console/peer files")
    parser.add_argument(
        "bom_repo",
        help="Path to *-bom repo (contains deploy_targets.yml)",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="BOM version (e.g. 0.1.7 or v0.1.7)",
    )
    parser.add_argument(
        "--master",
        help="Optional master JSON (default: merge hub + console + peers on disk)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate on-disk split matches placement (no writes)",
    )
    args = parser.parse_args()

    root = Path(args.bom_repo).resolve()
    if not (root / "deploy_targets.yml").is_file():
        print(f"missing deploy_targets.yml in {root}", file=sys.stderr)
        return 1

    catalog = load_package_catalog(root)
    placement = load_placement(root)

    if args.validate:
        errors = validate_split(root, args.version, catalog=catalog, placement=placement)
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            return 1
        print(f"OK split BOMs for {args.version}")
        return 0

    master_path = Path(args.master) if args.master else None
    try:
        master = _load_master(root, args.version, master_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    written = write_split_boms(
        root,
        args.version,
        master,
        catalog=catalog,
        placement=placement,
    )
    print(json.dumps(written, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
