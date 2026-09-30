#!/usr/bin/env python3
"""Copy repo-root blueprints/*.json into package/<import>/blueprints/.

Does not move or change the git source of truth. Publish and pip-install
use the staged copies so the wheel contains the current-tag JSON.

Usage:
    python scripts/stage_extension_blueprints.py
    python scripts/stage_extension_blueprints.py --root /path/to/extension
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SKIP_DIRS = frozenset({"tests", "test", "docs", "build", "dist"})


def find_import_package(package_dir: Path) -> Path | None:
    if not package_dir.is_dir():
        return None
    candidates: list[Path] = []
    for child in sorted(package_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue
        if child.name.endswith(".egg-info"):
            continue
        if (child / "__init__.py").is_file():
            candidates.append(child)
    if not candidates:
        return None
    return candidates[0]


def stage_extension_blueprints(
    *,
    extension_root: Path | None = None,
    package_dir: Path | None = None,
) -> Path | None:
    """Copy ``<root>/blueprints/*.json`` into ``package/<import>/blueprints/``.

    Returns the destination directory, or None if there is nothing to stage.
    """
    if package_dir is not None:
        package_dir = Path(package_dir).resolve()
        root = package_dir.parent
    elif extension_root is not None:
        root = Path(extension_root).resolve()
        package_dir = root / "package"
    else:
        root = Path.cwd().resolve()
        package_dir = root / "package"

    src = root / "blueprints"
    files = sorted(src.glob("*.json")) if src.is_dir() else []
    if not files:
        return None

    import_pkg = find_import_package(package_dir)
    if import_pkg is None:
        raise FileNotFoundError(
            f"No import package (directory with __init__.py) under {package_dir}"
        )

    dest = import_pkg / "blueprints"
    dest.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copy2(path, dest / path.name)
    return dest


def stage_extension_installer(
    *,
    extension_root: Path | None = None,
    package_dir: Path | None = None,
) -> Path | None:
    """Copy ``<root>/installer/infra`` into ``package/<import>/installer/infra``.

    Peer / hub CDK then read that tree from the published wheel (same pin as
    runtime code). Does not move the git source of truth.
    """
    if package_dir is not None:
        package_dir = Path(package_dir).resolve()
        root = package_dir.parent
    elif extension_root is not None:
        root = Path(extension_root).resolve()
        package_dir = root / "package"
    else:
        root = Path.cwd().resolve()
        package_dir = root / "package"

    src = root / "installer" / "infra"
    if not src.is_dir() or not (src / "cdk_extension.json").is_file():
        return None

    import_pkg = find_import_package(package_dir)
    if import_pkg is None:
        raise FileNotFoundError(
            f"No import package (directory with __init__.py) under {package_dir}"
        )

    dest = import_pkg / "installer" / "infra"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=".",
        help="Extension repo root (default: cwd). Expects blueprints/ and package/.",
    )
    parser.add_argument(
        "--package-dir",
        default="",
        help="Override package/ directory (default: <root>/package)",
    )
    args = parser.parse_args()
    root = Path(args.root)
    package_dir = Path(args.package_dir) if args.package_dir else None
    try:
        dest = stage_extension_blueprints(extension_root=root, package_dir=package_dir)
        infra = stage_extension_installer(extension_root=root, package_dir=package_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if dest is None:
        print(f"No blueprints/*.json under {root.resolve() / 'blueprints'}; nothing to stage.")
    else:
        count = len(list(dest.glob("*.json")))
        print(f"Staged {count} blueprint(s) into {dest}")
    if infra is None:
        print(f"No installer/infra under {root.resolve() / 'installer' / 'infra'}; nothing to stage.")
    else:
        print(f"Staged installer/infra into {infra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
