#!/usr/bin/env python3
"""Write ``constraints/console/<version>.package-lock.json`` for a console BOM.

Starts from the console host's package.json and package-lock.json, adds the
BOM npm pins at those exact versions, and asks npm to update the lock without
installing. The host tree stays at the versions already in its lock. Packages
added by the BOM, and everything they depend on, are frozen at this run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from bom_manifest import load_bom, package_pins
    from configure_codeartifact import render_scoped_npmrc
except ImportError:
    from renglo_ops.release.bom_manifest import load_bom, package_pins
    from renglo_ops.release.configure_codeartifact import render_scoped_npmrc


def _workspace_root(start: Path) -> Path:
    for candidate in (start, *start.resolve().parents):
        if (candidate / "console" / "package.json").is_file() and (candidate / "extensions").is_dir():
            return candidate
    raise SystemExit(
        f"No workspace with console/package.json above {start}. Pass --workspace."
    )


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _block_lines(lines: list[str], start: int) -> list[tuple[int, str]]:
    """Lines belonging to a top-level key, with their indentation."""
    collected: list[tuple[int, str]] = []
    index = start
    while index < len(lines):
        raw = lines[index]
        if raw.strip() and not raw.startswith((" ", "\t", "#", "-")):
            break
        indent = len(raw) - len(raw.lstrip(" "))
        collected.append((indent, raw.strip()))
        index += 1
    return collected


def registries_from_renglo_yaml(text: str) -> list[dict[str, object]]:
    """CodeArtifact npm targets from a BOM ``renglo.yaml``.

    A registry without ``account`` uses ``accounts.staging``. The public npm
    registry is not one of these targets.
    """
    lines = text.splitlines()
    staging_account = ""
    staging_region = "us-east-1"
    registries: list[dict[str, object]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("accounts:"):
            account_name = ""
            for indent, body in _block_lines(lines, index + 1):
                if indent == 2 and body.endswith(":"):
                    account_name = body[:-1].strip()
                    continue
                if account_name != "staging" or indent != 4 or ":" not in body:
                    continue
                key, value = body.split(":", 1)
                if key.strip() == "id":
                    staging_account = _unquote(value)
                elif key.strip() == "region" and _unquote(value):
                    staging_region = _unquote(value)
            index += 1
            continue
        if line.startswith("registries:"):
            current: dict[str, object] | None = None
            in_scopes = False
            for _indent, body in _block_lines(lines, index + 1):
                if not body or body.startswith("#"):
                    continue
                if body.startswith("- "):
                    rest = body[2:].strip()
                    if in_scopes and current is not None and ":" not in rest:
                        scopes = current["npm_scopes"]
                        assert isinstance(scopes, list)
                        scopes.append(_unquote(rest))
                        continue
                    in_scopes = False
                    current = {
                        "domain": "",
                        "domain_owner": "",
                        "npm_repository": "npm-store",
                        "region": staging_region,
                        "npm_scopes": [],
                    }
                    registries.append(current)
                    body = rest
                if current is None or ":" not in body:
                    continue
                key, value = body.split(":", 1)
                key = key.strip()
                value = _unquote(value)
                if key == "domain":
                    current["domain"] = value
                elif key == "npm":
                    current["npm_repository"] = value or "npm-store"
                elif key == "account":
                    current["domain_owner"] = value
                elif key == "region":
                    current["region"] = value or staging_region
                elif key == "scopes":
                    in_scopes = True
            index += 1
            continue
        index += 1
    resolved: list[dict[str, object]] = []
    for entry in registries:
        owner = str(entry.get("domain_owner") or "").strip() or staging_account
        if not entry.get("domain") or not owner:
            continue
        entry["domain_owner"] = owner
        entry["region"] = str(entry.get("region") or staging_region)
        resolved.append(entry)
    return resolved


def _npmrc_for_bom(bom_file: Path) -> tuple[str, list[str]] | None:
    manifest = bom_file.parent.parent / "renglo.yaml"
    if not manifest.is_file():
        return None
    registries = registries_from_renglo_yaml(manifest.read_text(encoding="utf-8"))
    if not registries:
        return None
    try:
        return render_scoped_npmrc(registries)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise SystemExit(
            "Could not log npm into CodeArtifact for the console lock. "
            "AWS credentials are required, and the public registry does not host @renglo or @skbrk.\n"
            f"{detail}"
        ) from exc


def _scrub(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def console_lock_path(bom_file: Path) -> Path:
    """``console_bom/v0.1.17.json`` → ``constraints/console/v0.1.17.package-lock.json``."""
    return bom_file.parent.parent / "constraints" / "console" / f"{bom_file.stem}.package-lock.json"


def compile_console_lock(bom_file: Path, *, workspace: Path, npmrc: Path | None = None) -> Path:
    data = load_bom(bom_file)
    npm_pins = package_pins(data, "npm")
    if not npm_pins:
        raise SystemExit(f"{bom_file} has no npm pins.")
    host_path = workspace / "console" / "package.json"
    host_lock = workspace / "console" / "package-lock.json"
    if not host_path.is_file():
        raise SystemExit(f"missing {host_path}")
    if not host_lock.is_file():
        raise SystemExit(f"missing {host_lock}; the console host tree is not locked")
    host = json.loads(host_path.read_text(encoding="utf-8"))
    host_name = str(host.get("name") or "").strip()
    pinned_host = str(npm_pins.get(host_name) or "").strip()
    if pinned_host and str(host.get("version") or "").strip() != pinned_host:
        raise SystemExit(
            f"{host_name} in the workspace is {host.get('version')}, and the BOM pins {pinned_host}."
        )
    specs = [
        f"{name}@{str(version).strip()}"
        for name, version in npm_pins.items()
        if name != host_name and str(version).strip()
    ]
    dest = console_lock_path(bom_file)
    if not specs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(host_lock, dest)
        return dest
    with tempfile.TemporaryDirectory(prefix="console-lock-") as raw:
        work = Path(raw)
        (work / "package.json").write_text(json.dumps(host, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(host_lock, work / "package-lock.json")
        secrets: list[str] = []
        if npmrc is not None:
            shutil.copyfile(npmrc, work / ".npmrc")
        else:
            generated = _npmrc_for_bom(bom_file)
            if generated is not None:
                text, secrets = generated
                (work / ".npmrc").write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [
                "npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--save-exact",
                *specs,
            ],
            cwd=work,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = _scrub(completed.stderr or completed.stdout or "", secrets)
            sys.stderr.write(detail)
            raise SystemExit(completed.returncode or 1)
        produced = work / "package-lock.json"
        if not produced.is_file():
            raise SystemExit("npm did not write package-lock.json")
        if any(secret and secret in produced.read_text(encoding="utf-8") for secret in secrets):
            raise SystemExit("console lock contained a registry token and was not written")
        written = json.loads((work / "package.json").read_text(encoding="utf-8"))
        locked = json.loads(produced.read_text(encoding="utf-8"))
        locked_root = (locked.get("packages") or {}).get("") or {}
        locked_deps = locked_root.get("dependencies")
        if locked_deps is None:
            locked_deps = locked.get("dependencies") or {}
        written_deps = written.get("dependencies") or {}
        for name, version in npm_pins.items():
            if name == host_name:
                continue
            exact = str(version).strip()
            if written_deps.get(name) != exact or locked_deps.get(name) != exact:
                raise SystemExit(
                    f"{name} resolved to package {written_deps.get(name)!r} "
                    f"and lock {locked_deps.get(name)!r}; expected {exact}."
                )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Write an npm lock for a console BOM")
    parser.add_argument("bom_file", help="Path to console_bom/vX.Y.Z.json")
    parser.add_argument("--workspace", default="", help="Checkout that contains console/")
    parser.add_argument("--output", default="", help="Destination lock file")
    parser.add_argument(
        "--npmrc",
        default="",
        help="npm config to use while resolving. Not copied into the lock.",
    )
    args = parser.parse_args()
    bom_file = Path(args.bom_file)
    workspace = Path(args.workspace) if args.workspace else _workspace_root(bom_file)
    npmrc = Path(args.npmrc) if args.npmrc else None
    try:
        dest = compile_console_lock(bom_file, workspace=workspace, npmrc=npmrc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.output:
        target = Path(args.output)
        if target.resolve() != dest.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, target)
            dest = target
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
