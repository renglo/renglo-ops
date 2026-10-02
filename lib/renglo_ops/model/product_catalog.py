"""Product package list shipped by the white-label pack.

``<tenant>-wl/product.yaml`` names the packages a product installs.
``renglo catalog sync`` copies those handles into ``renglo.yaml``.
Placement stays in ``renglo.yaml``. A missing catalog or white-label
checkout is ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.local import workspace_root
from renglo_ops.model.tenant import Tenant

CATALOG_NAME = "product.yaml"


@dataclass
class CatalogSync:
    catalog: Path | None
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    wrote: bool = False
    dry_run: bool = False

    def format(self) -> str:
        if self.catalog is None:
            return "catalog: (not found)\nrenglo.yaml unchanged\n"
        lines = [f"catalog: {self.catalog}"]
        lines.append(f"added: {', '.join(self.added) if self.added else '(none)'}")
        lines.append(f"updated: {', '.join(self.updated) if self.updated else '(none)'}")
        if self.unplaced:
            lines.append(
                "unplaced: "
                + ", ".join(self.unplaced)
                + " (add to placement.hub or a peer before deploy)"
            )
        else:
            lines.append("unplaced: (none)")
        if self.dry_run:
            lines.append("renglo.yaml unchanged (dry-run)")
        elif self.wrote:
            lines.append("wrote renglo.yaml")
        else:
            lines.append("renglo.yaml unchanged")
        return "\n".join(lines) + "\n"


def _wl_names(tenant: Tenant | None) -> list[str]:
    if tenant is None:
        return []
    names: list[str] = []
    for handle, spec in tenant.packages.items():
        python_name = ""
        npm_name = ""
        if isinstance(spec, dict):
            python_name = str(spec.get("python") or "").strip()
            npm_name = str(spec.get("npm") or "").strip()
        handle_s = str(handle)
        if (
            handle_s == "wl"
            or handle_s.endswith("-wl")
            or python_name.endswith("-wl")
            or npm_name.endswith("/wl")
        ):
            for name in (python_name, handle_s):
                if name and name not in names and not name.startswith("@"):
                    names.append(name)
    return names


def _search_roots(start: Path, tenant: Tenant | None) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    add(workspace_root(start))
    add(start)
    if tenant is not None and tenant.path is not None:
        bom = tenant.path.parent
        add(bom)
        for parent in list(bom.parents)[:4]:
            add(parent)
    return roots


def _checkout_candidates(roots: list[Path], names: list[str]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for name in names:
            found.append(root / "dev" / name / CATALOG_NAME)
            found.append(root / name / CATALOG_NAME)
        folders: list[Path] = []
        try:
            folders.extend(path for path in root.glob("*-wl") if path.is_dir())
            dev = root / "dev"
            if dev.is_dir():
                folders.extend(path for path in dev.glob("*-wl") if path.is_dir())
        except OSError:
            continue
        for folder in folders:
            found.append(folder / CATALOG_NAME)
    return found


def _installed_catalog() -> Path | None:
    try:
        import wl
    except Exception:
        return None
    try:
        here = Path(wl.__file__).resolve().parent
    except Exception:
        return None
    for candidate in (here / CATALOG_NAME, here.parent.parent / CATALOG_NAME):
        if candidate.is_file():
            return candidate
    return None


def find_product_catalog(start: Path, tenant: Tenant | None = None) -> Path | None:
    """Locate product.yaml. Missing white-label pack or file returns None."""
    override = os.environ.get("RENGLO_PRODUCT_CATALOG", "").strip()
    if override:
        path = Path(override).expanduser()
        return path.resolve() if path.is_file() else None
    try:
        roots = _search_roots(start, tenant)
        names = _wl_names(tenant)
        for candidate in _checkout_candidates(roots, names):
            if candidate.is_file():
                return candidate.resolve()
    except Exception:
        pass
    return _installed_catalog()


def _normalize_spec(handle: str, spec: Any) -> dict[str, str]:
    if not isinstance(spec, dict):
        raise RengloOpsError(f"packages.{handle} must be a mapping")
    out: dict[str, str] = {}
    for key in ("python", "npm"):
        value = str(spec.get(key) or "").strip()
        if value:
            out[key] = value
    if not out:
        raise RengloOpsError(f"packages.{handle} needs python or npm")
    return out


def load_product_catalog(path: Path) -> dict[str, dict[str, str]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RengloOpsError(f"could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RengloOpsError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RengloOpsError(f"{path} must be a mapping")
    packages = data.get("packages") or {}
    if not isinstance(packages, dict):
        raise RengloOpsError(f"{path} packages must be a mapping")
    return {str(handle): _normalize_spec(str(handle), spec) for handle, spec in packages.items()}


def merge_packages(
    existing: dict[str, Any],
    catalog: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Add or refresh catalog handles. Packages absent from the catalog stay."""
    merged: dict[str, Any] = dict(existing)
    added: list[str] = []
    updated: list[str] = []
    for handle, spec in catalog.items():
        current = existing.get(handle)
        if not isinstance(current, dict):
            merged[handle] = dict(spec)
            added.append(handle)
            continue
        current_norm = {
            key: str(current.get(key) or "").strip()
            for key in ("python", "npm")
            if str(current.get(key) or "").strip()
        }
        if current_norm == spec:
            continue
        row = dict(current)
        row.update(spec)
        merged[handle] = row
        updated.append(handle)
    return merged, added, updated


