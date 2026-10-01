#!/usr/bin/env python3
"""pip-install backend artifacts: CodeArtifact wheels, then local checkout trees.

Used by the Lambda Dockerfile. Install order:

  1. wheels/*.whl (and .tar.gz) downloaded from the publisher registry
  2. pip-installable trees under dev/ (renglo-lib, then renglo-api, then others)
  3. extensions/*/package when pyproject.toml or setup.py exists

UI-only trees are skipped.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from stage_extension_blueprints import stage_extension_blueprints


def _is_python_package(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / "pyproject.toml").is_file() or (path / "setup.py").is_file()


def _dev_sort_key(path: Path) -> tuple[int, str]:
    name = path.name
    if name == "renglo-lib":
        return (0, name)
    if name == "renglo-api":
        return (1, name)
    return (2, name)


def _wheel_sort_key(path: Path) -> tuple[int, str]:
    """Prefer core platform wheels before dependents when pip needs order hints."""
    stem = path.name.lower()
    if stem.startswith("renglo_lib"):
        return (0, stem)
    if stem.startswith("renglo_api"):
        return (1, stem)
    if stem.startswith("renglo_"):
        return (2, stem)
    return (3, stem)


def find_wheels(root: Path) -> list[Path]:
    wheels = root / "wheels"
    if not wheels.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(wheels.iterdir(), key=_wheel_sort_key):
        if path.suffix == ".whl" or path.name.endswith(".tar.gz"):
            found.append(path)
    return found


def _install_wheels(root: Path, wheels: list[Path]) -> None:
    """Install a pre-downloaded wheelhouse (CodeArtifact pins + transitive deps).

    Wheels must be installed together with ``--no-index`` so pip resolves
    ``renglo-api`` → ``renglo-lib`` from ``wheels/`` instead of public PyPI.
    """
    if not wheels:
        return
    wheels_dir = root / "wheels"
    rels = ", ".join(w.relative_to(root).as_posix() for w in wheels)
    print(f"Installing wheelhouse ({len(wheels)} artifacts): {rels}")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            f"--find-links={wheels_dir}",
            *[str(w) for w in wheels],
        ],
        check=True,
    )


def _pip_install(target: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", str(target)],
        check=True,
    )


def _install_local_package(pkg: Path) -> None:
    """pip-install a local tree, staging sibling blueprints/ into a temp copy.

    Staging in a temp dir keeps the git checkout clean. The source of truth
    stays at ``extensions/<name>/blueprints/``.
    """
    extension_root = pkg.parent
    src_blueprints = extension_root / "blueprints"
    has_blueprints = (
        pkg.parent.name != "dev"
        and src_blueprints.is_dir()
        and any(src_blueprints.glob("*.json"))
    )
    if not has_blueprints:
        _pip_install(pkg)
        return

    with tempfile.TemporaryDirectory(prefix="renglo-ext-") as tmp:
        work = Path(tmp) / extension_root.name
        dest_pkg = work / "package"
        shutil.copytree(
            pkg,
            dest_pkg,
            ignore=shutil.ignore_patterns("*.egg-info", "build", "dist", "__pycache__"),
        )
        shutil.copytree(src_blueprints, work / "blueprints")
        staged = stage_extension_blueprints(extension_root=work)
        if staged:
            print(f"  staged blueprints -> {staged}")
        _pip_install(dest_pkg)


def find_packages(root: Path) -> list[Path]:
    found: list[Path] = []
    dev = root / "dev"
    if dev.is_dir():
        dev_pkgs = [p for p in dev.iterdir() if _is_python_package(p)]
        found.extend(sorted(dev_pkgs, key=_dev_sort_key))

    extensions = root / "extensions"
    if extensions.is_dir():
        ext_pkgs = []
        for child in sorted(extensions.iterdir(), key=lambda p: p.name):
            pkg = child / "package"
            if _is_python_package(pkg):
                ext_pkgs.append(pkg)
        found.extend(ext_pkgs)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Install backend Python packages from a checkout tree")
    parser.add_argument(
        "--root",
        default=".",
        help="Workspace root containing dev/ and extensions/ (default: cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print packages that would be installed",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    wheels = find_wheels(root)
    packages = find_packages(root)
    if not wheels and not packages:
        print(
            f"No wheels under {root}/wheels and no packages under {root}/dev or {root}/extensions/*/package",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        if wheels:
            print(f"pip install --no-index --find-links=./wheels {' '.join(w.relative_to(root).as_posix() for w in wheels)}")
        for pkg in packages:
            print(f"pip install ./{pkg.relative_to(root).as_posix()}")
        return 0

    if wheels:
        _install_wheels(root, wheels)

    for pkg in packages:
        rel = pkg.relative_to(root)
        print(f"Installing {rel.as_posix()}")
        _install_local_package(pkg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
