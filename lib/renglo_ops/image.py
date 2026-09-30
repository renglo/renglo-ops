"""Materialize the Lambda image build files into a BOM workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "image_data"
_FILES = (
    "Dockerfile",
    "install_backend_packages.py",
    "stage_extension_blueprints.py",
)


def main() -> None:
    dest = Path.cwd()
    for path in materialize(dest):
        print(path)


def materialize(dest: Path) -> list[Path]:
    """Copy the Dockerfile and the two in-image installers into dest.

    The Dockerfile COPY paths expect the installers under dest/scripts/.
    """
    dest = dest.expanduser().resolve()
    scripts = dest / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    dockerfile = _DATA / "Dockerfile"
    target = dest / "Dockerfile"
    shutil.copy2(dockerfile, target)
    written.append(target)
    for name in _FILES[1:]:
        path = scripts / name
        shutil.copy2(_DATA / name, path)
        written.append(path)
    return written


if __name__ == "__main__":
    main()
