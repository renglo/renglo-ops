#!/usr/bin/env python3
"""Load a BOM JSON and resolve checkout / install plans.

v2 JSON may pin package versions under ``python`` / ``npm`` and still list
git SHAs under ``repos``. A package pin wins: that repo is not cloned for
the matching pipeline.

  python.renglo-lib / renglo-api  -> pip install from the publisher registry
  remaining repos                 -> git clone (extensions, console, …)

Layout and pipeline membership come from conventions (overrideable per repo):

  renglo/renglo-api            -> dev/renglo-api            (backend)
  renglo/renglo-lib            -> dev/renglo-lib            (backend; handlers)
  renglo/console               -> console                   (console)
  org/wl | org/<tenant>-wl     -> <repo shortname>          (console)
  any other org/name           -> extensions/<name>

Unknown repos in bom/*.json default to backend+console.
Unknown repos in handlers_bom/*.json default to handlers (overflow node).
Unknown repos in peers_bom/**/*.json default to peers.

Optional per-repo JSON fields:
  path        Checkout directory relative to the workspace root
  pipelines   List of backend | console | handlers | peers
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_PIPELINES = ("backend", "console", "handlers", "peers")
DEFAULT_BRANCH = "main"

CORE_REPOS: dict[str, tuple[str, frozenset[str]]] = {
    "renglo/renglo-api": ("dev/renglo-api", frozenset({"backend"})),
    # Backend may still clone when unpinned; handlers always consume the python pin.
    "renglo/renglo-lib": ("dev/renglo-lib", frozenset({"backend"})),
    "renglo/console": ("console", frozenset({"console"})),
}

# Console host app. A pin unpacks this tarball to console/; it is not npm-installed
# into that same tree.
CONSOLE_NPM_PACKAGE = "@renglo/console"
WL_NPM_NAME = re.compile(r"^@[^/]+/wl$")

# Core Python dist names → git repo keys. A pin here skips the clone.
CORE_PYTHON_TO_REPO: dict[str, str] = {
    "renglo-lib": "renglo/renglo-lib",
    "renglo-api": "renglo/renglo-api",
}


@dataclass(frozen=True)
class RepoSpec:
    key: str
    ref: str
    path: str
    pipelines: frozenset[str]
    url: str

    @property
    def shortname(self) -> str:
        return Path(self.path).name

    @property
    def is_extension(self) -> bool:
        return self.path.startswith("extensions/")


def is_wl_repo_name(name: str) -> bool:
    """Git repo shortname for a tenant white-label pack."""
    return name == "wl" or name.endswith("-wl")


def pick_ref(repo_entry: dict[str, Any] | None) -> str:
    entry = repo_entry or {}
    commit = str(entry.get("commit", "")).strip()
    if commit:
        return commit
    branch = str(entry.get("branch", "")).strip()
    return branch if branch else DEFAULT_BRANCH


def default_path(repo_key: str) -> str:
    if repo_key in CORE_REPOS:
        return CORE_REPOS[repo_key][0]
    name = repo_key.split("/", 1)[-1].strip()
    if not name:
        raise ValueError(f"Invalid repo key: {repo_key!r}")
    if is_wl_repo_name(name):
        return name
    return f"extensions/{name}"


def default_pipelines(repo_key: str, source: str) -> frozenset[str]:
    if repo_key in CORE_REPOS:
        return CORE_REPOS[repo_key][1]
    name = repo_key.split("/", 1)[-1].strip()
    if is_wl_repo_name(name):
        return frozenset({"console"})
    if source == "peers":
        return frozenset({"peers"})
    if source == "handlers":
        return frozenset({"handlers"})
    return frozenset({"backend", "console"})


def infer_source(bom_file: Path, data: dict[str, Any]) -> str:
    parts = {p.lower() for p in bom_file.resolve().parts}
    if "console_bom" in parts:
        return "console"
    if "peers_bom" in parts:
        return "peers"
    if "handlers_bom" in parts or str(data.get("deploy_stage", "")).strip():
        return "handlers"
    return "bom"


def parse_pipelines(raw: Any, repo_key: str) -> frozenset[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{repo_key}: pipelines must be a non-empty list")
    values = {str(x).strip().lower() for x in raw}
    unknown = values - set(VALID_PIPELINES)
    if unknown:
        raise ValueError(f"{repo_key}: unknown pipelines {sorted(unknown)}")
    if not all(str(x).strip() for x in raw):
        raise ValueError(f"{repo_key}: pipelines contains an empty value")
    return frozenset(values)


def package_pins(data: dict[str, Any], section: str) -> dict[str, str]:
    """Return ``{dist_name: version}`` for ``python`` or ``npm``. Empty if omitted."""
    raw = data.get(section)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{section} must be an object of name → version")
    pins: dict[str, str] = {}
    for name, version in raw.items():
        key = str(name).strip()
        ver = str(version).strip()
        if not key or not ver:
            raise ValueError(f"{section}: empty name or version ({name!r}: {version!r})")
        pins[key] = ver
    return pins


def python_install_specs(data: dict[str, Any]) -> list[str]:
    return [f"{name}=={version}" for name, version in package_pins(data, "python").items()]


def npm_install_specs(data: dict[str, Any]) -> list[str]:
    return [f"{name}@{version}" for name, version in package_pins(data, "npm").items()]


def console_host_spec(data: dict[str, Any]) -> str:
    """``@renglo/console@version`` when pinned, else empty."""
    version = package_pins(data, "npm").get(CONSOLE_NPM_PACKAGE, "").strip()
    return f"{CONSOLE_NPM_PACKAGE}@{version}" if version else ""


def console_dependency_specs(data: dict[str, Any]) -> list[str]:
    """npm pins to install into the console host (not the host package itself)."""
    return [
        f"{name}@{version}"
        for name, version in package_pins(data, "npm").items()
        if name != CONSOLE_NPM_PACKAGE
    ]


def is_non_extension_npm(name: str) -> bool:
    """Console host and white-label packs are never extension UI."""
    return name == CONSOLE_NPM_PACKAGE or bool(WL_NPM_NAME.match(name))


def npm_package_handle(name: str) -> str:
    if name.startswith("@") and "/" in name:
        return name.split("/", 1)[1]
    return name


def npm_extension_handles(data: dict[str, Any]) -> list[str]:
    """Unscoped handles for npm pins that are not the console host or a wl pack.

    Publisher scope is not the discriminator (``@x/casting`` is an
    extension; ``@x/wl`` is not). Vite still filters by UI shape at
    build time.
    """
    handles: list[str] = []
    for name in package_pins(data, "npm"):
        if is_non_extension_npm(name):
            continue
        handle = npm_package_handle(name)
        if handle not in handles:
            handles.append(handle)
    return handles


def python_package_to_repo(name: str) -> str | None:
    if name in CORE_PYTHON_TO_REPO:
        return CORE_PYTHON_TO_REPO[name]
    if name.startswith("renglo-"):
        return f"renglo/{name.removeprefix('renglo-')}"
    return None


def python_handlers_handle(name: str) -> str | None:
    """Return dist name for handlers-relevant pins; None for core lib/api/ci.

    Image builds install by dist name (``--packages``). Runtime folder handles
    are not derived here. White-label packs (``*-wl``) are branding, not handlers.
    """
    if name in CORE_PYTHON_TO_REPO:
        return None
    if is_wl_repo_name(name):
        return None
    if name.startswith("renglo-"):
        short = name.removeprefix("renglo-")
        if short in ("lib", "api", "ci"):
            return None
        return name
    return name


def python_handlers_handles(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in package_pins(data, "python"):
        handle = python_handlers_handle(name)
        if handle and handle not in out:
            out.append(handle)
    return out


def handlers_python_packages(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (all python pins, image install pins).

    Install pins keep ``renglo-lib`` first (when present), then other ``renglo-*``,
    then extension dists. White-label packs (``*-wl``) stay in the wheelhouse list
    but are omitted from ``--packages``. Prepare puts all pins in the wheelhouse;
    ``run.py build --packages`` installs that ordered list (no git clone of
    ``dev/renglo-lib``). ``install_large_extras`` skips ``renglo-*`` for the
    ``[large-dependencies]`` step.
    """
    all_pins = list(package_pins(data, "python"))

    def _install_key(name: str) -> tuple[int, str]:
        if name == "renglo-lib":
            return (0, name)
        if name.startswith("renglo-"):
            return (1, name)
        return (2, name)

    install = sorted(
        (p for p in all_pins if not is_wl_repo_name(p)),
        key=_install_key,
    )
    return all_pins, install


