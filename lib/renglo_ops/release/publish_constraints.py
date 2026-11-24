"""Write the dependency locks for a BOM built from published packages.

``renglo bom setup`` has no product checkout. It resolves each pinned
distribution from CodeArtifact, freezes the closure, and writes the same
files git-convoy writes beside a generated BOM:

* ``constraints/vX.Y.Z.txt``
* ``constraints/peers/<id>/vX.Y.Z.txt``
* ``constraints/console/vX.Y.Z.package-lock.json``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.registry import sanitize_domain_name
from renglo_ops.model.tenant import Tenant
from renglo_ops.release.bom_manifest import (
    CONSOLE_NPM_PACKAGE,
    console_host_spec,
    load_bom,
    package_pins,
)
from renglo_ops.release.configure_codeartifact import pip_extra_index_url
from renglo_ops.release.python_constraints import (
    canonical_name,
    constraints_path,
    find_uv,
    parse_exact_pins,
    render_constraints,
)

Resolve = Callable[[list[str], list[str], list[str]], str]
PYPI = "https://pypi.org/simple"


def _scrub(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if not secret:
            continue
        cleaned = cleaned.replace(secret, "***")
        cleaned = cleaned.replace(quote(secret, safe=""), "***")
    return cleaned


def console_lock_path(bom_file: Path) -> Path:
    """``console_bom/v0.0.1.json`` → ``constraints/console/v0.0.1.package-lock.json``."""
    return bom_file.parent.parent / "constraints" / "console" / f"{bom_file.stem}.package-lock.json"


def merge_lock_pins(
    bom_python: dict[str, str],
    resolved: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Keep the BOM's own versions and every other distribution the resolver chose."""
    bom_names = {canonical_name(name) for name in bom_python}
    pins = [(name, version) for name, version in resolved if canonical_name(name) not in bom_names]
    pins.extend((str(name), str(version).strip()) for name, version in bom_python.items())
    return pins


def python_lock_text(
    bom_file: Path,
    *,
    extra_args: list[str],
    secrets: list[str],
    also: list[str] | None = None,
    resolve: Resolve | None = None,
) -> str:
    """Exact pins for one python BOM, resolved from the published distributions."""
    bom_python = package_pins(load_bom(bom_file), "python")
    if not bom_python:
        return ""
    requirements = [f"{name}=={str(version).strip()}" for name, version in bom_python.items()]
    requirements.extend(item.strip() for item in (also or []) if item.strip())
    runner = resolve or _resolve_with_uv
    try:
        stdout = runner(requirements, extra_args, secrets)
        resolved = parse_exact_pins(stdout, source="uv pip compile")
    except (OSError, ValueError) as exc:
        raise RengloOpsError(_scrub(str(exc), secrets)) from exc
    return render_constraints(
        merge_lock_pins(bom_python, resolved),
        bom_file=bom_file,
    )