def unplaced_handles(
    packages: dict[str, Any],
    placement_hub: list[Any],
    placement_peers: dict[str, Any],
    catalog_handles: list[str],
) -> list[str]:
    by_name: dict[str, str] = {}
    for handle, spec in packages.items():
        by_name[str(handle)] = str(handle)
        if isinstance(spec, dict):
            python_name = str(spec.get("python") or "").strip()
            npm_name = str(spec.get("npm") or "").strip()
            if python_name:
                by_name[python_name] = str(handle)
            if npm_name:
                by_name[npm_name] = str(handle)
    placed: set[str] = set()
    for item in placement_hub:
        key = str(item).strip()
        if key:
            placed.add(by_name.get(key, key))
    for peer in placement_peers.values():
        extensions = peer.get("extensions") if isinstance(peer, dict) else []
        for handle in extensions or []:
            placed.add(str(handle))
    return [handle for handle in catalog_handles if handle not in placed]


def _yaml_scalar(value: str) -> str:
    if value.startswith("@") or any(char in value for char in ":#{}[]&*!|>%@`"):
        return "'" + value.replace("'", "''") + "'"
    return value


def render_packages_block(packages: dict[str, Any]) -> str:
    if not packages:
        return "packages: {}\n"
    lines = ["packages:"]
    for handle, spec in packages.items():
        lines.append(f"  {handle}:")
        if not isinstance(spec, dict):
            lines.append(f"    {_yaml_scalar(str(spec))}")
            continue
        for key in ("python", "npm"):
            value = str(spec.get(key) or "").strip()
            if value:
                lines.append(f"    {key}: {_yaml_scalar(value)}")
        for key, value in spec.items():
            if key in {"python", "npm"}:
                continue
            lines.append(f"    {key}: {_yaml_scalar(str(value))}")
    return "\n".join(lines) + "\n"


def replace_packages_block(text: str, packages: dict[str, Any]) -> str:
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if line.startswith("packages:")), None)
    rendered = render_packages_block(packages)
    if start is None:
        body = text if text.endswith("\n") or not text else text + "\n"
        return body + rendered
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = index
            break
    return "".join(lines[:start]) + rendered + "".join(lines[end:])


def sync_tenant_packages(tenant: Tenant, *, start: Path, dry_run: bool = False) -> CatalogSync:
    """Copy catalog packages into renglo.yaml. Missing catalog leaves the file alone."""
    catalog_path = find_product_catalog(start, tenant)
    if catalog_path is None:
        return CatalogSync(catalog=None, dry_run=dry_run)
    if tenant.path is None:
        raise RengloOpsError("renglo.yaml path is unknown")
    catalog = load_product_catalog(catalog_path)
    text = tenant.path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise RengloOpsError(f"{tenant.path} must be a mapping")
    existing = raw.get("packages") or {}
    if not isinstance(existing, dict):
        raise RengloOpsError("packages must be a mapping")
    merged, added, updated = merge_packages(existing, catalog)
    placement = raw.get("placement") if isinstance(raw.get("placement"), dict) else {}
    hub = placement.get("hub") or []
    peers = placement.get("peers") or {}
    if not isinstance(hub, list):
        hub = []
    if not isinstance(peers, dict):
        peers = {}
    unplaced = unplaced_handles(merged, hub, peers, list(catalog))
    wrote = False
    if (added or updated) and not dry_run:
        tenant.path.write_text(replace_packages_block(text, merged), encoding="utf-8")
        wrote = True
    return CatalogSync(
        catalog=catalog_path,
        added=added,
        updated=updated,
        unplaced=unplaced,
        wrote=wrote,
        dry_run=dry_run,
    )


def catalog_doctor(start: Path, tenant: Tenant) -> tuple[str, str]:
    """(ok|skip|warn, detail). Never raises."""
    try:
        path = find_product_catalog(start, tenant)
    except Exception as exc:
        return "skip", f"product catalog skipped ({exc})"
    if path is None:
        return "skip", "product catalog not found (optional)"
    try:
        catalog = load_product_catalog(path)
    except Exception as exc:
        return "warn", str(exc)
    missing = [handle for handle in catalog if handle not in tenant.packages]
    if missing:
        return "warn", "not in renglo.yaml (renglo catalog sync): " + ", ".join(missing)
    return "ok", str(path)
