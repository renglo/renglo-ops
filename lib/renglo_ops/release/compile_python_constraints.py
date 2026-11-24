#!/usr/bin/env python3
"""Write ``constraints/vX.Y.Z.txt`` for one backend BOM.

Resolves third-party dependencies declared by the workspace checkouts whose
versions match the BOM, then records every distribution at an exact version.
First-party packages are taken from the BOM pins and are not downloaded.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from bom_manifest import load_bom, package_pins
from python_constraints import (
    canonical_name,
    constraints_path,
    find_uv,
    parse_exact_pins,
    render_constraints,
)

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def _workspace_root(start: Path) -> Path:
    for candidate in (start, *start.resolve().parents):
        if (candidate / "dev").is_dir() and (candidate / "extensions").is_dir():
            return candidate
    raise SystemExit(
        f"No workspace with dev/ and extensions/ above {start}. Pass --workspace."
    )


def _local_packages(root: Path) -> dict[str, tuple[str, Path]]:
    found: dict[str, tuple[str, Path]] = {}
    candidates: list[Path] = []
    dev = root / "dev"
    if dev.is_dir():
        candidates.extend(path for path in dev.iterdir() if (path / "pyproject.toml").is_file())
    extensions = root / "extensions"
    if extensions.is_dir():
        for child in extensions.iterdir():
            package = child / "package"
            if (package / "pyproject.toml").is_file():
                candidates.append(package)
    for path in candidates:
        data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
        project = data.get("project") or {}
        name = str(project.get("name") or "").strip()
        version = str(project.get("version") or "").strip()
        if name and version:
            found[canonical_name(name)] = (version, path)
    return found


def _dependency_name(requirement: str) -> str:
    match = _REQ_NAME.match(requirement)
    if not match:
        raise ValueError(f"cannot read a package name from {requirement!r}")
    return match.group(1)


def _third_party_requirements(
    bom_python: dict[str, str],
    local: dict[str, tuple[str, Path]],
) -> list[str]:
    """Dependency strings from matching checkouts, skipping other BOM packages."""
    bom_names = {canonical_name(name) for name in bom_python}
    requirements: list[str] = []
    for name, version in bom_python.items():
        key = canonical_name(name)
        located = local.get(key)
        if located is None:
            raise SystemExit(
                f"{name}=={version} is not checked out. Compile from a workspace that contains it."
            )
        local_version, path = located
        if local_version != str(version).strip():
            raise SystemExit(
                f"{name} is {local_version} in {path}, and the BOM pins {version}. "
                "The lock has to be compiled from the versions the image installs."
            )
        project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = (project.get("project") or {}).get("dependencies") or []
        if not isinstance(dependencies, list):
            raise SystemExit(f"{path / 'pyproject.toml'} project.dependencies is not a list")
        for requirement in dependencies:
            text = str(requirement).strip()
            if not text:
                continue
            if canonical_name(_dependency_name(text)) in bom_names:
                continue
            requirements.append(text)
    return requirements


def _compile_with_uv(
    requirements: list[str],
    *,
    exclude_newer: str,
    python_version: str,
    extra_args: list[str] | None = None,
) -> list[tuple[str, str]]:
    if not requirements:
        return []
    uv = find_uv()
    if not uv:
        raise SystemExit(
            "uv is not installed. Install uv and rerun this command. "
            "https://docs.astral.sh/uv/"
        )
    cmd = [
        uv,
        "pip",
        "compile",
        "--python-version",
        python_version,
        "--no-header",
        "--no-annotate",
        *(extra_args or []),
        "-",
    ]
    if exclude_newer:
        cmd.extend(["--exclude-newer", exclude_newer])
    try:
        completed = subprocess.run(
            cmd,
            input="\n".join(requirements) + "\n",
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit("uv is not installed. Install uv and rerun this command.") from exc
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr or completed.stdout or "")
        raise SystemExit(completed.returncode)
    return parse_exact_pins(completed.stdout, source="uv pip compile")


def compile_constraints(
    bom_file: Path,
    *,
    workspace: Path,
    exclude_newer: str = "",
    python_version: str = "3.12",
    also: list[str] | None = None,
) -> str:
    data = load_bom(bom_file)
    bom_python = package_pins(data, "python")
    if not bom_python:
        raise SystemExit(f"{bom_file} has no python pins.")
    local = _local_packages(workspace)
    third_party = _third_party_requirements(bom_python, local)
    third_party.extend(item.strip() for item in (also or []) if item.strip())
    resolved = _compile_with_uv(
        third_party,
        exclude_newer=exclude_newer,
        python_version=python_version,
    )
    bom_names = {canonical_name(name) for name in bom_python}
    pins = [(name, version) for name, version in resolved if canonical_name(name) not in bom_names]
    pins.extend((str(name), str(version).strip()) for name, version in bom_python.items())
    return render_constraints(pins, bom_file=bom_file, exclude_newer=exclude_newer)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write exact pins for a backend BOM")
    parser.add_argument("bom_file", help="Path to bom/vX.Y.Z.json")
    parser.add_argument(
        "--workspace",
        default="",
        help="Checkout that contains dev/ and extensions/ (default: search parents)",
    )
    parser.add_argument(
        "--exclude-newer",
        default="",
        help="Ignore distributions uploaded after this time (passed to uv)",
    )
    parser.add_argument(
        "--python-version",
        default="3.12",
        help="Interpreter version the Lambda image runs (default: 3.12)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Destination (default: constraints/<bom stem>.txt beside bom/)",
    )
    parser.add_argument(
        "--also",
        action="append",
        default=[],
        help="Extra requirement to resolve into the lock (repeatable)",
    )
    args = parser.parse_args()

    bom_file = Path(args.bom_file)
    workspace = Path(args.workspace) if args.workspace else _workspace_root(bom_file)
    try:
        text = compile_constraints(
            bom_file,
            workspace=workspace,
            exclude_newer=args.exclude_newer.strip(),
            python_version=args.python_version.strip() or "3.12",
            also=args.also,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    dest = Path(args.output) if args.output else constraints_path(bom_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
