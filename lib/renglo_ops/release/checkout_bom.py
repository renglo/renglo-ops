#!/usr/bin/env python3
"""Clone every repo in a BOM JSON for one pipeline.

Usage:
    python scripts/checkout_bom.py bom/v0.0.7.json --pipeline backend
    python scripts/checkout_bom.py handlers_bom/v0.0.3.json --pipeline handlers
    python scripts/checkout_bom.py peers_bom/lab/v0.1.8.json --pipeline peers

Auth (GitHub Actions): set GITHUB_TOKEN or GH_TOKEN. Origin URLs stay token-free;
the token is passed only as an http.extraheader on fetch.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from install_backend_packages import find_packages
from bom_manifest import (
    VALID_PIPELINES,
    RepoSpec,
    checkout_specs,
    ci_outputs,
    extension_handles,
    handlers_python_packages,
    infer_source,
    load_bom,
    npm_extension_handles,
    package_pins,
    pipeline_has_work,
    python_handlers_handles,
    resolve_specs,
    scan_vite_extensions,
    write_github_output,
)


def _git(args: list[str], *, extraheader: str | None = None, cwd: Path | None = None) -> None:
    cmd = ["git"]
    if extraheader:
        cmd += ["-c", f"http.extraheader={extraheader}"]
    cmd += args
    visible = ["git", *args]
    print(f"+ {' '.join(visible)}")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None, env=env)


def _https_url(repo_key: str) -> str:
    return f"https://github.com/{repo_key}.git"


def _extraheader(token: str | None) -> str | None:
    if not token:
        return None
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return f"AUTHORIZATION: basic {basic}"


def checkout_spec(dest_root: Path, spec: RepoSpec, token: str | None) -> None:
    dest = dest_root / spec.path
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"Checkout path is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    origin = _https_url(spec.key)
    header = _extraheader(token)
    _git(["init", "--quiet"], cwd=dest)
    _git(["remote", "add", "origin", origin], cwd=dest)
    try:
        _git(["fetch", "--depth", "1", "origin", spec.ref], extraheader=header, cwd=dest)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to fetch {spec.key} at {spec.ref}. "
            "If this is a private repo, ensure GITHUB_TOKEN can read it."
        ) from exc
    _git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=dest)
    print(f"  checked out {spec.key} -> {spec.path}")


def _ensure_tree_placeholders(dest_root: Path) -> None:
    """Docker COPY fails on missing directories; keep empty trees in the context."""
    for rel in ("dev", "extensions", "wheels"):
        folder = dest_root / rel
        folder.mkdir(parents=True, exist_ok=True)
        keep = folder / ".keep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Checkout BOM JSON repos for a pipeline")
    parser.add_argument("bom_file", help="Path to a BOM JSON file")
    parser.add_argument("--pipeline", required=True, choices=VALID_PIPELINES)
    parser.add_argument(
        "--dest",
        default=".",
        help="Workspace root to clone into (default: current directory)",
    )
    args = parser.parse_args()

    bom_file = Path(args.bom_file)
    dest_root = Path(args.dest).resolve()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    token = token.strip() or None

    try:
        data = load_bom(bom_file)
        all_specs = resolve_specs(bom_file, data)
        specs = checkout_specs(all_specs, args.pipeline, data)
        source = infer_source(bom_file, data)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not pipeline_has_work(args.pipeline, specs, data, source=source):
        print(f"No repos or package pins for pipeline {args.pipeline!r} in {bom_file}", file=sys.stderr)
        return 1

    _ensure_tree_placeholders(dest_root)

    print(f"Checking out {len(specs)} repo(s) for {args.pipeline} into {dest_root}")
    for spec in specs:
        try:
            checkout_spec(dest_root, spec, token)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if args.pipeline == "backend" and not find_packages(dest_root) and not package_pins(data, "python"):
        print("No Python packages found under dev/ or extensions/*/package after checkout.", file=sys.stderr)
        return 1
    if args.pipeline == "console":
        npm = package_pins(data, "npm")
        has_console = (dest_root / "console").is_dir() or "@renglo/console" in npm
        if not has_console:
            print("Console checkout missing: expected a console/ directory or @renglo/console pin.", file=sys.stderr)
            return 1
    vite_extensions = scan_vite_extensions(dest_root, specs)
    vite_names = list(dict.fromkeys([*vite_extensions, *npm_extension_handles(data)]))
    if args.pipeline == "console" and not vite_names:
        print("Warning: no extensions/*/ui directories or npm UI pins after checkout.", file=sys.stderr)
    all_pins, handler_pkgs = handlers_python_packages(data)
    pin_handles = python_handlers_handles(data)
    outputs = {
        **ci_outputs(data, specs, dest_root=dest_root),
        "vite_extensions": ",".join(vite_names),
        "handlers_packages": ",".join(handler_pkgs),
        "handlers_wheelhouse_packages": ",".join(all_pins),
        "extension_handles": ",".join(
            list(dict.fromkeys([*pin_handles, *extension_handles(specs)]))
            if args.pipeline in ("handlers", "peers")
            else extension_handles(specs)
        ),
    }
    print(f"  vite_extensions={outputs['vite_extensions'] or '(none)'}")
    if outputs.get("vite_wl_package"):
        print(f"  vite_wl_package={outputs['vite_wl_package']}")
    if outputs["python_specs"]:
        print(f"  python_specs={outputs['python_specs']}")
    if outputs["npm_specs"]:
        print(f"  npm_specs={outputs['npm_specs']}")
    if outputs["npm_local_specs"]:
        print(f"  npm_local_specs={outputs['npm_local_specs']}")
    if outputs["console_host_spec"]:
        print(f"  console_host_spec={outputs['console_host_spec']}")
    if args.pipeline in ("handlers", "peers"):
        print(f"  handlers_packages={outputs['handlers_packages'] or '(none)'}")
        print(
            f"  handlers_wheelhouse_packages="
            f"{outputs['handlers_wheelhouse_packages'] or '(none)'}"
        )
    write_github_output(outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
