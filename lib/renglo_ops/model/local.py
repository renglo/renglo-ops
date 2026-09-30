"""Laptop-only session file: .renglo/local.yaml at the product workspace root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from renglo_ops.model.errors import RengloOpsError


@dataclass
class LocalSession:
    tenant: str
    profile: str = ""
    region: str = ""
    registry: str = ""
    path: Path | None = None


def load_local(path: Path) -> LocalSession:
    path = path.expanduser().resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RengloOpsError(f"{path} must be a mapping")
    tenant = str(data.get("tenant") or "").strip()
    if not tenant:
        raise RengloOpsError(f"{path} requires tenant")
    return LocalSession(
        tenant=tenant,
        profile=str(data.get("profile") or "").strip(),
        region=str(data.get("region") or "").strip(),
        registry=str(data.get("registry") or "").strip(),
        path=path,
    )


def find_local(start: Path) -> Path | None:
    here = start.expanduser().resolve()
    for candidate in [here, *here.parents]:
        path = candidate / ".renglo" / "local.yaml"
        if path.is_file():
            return path
    return None


def workspace_root(start: Path) -> Path:
    local = find_local(start)
    if local is not None:
        return local.parent.parent
    for candidate in [start.expanduser().resolve(), *start.expanduser().resolve().parents]:
        if (candidate / "console").is_dir() and (candidate / "dev").is_dir():
            return candidate
        if (candidate / "renglo.yaml").is_file():
            return candidate
    return start.expanduser().resolve()


def is_tool_checkout(path: Path) -> bool:
    return (path / "lib" / "renglo_ops").is_dir() and (path / "renglo").is_dir()


def refuse_tool_output(dest: Path) -> None:
    """Generated files stay in the product workspace, never in this package."""
    for candidate in [dest.expanduser().resolve(), *dest.expanduser().resolve().parents]:
        if is_tool_checkout(candidate):
            raise RengloOpsError(
                f"refusing to write {dest} inside the renglo-ops checkout"
            )
        if "site-packages" in candidate.parts:
            raise RengloOpsError(f"refusing to write {dest} inside site-packages")