def _resolve_with_uv(requirements: list[str], extra_args: list[str], secrets: list[str]) -> str:
    uv = find_uv()
    if not uv:
        raise RengloOpsError(
            "uv is not installed. Install uv and rerun renglo bom setup. "
            "https://docs.astral.sh/uv/"
        )
    cmd = [uv]
    cmd.extend(
        [
            "pip",
            "compile",
            "--python-version",
            "3.12",
            "--no-header",
            "--no-annotate",
            *extra_args,
            "-",
        ]
    )
    try:
        completed = subprocess.run(
            cmd,
            input="\n".join(requirements) + "\n",
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RengloOpsError("uv is not installed. Install uv and rerun renglo bom setup.") from exc
    if completed.returncode != 0:
        detail = _scrub(completed.stderr or completed.stdout or "uv pip compile failed", secrets).strip()
        raise RengloOpsError(detail or "uv pip compile failed")
    return completed.stdout


def _registry_auth(
    tenant: Tenant,
    client: Any,
    account_id: str,
) -> tuple[list[str], str, list[str]]:
    """Authenticated Python indexes, an npmrc, and the tokens those contain."""
    indexes: list[str] = []
    npm_lines = ["registry=https://registry.npmjs.org/"]
    secrets: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for registry in tenant.registries:
        owner = registry.account.strip() or account_id.strip()
        if not owner:
            raise RengloOpsError(f"{registry.domain} has no account and no caller account was given")
        domain = sanitize_domain_name(registry.domain)
        token = client.get_authorization_token(domain=domain, domainOwner=owner)["authorizationToken"]
        secrets.append(str(token))
        python_key = (domain, owner, registry.python)
        if python_key not in seen:
            seen.add(python_key)
            endpoint = client.get_repository_endpoint(
                domain=domain,
                domainOwner=owner,
                repository=registry.python,
                format="pypi",
            )["repositoryEndpoint"]
            indexes.append(pip_extra_index_url(str(endpoint), str(token)))
        npm_endpoint = str(
            client.get_repository_endpoint(
                domain=domain,
                domainOwner=owner,
                repository=registry.npm,
                format="npm",
            )["repositoryEndpoint"]
        ).rstrip("/") + "/"
        parsed = urlparse(npm_endpoint)
        host_path = f"//{parsed.netloc}{parsed.path}"
        for scope in registry.scopes:
            scope_name = str(scope).strip()
            if not scope_name:
                continue
            if not scope_name.startswith("@"):
                scope_name = f"@{scope_name}"
            npm_lines.append(f"{scope_name}:registry={npm_endpoint}")
        npm_lines.append(f"{host_path}:_authToken={token}")
    return indexes, "\n".join(npm_lines) + "\n", secrets


def _npm(args: list[str], *, cwd: Path, secrets: list[str]) -> None:
    try:
        completed = subprocess.run(
            ["npm", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RengloOpsError("npm is not installed. Install Node.js and rerun renglo bom setup.") from exc
    if completed.returncode != 0:
        detail = _scrub(completed.stderr or completed.stdout or "npm failed", secrets).strip()
        raise RengloOpsError(detail or "npm failed")


def console_lock_from_published(
    console_bom: Path,
    *,
    npmrc_text: str,
    secrets: list[str],
) -> Path | None:
    """Freeze the console host and the other npm pins at the versions just published."""
    data = load_bom(console_bom)
    npm_pins = package_pins(data, "npm")
    if not npm_pins:
        return None
    spec = console_host_spec(data)
    if not spec:
        raise RengloOpsError(
            f"{console_bom.name} has npm pins and no {CONSOLE_NPM_PACKAGE} pin to lock them into."
        )
    dest = console_lock_path(console_bom)
    with tempfile.TemporaryDirectory(prefix="bom-console-lock-") as raw:
        work = Path(raw)
        npmrc = work / ".npmrc"
        npmrc.write_text(npmrc_text, encoding="utf-8")
        pack_dir = work / "pack"
        pack_dir.mkdir()
        _npm(
            ["pack", spec, "--pack-destination", str(pack_dir), "--userconfig", str(npmrc)],
            cwd=work,
            secrets=secrets,
        )
        tarballs = sorted(pack_dir.glob("*.tgz"))
        if len(tarballs) != 1:
            raise RengloOpsError(f"npm pack {spec} produced {len(tarballs)} tarball(s)")
        unpacked = work / "unpacked"
        with tarfile.open(tarballs[0], "r:gz") as archive:
            archive.extractall(unpacked, filter="data")
        package_dir = unpacked / "package"
        package_file = package_dir / "package.json"
        if not package_file.is_file():
            raise RengloOpsError(f"{spec} tarball has no package.json")
        host = json.loads(package_file.read_text(encoding="utf-8"))
        dependencies = dict(host.get("dependencies") or {})
        host_name = str(host.get("name") or "").strip()
        for name, version in npm_pins.items():
            if str(name) == host_name:
                continue
            dependencies[str(name)] = str(version).strip()
        host["dependencies"] = dependencies
        lock_dir = work / "lock"
        lock_dir.mkdir()
        (lock_dir / "package.json").write_text(json.dumps(host, indent=2) + "\n", encoding="utf-8")
        (lock_dir / ".npmrc").write_text(npmrc_text, encoding="utf-8")
        packed_lock = package_dir / "package-lock.json"
        if packed_lock.is_file():
            shutil.copyfile(packed_lock, lock_dir / "package-lock.json")
        _npm(
            [
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--userconfig",
                str(lock_dir / ".npmrc"),
            ],
            cwd=lock_dir,
            secrets=secrets,
        )
        produced = lock_dir / "package-lock.json"
        if not produced.is_file():
            raise RengloOpsError("npm did not write a console lock")
        text = produced.read_text(encoding="utf-8")
        if any(secret and secret in text for secret in secrets):
            raise RengloOpsError("console lock contained a registry token and was not written")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return dest


def _write_python_lock(
    bom_file: Path,
    *,
    extra_args: list[str],
    secrets: list[str],
    also: list[str] | None = None,
    resolve: Resolve | None = None,
) -> Path | None:
    text = python_lock_text(
        bom_file,
        extra_args=extra_args,
        secrets=secrets,
        also=also,
        resolve=resolve,
    )
    if not text:
        return None
    if any(secret and secret in text for secret in secrets):
        raise RengloOpsError("python lock contained a registry token and was not written")
    dest = constraints_path(bom_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def publish_bom_constraints(
    bom_root: Path,
    written: dict[str, Any],
    *,
    tenant: Tenant,
    codeartifact: Any,
    account_id: str,
    resolve: Resolve | None = None,
) -> list[str]:
    """Resolve the BOMs just written and store their locks next to them."""
    indexes, npmrc, secrets = _registry_auth(tenant, codeartifact, account_id)
    extra_args = ["--index-url", PYPI]
    for url in indexes:
        extra_args.extend(["--extra-index-url", url])
    notes: list[str] = []
    hub = _write_python_lock(
        Path(written["hub"]),
        extra_args=extra_args,
        secrets=secrets,
        resolve=resolve,
    )
    if hub is not None:
        notes.append(f"wrote {hub.relative_to(bom_root).as_posix()}")
    for peer_id, peer_path in (written.get("peers") or {}).items():
        dest = peer_constraints_dest(bom_root, str(peer_id), Path(peer_path))
        text = python_lock_text(
            Path(peer_path),
            extra_args=extra_args,
            secrets=secrets,
            also=["setuptools>=40.8.0", "wheel"],
            resolve=resolve,
        )
        if not text:
            continue
        if any(secret and secret in text for secret in secrets):
            raise RengloOpsError("python lock contained a registry token and was not written")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        notes.append(f"wrote {dest.relative_to(bom_root).as_posix()}")
    console = console_lock_from_published(
        Path(written["console"]),
        npmrc_text=npmrc,
        secrets=secrets,
    )
    if console is not None:
        notes.append(f"wrote {console.relative_to(bom_root).as_posix()}")
    return notes


def peer_constraints_dest(bom_root: Path, peer_id: str, peer_bom: Path) -> Path:
    """``constraints/peers/<id>/v0.0.1.txt``. Not beside ``peers_bom/``."""
    return bom_root / "constraints" / "peers" / peer_id / f"{peer_bom.stem}.txt"

