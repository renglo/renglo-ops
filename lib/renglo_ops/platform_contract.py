"""Which platform-vars a placed extension's pin requires.

The pin is ``[tool.renglo] platform`` (and ``renglo.platform`` in package.json).
A pin below the version that introduced a key does not demand that key, so an
older extension keeps working on a stack that has not been redeployed.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from renglo_ops.cdk.shared.config_builder import (
    DERIVED_PLATFORM_KEYS,
    OPERATOR_PLATFORM_KEYS,
    PLATFORM_KEY_ADDITIONS,
)
from renglo_ops.model.placement import resolve_package_handle
from renglo_ops.model.tenant import Tenant

_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")


@dataclass(frozen=True)
class ExtensionPin:
    handle: str
    pin: str
    checked_out: bool


def version_key(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in (text or "").strip().split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            return ()
        parts.append(int(digits))
    return tuple(parts)


def pin_meets(pin: str, minimum: str) -> bool:
    left = version_key(pin)
    right = version_key(minimum)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left >= right


def keys_for_pin(pin: str) -> tuple[str, ...]:
    found: list[str] = []
    for minimum, keys in PLATFORM_KEY_ADDITIONS:
        if not pin_meets(pin, minimum):
            continue
        for key in keys:
            if key not in found:
                found.append(key)
    return tuple(found)


def placed_handles(tenant: Tenant) -> list[str]:
    raw: list[str] = [str(item) for item in tenant.placement_hub]
    for peer in tenant.placement_peers.values():
        if isinstance(peer, dict):
            raw.extend(str(item) for item in (peer.get("extensions") or []))
    ordered: list[str] = []
    for item in raw:
        handle = resolve_package_handle(tenant.packages, item)
        if handle and handle not in ordered:
            ordered.append(handle)
    return ordered


def extension_pins(workspace: Path, handles: list[str]) -> list[ExtensionPin]:
    pins: list[ExtensionPin] = []
    root = workspace / "extensions"
    for handle in handles:
        repo = root / handle
        if not repo.is_dir():
            pins.append(ExtensionPin(handle=handle, pin="", checked_out=False))
            continue
        pin = _highest_pin(
            [
                _pyproject_pin(repo / "package" / "pyproject.toml"),
                _package_json_pin(repo / "ui" / "package.json"),
            ]
        )
        pins.append(ExtensionPin(handle=handle, pin=pin, checked_out=True))
    return pins


def demanded_keys(pins: list[ExtensionPin]) -> dict[str, tuple[str, ...]]:
    """Config key → handles whose pin requires it."""
    found: dict[str, list[str]] = {}
    for item in pins:
        for key in keys_for_pin(item.pin):
            found.setdefault(key, []).append(item.handle)
    return {key: tuple(handles) for key, handles in found.items()}


def missing_stages(key: str, stages: dict[str, dict[str, str]]) -> list[str]:
    missing: list[str] = []
    for stage, block in stages.items():
        if not str(block.get(key) or "").strip():
            missing.append(stage)
    return missing


def env_config_values(text: str) -> dict[str, str]:
    """Assignments in a generated env_config.py. The file is not executed."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGN.match(stripped)
        if not match:
            continue
        raw = match.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1]
        values[match.group(1)] = raw
    return values


def requirement_sentence(
    key: str,
    handles: tuple[str, ...],
    stages: list[str],
    *,
    operator_value: str,
) -> str:
    who = ", ".join(handles)
    where = _stage_phrase(stages)
    if key in DERIVED_PLATFORM_KEYS:
        action = "run renglo stack deploy --stack b"
    elif key in OPERATOR_PLATFORM_KEYS and not operator_value.strip():
        action = f"set env.{key} in renglo.yaml, then run renglo stack deploy --stack b"
    else:
        action = "run renglo stack deploy --stack b"
    return f"{key} is missing from {where} ({who}); {action}"


def _stage_phrase(stages: list[str]) -> str:
    names = [f"platform-vars/{stage}" for stage in stages]
    if len(names) <= 1:
        return names[0] if names else "platform-vars"
    return " and ".join(names)


def _highest_pin(pins: list[str]) -> str:
    valid = [pin for pin in pins if version_key(pin)]
    if not valid:
        return ""
    return max(valid, key=version_key)


def _pyproject_pin(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    tool = data.get("tool") if isinstance(data, dict) else None
    renglo = tool.get("renglo") if isinstance(tool, dict) else None
    if not isinstance(renglo, dict):
        return ""
    return str(renglo.get("platform") or "").strip()


def _package_json_pin(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    renglo = data.get("renglo") if isinstance(data, dict) else None
    if not isinstance(renglo, dict):
        return ""
    return str(renglo.get("platform") or "").strip()
