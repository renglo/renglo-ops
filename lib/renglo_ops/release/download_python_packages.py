#!/usr/bin/env python3
"""Download BOM python pins into wheels/ (CodeArtifact must already be configured).

When the BOM has python pins and this is not ``--no-deps``,
``constraints/vX.Y.Z.txt`` is required. Those exact versions are downloaded
with ``--no-deps``. A package that is not in the file is not installed, and a
newer release of a package that is in the file is not selected.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from bom_manifest import load_bom, package_pins, python_install_specs

try:
    from python_constraints import (
        constraints_path,
        locked_download_command,
        missing_bom_pins,
        parse_exact_pins,
    )
except ImportError:  # imported as renglo_ops.release.download_python_packages
    from renglo_ops.release.python_constraints import (
        constraints_path,
        locked_download_command,
        missing_bom_pins,
        parse_exact_pins,
    )


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
        help="Pass --no-deps to pip download of the BOM pins only (peer sdist path)",
    )
    parser.add_argument(
        "--sdist",
        action="store_true",
        help="Download source distributions only (--no-binary :all:)",
    )
    parser.add_argument(
        "--constraints",
        default="",
        help="Exact-pin file (default: constraints/vX.Y.Z.txt beside bom/)",
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
    if args.no_deps:
        cmd = [sys.executable, "-m", "pip", "download", "--no-deps", "-d", str(dest)]
        if args.sdist:
            cmd.extend(["--no-binary", ":all:"])
        for link in args.find_links:
            cmd.extend(["--find-links", str(Path(link).expanduser().resolve())])
        cmd.extend(specs)
    else:
        constraints = Path(args.constraints) if args.constraints else constraints_path(path)
        if not constraints.is_file():
            print(
                f"Missing {constraints}. Refusing to let pip choose transitive versions.\n"
                "This file is written when the BOM is generated.",
                file=sys.stderr,
            )
            return 1
        try:
            pins = parse_exact_pins(constraints.read_text(encoding="utf-8"), source=str(constraints))
            missing = missing_bom_pins(package_pins(data, "python"), pins)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if missing:
            print(
                f"{constraints} does not pin the BOM python packages: {', '.join(missing)}.\n"
                "Regenerate it after changing the BOM.",
                file=sys.stderr,
            )
            return 1
        cmd = locked_download_command(
            dest=dest,
            constraints=constraints,
            find_links=[str(Path(link).expanduser().resolve()) for link in args.find_links],
            sdist=args.sdist,
        )

    print(f"Downloading {len(specs)} pin(s) into {dest.resolve()}")
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
