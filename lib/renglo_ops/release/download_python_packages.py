#!/usr/bin/env python3
"""Download BOM python pins into wheels/ (CodeArtifact must already be configured)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from bom_manifest import load_bom, python_install_specs


def main() -> int:
    parser = argparse.ArgumentParser(description="pip download BOM JSON python pins")
    parser.add_argument("bom_file", help="Path to a BOM JSON file")
    parser.add_argument(
        "--dest",
        default="wheels",
        help="Directory to write wheels/sdists into (default: wheels)",
    )
    parser.add_argument(
        "--targets",
        default="",
        help="Optional deploy_targets.yml (accepted for compose/CI compatibility)",
    )
    parser.add_argument(
        "--find-links",
        action="append",
        default=[],
        help="Extra pip find-links directory (repeatable)",
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Pass --no-deps to pip download",
    )
    parser.add_argument(
        "--sdist",
        action="store_true",
        help="Download source distributions only (--no-binary :all:)",
    )
    args = parser.parse_args()

    path = Path(args.bom_file)
    dest = Path(args.dest)
    try:
        data = load_bom(path)
        specs = python_install_specs(data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not specs:
        print("No python pins in BOM JSON; nothing to download.")
        dest.mkdir(parents=True, exist_ok=True)
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "download", "-d", str(dest)]
    if args.no_deps:
        cmd.append("--no-deps")
    if args.sdist:
        cmd.extend(["--no-binary", ":all:"])
    for link in args.find_links:
        cmd.extend(["--find-links", str(Path(link).expanduser().resolve())])
    cmd.extend(specs)

    print(f"Downloading {len(specs)} pin(s) into {dest.resolve()}")
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