def npm_wl_package(data: dict[str, Any]) -> str:
    """First ``@*/wl`` pin, if any. Empty when the BOM has no white-label pack."""
    for name in package_pins(data, "npm"):
        if WL_NPM_NAME.match(name):
            return name
    return ""


def npm_pin_repo_keys(name: str, repos: dict[str, Any]) -> list[str]:
    """BOM ``repos`` keys that this npm pin replaces (do not invent tenant orgs)."""
    if name == CONSOLE_NPM_PACKAGE:
        return ["renglo/console"] if "renglo/console" in repos else []
    if WL_NPM_NAME.match(name):
        scope = name[1:].split("/", 1)[0]
        keys: list[str] = []
        for key in repos:
            short = str(key).split("/", 1)[-1]
            if short == "wl" or short == f"{scope}-wl":
                keys.append(str(key))
        return keys
    if name.startswith("@") and "/" in name:
        scope, pkg = name[1:].split("/", 1)
        guessed = f"{scope}/{pkg}"
        return [guessed] if guessed in repos else []
    return []


def npm_package_to_repo(name: str, repos: dict[str, Any] | None = None) -> str | None:
    """Repo key a pin skips, if that key is in ``repos``.

    ``@renglo/name`` still maps to ``renglo/name`` when ``repos`` is omitted
    (unit tests and callers that only have the pin name).
    """
    if repos is not None:
        keys = npm_pin_repo_keys(name, repos)
        return keys[0] if keys else None
    if name.startswith("@renglo/"):
        return f"renglo/{name.split('/', 1)[1]}"
    return None


