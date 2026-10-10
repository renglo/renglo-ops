"""Resolve ``placement.hub`` and peer extension names against ``packages:`` slots."""

from __future__ import annotations

from typing import Any


def package_name_aliases(packages: dict[str, Any]) -> dict[str, str]:
    """Map catalog handle, python dist, and npm name -> catalog handle."""
    by_name: dict[str, str] = {}
    for handle, spec in packages.items():
        key = str(handle)
        by_name[key] = key
        python_name = ""
        npm_name = ""
        if isinstance(spec, dict):
            python_name = str(spec.get("python") or "").strip()
            npm_name = str(spec.get("npm") or "").strip()
        else:
            python_name = str(spec or "").strip()
        if python_name:
            by_name[python_name] = key
        if npm_name:
            by_name[npm_name] = key
    return by_name


def resolve_package_handle(packages: dict[str, Any], item: str) -> str:
    """Catalog handle for a hub line or peer extension entry."""
    name = str(item).strip()
    if not name:
        return name
    return package_name_aliases(packages).get(name, name)


def placement_hub_missing_packages(
    packages: dict[str, Any],
    placement_hub: list[str],
) -> list[str]:
    """Hub entries that do not resolve to a ``packages:`` key."""
    missing: list[str] = []
    for item in placement_hub:
        handle = resolve_package_handle(packages, item)
        if handle not in packages:
            missing.append(str(item).strip())
    return missing
