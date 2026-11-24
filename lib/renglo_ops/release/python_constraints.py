"""Exact pins for every distribution an image build installs.

A BOM json pins first-party packages only. ``pip download`` of those names
asks PyPI for the newest match of everything underneath, so a release of a
library this repo does not mention can change the next image.
``constraints/vX.Y.Z.txt`` next to ``bom/`` lists every distribution at
``name==version``. The backend image downloads that list with ``--no-deps``
and does not resolve again.
"""

from __future__ import annotations

import sys
from pathlib import Path


def constraints_path(bom_file: Path) -> Path:
    """``bom/v0.1.17.json`` → ``constraints/v0.1.17.txt`` beside the ``bom/`` directory."""
    return bom_file.parent.parent / "constraints" / f"{bom_file.stem}.txt"


def canonical_name(name: str) -> str:
    base = name.split("[", 1)[0].strip()
    return base.lower().replace("_", "-")


def parse_exact_pins(text: str, *, source: str = "constraints") -> list[tuple[str, str]]:
    """Return ``(name, version)`` for every exact pin. Reject ranges and duplicates."""
    pins: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            raise ValueError(f"{source}:{line_number}: unsupported line {raw!r}")
        name, sep, rest = line.partition("==")
        version = rest.split(";", 1)[0].strip()
        if (
            not sep
            or not name.strip()
            or not version
            or any(op in version for op in "<>!~ ")
        ):
            raise ValueError(f"{source}:{line_number}: not an exact pin: {raw!r}")
        key = canonical_name(name)
        previous = seen.get(key)
        if previous is not None and previous != version:
            raise ValueError(
                f"{source}:{line_number}: {name.strip()} pinned as both {previous} and {version}"
            )
        seen[key] = version
        if previous is None:
            pins.append((name.strip(), version))
    return pins


def missing_bom_pins(bom_python: dict[str, str], pins: list[tuple[str, str]]) -> list[str]:
    """BOM ``name → version`` entries that the lock does not pin exactly."""
    locked = {canonical_name(name): version for name, version in pins}
    missing: list[str] = []
    for name, version in bom_python.items():
        if locked.get(canonical_name(str(name))) != str(version).strip():
            missing.append(f"{name}=={version}")
    return missing


def render_constraints(
    pins: list[tuple[str, str]],
    *,
    bom_file: Path,
    exclude_newer: str = "",
) -> str:
    """Stable, comment-headed requirements file of exact pins."""
    lines = [
        f"# Exact wheels for {bom_file.name}.",
        "# The backend image downloads this list with --no-deps.",
        "# A new release on PyPI is not installed until this file changes.",
        "# Written when the BOM is generated.",
    ]
    if exclude_newer:
        lines.append(f"# Resolved with --exclude-newer {exclude_newer}.")
    lines.append("")
    ordered = sorted(pins, key=lambda item: canonical_name(item[0]))
    lines.extend(f"{name}=={version}" for name, version in ordered)
    lines.append("")
    return "\n".join(lines)


def locked_download_command(
    *,
    dest: Path,
    constraints: Path,
    find_links: list[str] | None = None,
    sdist: bool = False,
) -> list[str]:
    """``pip download`` the lock file and nothing the resolver would add."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "-d",
        str(dest),
        "-r",
        str(constraints),
    ]
    if sdist:
        cmd.extend(["--no-binary", ":all:"])
    for link in find_links or []:
        cmd.extend(["--find-links", link])
    return cmd
