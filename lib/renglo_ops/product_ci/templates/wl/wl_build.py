"""Copy repo-root locales/ and assets/ into src/wl before the wheel is packed."""

from __future__ import annotations

import shutil
from pathlib import Path

_DATA_DIRS = ("locales", "assets")
_DATA_FILES = ("product.yaml",)


def stage_wl_data(root: Path | None = None) -> Path:
    repo = Path(__file__).resolve().parent if root is None else root
    dest = repo / "src" / "wl"
    dest.mkdir(parents=True, exist_ok=True)
    for name in _DATA_DIRS:
        src = repo / name
        tgt = dest / name
        if tgt.exists():
            shutil.rmtree(tgt)
        if src.is_dir():
            shutil.copytree(src, tgt)
    for name in _DATA_FILES:
        src = repo / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    return dest


if __name__ == "__main__":
    staged = stage_wl_data()
    print(f"staged white-label data under {staged}")