def local_npm_install_paths(dest_root: Path, checkout: list[RepoSpec]) -> list[str]:
    """Cloned console packages with a root package.json (not console itself, not extensions)."""
    paths: list[str] = []
    for spec in checkout:
        if spec.is_extension or spec.path == "console":
            continue
        pkg_dir = dest_root / spec.path
        if (pkg_dir / "package.json").is_file():
            paths.append(str(pkg_dir.resolve()))
    return paths


def repos_skipped_by_pins(data: dict[str, Any], pipeline: str) -> set[str]:
    """Repo keys that a package pin replaces for this pipeline."""
    skipped: set[str] = set()
    if pipeline in ("backend", "handlers", "peers"):
        for name in package_pins(data, "python"):
            repo = python_package_to_repo(name)
            if repo:
                skipped.add(repo)
    if pipeline == "console":
        repos = data.get("repos") if isinstance(data.get("repos"), dict) else {}
        for name in package_pins(data, "npm"):
            for repo in npm_pin_repo_keys(name, repos):
                skipped.add(repo)
    return skipped


def load_bom(bom_file: Path) -> dict[str, Any]:
    if not bom_file.is_file():
        raise FileNotFoundError(f"BOM file not found: {bom_file}")
    data = json.loads(bom_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{bom_file}: top level must be an object")
    repos = data.get("repos")
    if repos is None:
        repos = {}
        data["repos"] = repos
    if not isinstance(repos, dict):
        raise ValueError(f"{bom_file}: repos must be an object")
    python = package_pins(data, "python")
    npm = package_pins(data, "npm")
    if not repos and not python and not npm:
        raise ValueError(f"{bom_file}: need a non-empty repos, python, or npm section")
    return data


def resolve_specs(bom_file: Path, data: dict[str, Any] | None = None) -> list[RepoSpec]:
    bom_file = Path(bom_file)
    data = data if data is not None else load_bom(bom_file)
    source = infer_source(bom_file, data)
    repos: dict[str, Any] = data["repos"]
    specs: list[RepoSpec] = []
    used_paths: dict[str, str] = {}

    for repo_key, raw_entry in repos.items():
        key = str(repo_key).strip()
        if not re.match(r"^[^/\s]+/[^/\s]+$", key):
            raise ValueError(f"Invalid repo key {repo_key!r} (expected org/name)")
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        path = str(entry.get("path", "")).strip() or default_path(key)
        path = path.lstrip("./")
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise ValueError(f"{key}: invalid path {path!r}")
        if path in used_paths:
            raise ValueError(f"Duplicate checkout path {path!r} for {key} and {used_paths[path]}")
        used_paths[path] = key

        if "pipelines" in entry:
            pipelines = parse_pipelines(entry.get("pipelines"), key)
        else:
            pipelines = default_pipelines(key, source)

        specs.append(
            RepoSpec(
                key=key,
                ref=pick_ref(entry),
                path=path,
                pipelines=pipelines,
                url=str(entry.get("url", "")).strip(),
            )
        )
    return specs


def specs_for_pipeline(specs: list[RepoSpec], pipeline: str) -> list[RepoSpec]:
    if pipeline not in VALID_PIPELINES:
        raise ValueError(f"Unknown pipeline {pipeline!r}")
    return [s for s in specs if pipeline in s.pipelines]


def checkout_specs(
    specs: list[RepoSpec],
    pipeline: str,
    data: dict[str, Any],
) -> list[RepoSpec]:
    """Pipeline specs that still need a git clone (not replaced by a package pin)."""
    skipped = repos_skipped_by_pins(data, pipeline)
    return [s for s in specs_for_pipeline(specs, pipeline) if s.key not in skipped]


def extension_handles(specs: list[RepoSpec]) -> list[str]:
    return [s.shortname for s in specs if s.is_extension]


def handlers_build_flags(
    specs: list[RepoSpec],
    data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """First handler dist + extras (checkout GITHUB_OUTPUT only).

    Prefer ``handlers_python_packages`` for ``run.py build --packages``.
    """
    handles: list[str] = []
    if data is not None:
        for handle in python_handlers_handles(data):
            if handle not in handles:
                handles.append(handle)
    for handle in extension_handles(specs):
        if handle not in handles:
            handles.append(handle)
    if not handles:
        return "", ""
    return handles[0], ",".join(handles[1:])


def scan_vite_extensions(root: Path, specs: list[RepoSpec]) -> list[str]:
    """Extension shortnames that have a console ui/ directory after checkout."""
    found: list[str] = []
    for spec in specs:
        if spec.is_extension and (root / spec.path / "ui").is_dir():
            found.append(spec.shortname)
    return found


def format_plan(specs: list[RepoSpec]) -> str:
    if not specs:
        return "(none)"
    lines = []
    for spec in specs:
        lines.append(f"  {spec.key}  ref={spec.ref}  path={spec.path}")
    return "\n".join(lines)


def format_package_plan(data: dict[str, Any]) -> str:
    lines: list[str] = []
    python = python_install_specs(data)
    npm = npm_install_specs(data)
    if python:
        lines.append("  python:")
        lines.extend(f"    {spec}" for spec in python)
    if npm:
        lines.append("  npm:")
        lines.extend(f"    {spec}" for spec in npm)
    return "\n".join(lines) if lines else ""


def pipeline_has_work(
    pipeline: str,
    selected: list[RepoSpec],
    data: dict[str, Any],
    *,
    source: str = "",
) -> bool:
    if selected:
        return True
    if pipeline == "console":
        return bool(package_pins(data, "npm"))
    if pipeline == "backend":
        return bool(package_pins(data, "python"))
    if pipeline == "handlers":
        if source == "peers":
            return False
        return bool(package_pins(data, "python"))
    if pipeline == "peers":
        if source == "handlers":
            return False
        return bool(package_pins(data, "python"))
    return False


def write_github_output(values: dict[str, str]) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    lines = [f"{key}={value}" for key, value in values.items()]
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n--- GITHUB_OUTPUT (dry run) ---")
        print("\n".join(lines))


def ci_outputs(
    data: dict[str, Any],
    checkout: list[RepoSpec],
    *,
    dest_root: Path | None = None,
) -> dict[str, str]:
    python = python_install_specs(data)
    npm_deps = console_dependency_specs(data)
    host = console_host_spec(data)
    local_npm = local_npm_install_paths(dest_root, checkout) if dest_root else []
    return {
        "python_specs": " ".join(python),
        "npm_specs": " ".join(npm_deps),
        "npm_local_specs": " ".join(local_npm),
        "has_python_pins": "true" if python else "false",
        "has_npm_pins": "true" if npm_deps else "false",
        "has_npm_local": "true" if local_npm else "false",
        "console_host_spec": host,
        "has_console_pin": "true" if host else "false",
        "vite_wl_package": npm_wl_package(data),
        "repo_count": str(len(checkout)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or validate a BOM JSON")
    parser.add_argument("bom_file", help="Path to bom/vX.Y.Z.json, handlers_bom/vX.Y.Z.json, or peers_bom/<id>/vX.Y.Z.json")
    parser.add_argument(
        "--pipeline",
        choices=VALID_PIPELINES,
        default="",
        help="Filter the plan to one pipeline",
    )
    parser.add_argument("--plan", action="store_true", help="Print checkout and package-pin plan")
    parser.add_argument("--validate", action="store_true", help="Validate JSON and exit non-zero on errors")
    parser.add_argument("--pip-specs", action="store_true", help="Print space-separated pkg==ver pins")
    parser.add_argument("--npm-specs", action="store_true", help="Print space-separated pkg@version pins")
    parser.add_argument("--ci", action="store_true", help="Write pin/checkout fields to GITHUB_OUTPUT")
    args = parser.parse_args()

    if not any((args.plan, args.validate, args.pip_specs, args.npm_specs, args.ci)):
        args.plan = True

    path = Path(args.bom_file)
    try:
        data = load_bom(path)
        specs = resolve_specs(path, data)
        selected = checkout_specs(specs, args.pipeline, data) if args.pipeline else specs
        source = infer_source(path, data)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.pipeline and not pipeline_has_work(
        args.pipeline, selected, data, source=source
    ):
        print(f"No repos or package pins for pipeline {args.pipeline!r} in {path}", file=sys.stderr)
        return 1

    if args.pip_specs:
        print(" ".join(python_install_specs(data)))
        return 0
    if args.npm_specs:
        print(" ".join(npm_install_specs(data)))
        return 0
    if args.ci:
        write_github_output(ci_outputs(data, selected if args.pipeline else specs))
        return 0

    if args.validate:
        print(f"OK {path} ({len(specs)} repos, {len(package_pins(data, 'python'))} python, {len(package_pins(data, 'npm'))} npm)")
        if args.pipeline:
            print(f"  {args.pipeline}: {len(selected)} checkout(s)")
        return 0

    label = args.pipeline or "all"
    print(f"{path}  pipeline={label}  checkout={len(selected)}")
    pins = format_package_plan(data)
    if pins:
        print(pins)
    print(format_plan(selected))
    if args.pipeline in ("handlers", "peers"):
        _all, pkgs = handlers_python_packages(data)
        label = "peers" if args.pipeline == "peers" else "handlers"
        print(f"  {label} --packages {','.join(pkgs) or '(none)'}")
        if _all:
            print(f"  {label} wheelhouse pins: {','.join(_all)}")
    if args.pipeline == "console":
        candidates = extension_handles(selected)
        print(f"  console extension candidates: {','.join(candidates) or '(none)'}")
        print("  console discovers UI from extensions/*/ui and npm pins (no VITE_EXTENSIONS)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
