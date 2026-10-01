"""BOM package slot catalog from deploy_targets.yml ``packages:``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PackageSlot",
    "catalog_allowlist",
    "load_package_catalog",
    "parse_package_catalog",
    "slot_for_repo",
]

_SLOT_LINE = re.compile(r"^(\s*)([^:#\s][^:]*)\s*:\s*(?:#.*)?$")
_FIELD_LINE = re.compile(
    r"^(\s*)(python|npm|repo)\s*:\s*(?:(?P<q>['\"])(?P<quoted>.*)(?P=q)|(?P<bare>\S+))\s*(?:#.*)?$"
)


@dataclass(frozen=True)
class PackageSlot:
    id: str
    python: str = ""
    npm: str = ""
    repo: str = ""


def load_package_catalog(root: Path) -> list[PackageSlot] | None:
    from renglo_ops.release.catalog import legacy_text

    text = legacy_text(root)
    if text is None:
        return None
    return parse_package_catalog(text)


def parse_package_catalog(text: str) -> list[PackageSlot] | None:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^packages:\s*(?:#.*)?$", line):
            start = index + 1
            break
        if re.match(r"^packages:\s*\{\s*\}\s*(?:#.*)?$", line):
            return []
    if start is None:
        return None

    slots: list[PackageSlot] = []
    current_id = ""
    fields: dict[str, str] = {}
    slot_indent: int | None = None

    def flush() -> None:
        nonlocal current_id, fields
        if current_id:
            slots.append(
                PackageSlot(
                    id=current_id,
                    python=fields.get("python", ""),
                    npm=fields.get("npm", ""),
                    repo=fields.get("repo", ""),
                )
            )
        current_id = ""
        fields = {}

    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        field = _FIELD_LINE.match(line)
        if field and current_id:
            value = field.group("quoted")
            if value is None:
                value = field.group("bare") or ""
            fields[field.group(2)] = value
            continue
        slot = _SLOT_LINE.match(line)
        if not slot:
            continue
        this_indent = len(slot.group(1))
        if slot_indent is None:
            slot_indent = this_indent
        if this_indent != slot_indent:
            continue
        flush()
        current_id = slot.group(2).strip().strip("'\"")
    flush()
    return slots


def catalog_allowlist(catalog: list[PackageSlot]) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {"python": set(), "npm": set(), "repos": set()}
    for slot in catalog:
        if slot.python:
            allowed["python"].add(slot.python)
        if slot.npm:
            allowed["npm"].add(slot.npm)
        if slot.repo:
            allowed["repos"].add(slot.repo)
    return allowed


def slot_for_repo(
    catalog: list[PackageSlot],
    repo_id: str,
    *,
    npm_name: str = "",
    python_names: list[str] | None = None,
) -> PackageSlot | None:
    for slot in catalog:
        if slot.id == repo_id:
            return slot
    if npm_name:
        for slot in catalog:
            if slot.npm == npm_name:
                return slot
    names = set(python_names or [])
    if names:
        for slot in catalog:
            if slot.python and slot.python in names:
                return slot
    return None
