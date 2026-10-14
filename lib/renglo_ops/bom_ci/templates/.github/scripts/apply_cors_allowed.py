#!/usr/bin/env python3
"""Merge cors/allowed.yml into the Lambda env JSON for the API deploy.

The stage console (FE_BASE_URL) is already in that JSON. This adds
CORS_ALLOWED_ORIGINS. A "*" entry allows every browser origin.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def normalize_cors_origin(item: Any, path: Path) -> str:
    text = str(item).strip()
    if text == "*":
        return "*"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f'{path}: origin must be an http(s) URL or "*", got {text!r}')
    if parsed.path not in {"", "/"} or parsed.query or parsed.params or parsed.fragment:
        raise ValueError(f"{path}: origin must be scheme://host with no path, got {text!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def load_cors_allowed(path: Path) -> str:
    """Return CORS_ALLOWED_ORIGINS as a JSON list. '*' allows every origin."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required to read a CORS allow-list") from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if data is None:
        items: list[Any] = []
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        raw = data.get("origins", [])
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, list):
            items = raw
        else:
            raise ValueError(f'{path}: origins must be a list or "*"')
    else:
        raise ValueError(f"{path}: expected a list or a mapping with origins")

    origins: list[str] = []
    for item in items:
        origin = normalize_cors_origin(item, path)
        if origin == "*":
            return json.dumps(["*"])
        if origin not in origins:
            origins.append(origin)
    return json.dumps(origins)


def apply_cors_allowed(allowed: Path, merge: Path) -> str:
    encoded = load_cors_allowed(allowed)
    payload = json.loads(merge.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{merge}: Lambda env merge must be a JSON object")
    payload["CORS_ALLOWED_ORIGINS"] = encoded
    merge.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return encoded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add CORS_ALLOWED_ORIGINS to a Lambda env merge")
    parser.add_argument("--allowed", required=True, help="cors/allowed.yml")
    parser.add_argument("--merge", required=True, help="lambda_env_merge.json")
    args = parser.parse_args(argv)
    allowed = Path(args.allowed)
    merge = Path(args.merge)
    if not allowed.is_file():
        print(f"cors allow-list not found: {allowed}", file=sys.stderr)
        return 1
    if not merge.is_file():
        print(f"lambda env merge not found: {merge}", file=sys.stderr)
        return 1
    try:
        encoded = apply_cors_allowed(allowed, merge)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"cors allow-list failed: {exc}", file=sys.stderr)
        return 1
    print(f"Set CORS_ALLOWED_ORIGINS from {allowed}: {encoded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
