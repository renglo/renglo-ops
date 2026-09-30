#!/usr/bin/env python3
"""Configure pip or npm for one or more CodeArtifact registries (vendor mosaic).

First entry becomes the default index/registry. Remaining entries are added as
pip extra-index-url values, or npm scoped registries from npm_scopes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse


def _run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture,
    )


def _aws_codeartifact(args: list[str], region: str) -> str:
    cmd = ["aws", "codeartifact", *args, "--region", region, "--output", "text"]
    result = _run(cmd, capture=True)
    return (result.stdout or "").strip()


def _login(tool: str, entry: dict[str, Any]) -> None:
    _run(
        [
            "aws",
            "codeartifact",
            "login",
            "--tool",
            tool,
            "--domain",
            str(entry["domain"]),
            "--domain-owner",
            str(entry["domain_owner"]),
            "--repository",
            str(entry["python_repository"] if tool == "pip" else entry["npm_repository"]),
            "--region",
            str(entry["region"]),
        ]
    )


def _repository_endpoint(entry: dict[str, Any], *, format_name: str) -> str:
    repo = entry["python_repository"] if format_name == "pypi" else entry["npm_repository"]
    return _aws_codeartifact(
        [
            "get-repository-endpoint",
            "--domain",
            str(entry["domain"]),
            "--domain-owner",
            str(entry["domain_owner"]),
            "--repository",
            str(repo),
            "--format",
            format_name,
            "--query",
            "repositoryEndpoint",
        ],
        str(entry["region"]),
    )


def _auth_token(entry: dict[str, Any]) -> str:
    return _aws_codeartifact(
        [
            "get-authorization-token",
            "--domain",
            str(entry["domain"]),
            "--domain-owner",
            str(entry["domain_owner"]),
            "--query",
            "authorizationToken",
        ],
        str(entry["region"]),
    )


def pip_extra_index_url(endpoint: str, token: str) -> str:
    """Build an authenticated PyPI simple index URL for pip --extra-index-url."""
    parsed = urlparse(endpoint.rstrip("/") + "/simple/")
    netloc = f"aws:{quote(token, safe='')}@{parsed.netloc}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def configure_pip(registries: list[dict[str, Any]]) -> None:
    if not registries:
        raise SystemExit("configure_codeartifact: registries list is empty")
    _login("pip", registries[0])
    extras: list[str] = []
    for entry in registries[1:]:
        endpoint = _repository_endpoint(entry, format_name="pypi")
        token = _auth_token(entry)
        extras.append(pip_extra_index_url(endpoint, token))
    if extras:
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "config",
                "set",
                "global.extra-index-url",
                " ".join(extras),
            ]
        )
        os.environ["PIP_EXTRA_INDEX_URL"] = " ".join(extras)


def configure_npm(registries: list[dict[str, Any]]) -> None:
    if not registries:
        raise SystemExit("configure_codeartifact: registries list is empty")
    _login("npm", registries[0])
    for entry in registries:
        scopes = entry.get("npm_scopes") or []
        if not scopes:
            continue
        endpoint = _repository_endpoint(entry, format_name="npm").rstrip("/") + "/"
        token = _auth_token(entry)
        parsed = urlparse(endpoint)
        host_path = f"//{parsed.netloc}{parsed.path}"
        for scope in scopes:
            scope_name = str(scope).strip()
            if not scope_name:
                continue
            if not scope_name.startswith("@"):
                scope_name = f"@{scope_name}"
            _run(["npm", "config", "set", f"{scope_name}:registry", endpoint])
            _run(["npm", "config", "set", f"{host_path}:_authToken", token])
            # npm 11+ (Node 24) removed always-auth; a registry _authToken is enough.


def _load_registries(raw: str | None, path: str | None) -> list[dict[str, Any]]:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    elif raw:
        text = raw
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise SystemExit("configure_codeartifact: pass --registries-json, --registries-file, or stdin")
    data = json.loads(text)
    if not isinstance(data, list) or not data:
        raise SystemExit("configure_codeartifact: expected a non-empty JSON array of registries")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure pip/npm for CodeArtifact registries")
    parser.add_argument("--tool", choices=("pip", "npm"), required=True)
    parser.add_argument("--registries-json", default="", help="JSON array of registry objects")
    parser.add_argument("--registries-file", default="", help="Path to JSON array of registry objects")
    args = parser.parse_args()

    registries = _load_registries(
        args.registries_json or None,
        args.registries_file or None,
    )
    if args.tool == "pip":
        configure_pip(registries)
    else:
        configure_npm(registries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
