"""Fill a BOM checkout so the first deploy has a catalog, pins, and GitHub ids.

``renglo bom setup`` calls this. An empty ``renglo.yaml`` receives the platform
packages (lib, api, console, ``<namespace>-wl``, data, schd), the two registries,
and hub placement. Each package pin is the newest Published version that is not
a release candidate. ``platform`` in the manifest is the checked-out
``renglo-ops`` version. The first manifest is ``v0.0.1``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.registry import sanitize_domain_name
from renglo_ops.model.tenant import RegistryRef, Tenant, dump_tenant
from renglo_ops.release.align_workspace import RENGLO_DOMAIN_OWNER
from renglo_ops.release.catalog_slots import PackageSlot

FIRST_BOM_VERSION = "0.0.1"
_PYPROJECT_VERSION = re.compile(r"(?m)^version\s*=\s*['\"]([^'\"]+)['\"]")

STARTER_ENV = {
    "BEDROCK_MODELS": (
        "us.anthropic.claude-sonnet-5-5,"
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0,"
        "us.anthropic.claude-haiku-5-5"
    ),
    "OPENAI_MODELS": "gpt-4o-mini",
}

GithubLookup = Callable[[str], tuple[str, str]]


def installed_platform_version() -> str:
    for dist in ("renglo-ops", "renglo"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return ""


def checked_out_platform_version() -> str:
    """Version in the checked-out ``renglo-ops`` ``lib/pyproject.toml``."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        match = _PYPROJECT_VERSION.search(pyproject.read_text(encoding="utf-8"))
        if match:
            return _strip_v(match.group(1))
    return _strip_v(installed_platform_version())


def prepare_tenant(tenant: Tenant, *, platform: str = "") -> list[str]:
    """Fill blank catalog sections. Existing packages, registries, and placement stay.

    ``platform`` is accepted so an older caller can still pass one. The
    checked-out ``renglo-ops`` version is applied later, and the first manifest
    is ``0.0.1``.
    """
    del platform
    notes: list[str] = []
    if not tenant.registries:
        tenant.registries = _starter_registries(tenant.namespace)
        notes.append("registries set to the tenant domain and renglo")
    if not tenant.packages:
        tenant.packages = _starter_packages(tenant.namespace)
        notes.append("packages set to the platform set")
    if not tenant.placement_hub and not tenant.placement_peers:
        tenant.placement_hub = ["data", "schd"]
        notes.append("placement.hub set to data, schd")
    if not tenant.env:
        tenant.env = dict(STARTER_ENV)
        notes.append("env set to the starter model list")
    return notes


def fetch_github_ids(repo: str) -> tuple[str, str]:
    """Return ``(owner_id, repo_id)`` from ``gh api repos/ORG/REPO``."""
    slug = str(repo or "").strip().strip("/")
    if slug.count("/") != 1:
        raise RengloOpsError(f"github.repo must be ORG/REPO to look up ids, got {repo!r}")
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{slug}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RengloOpsError("gh is not on PATH; install the GitHub CLI and run gh auth login") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RengloOpsError(f"could not read GitHub ids for {slug}: {detail}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RengloOpsError(f"GitHub returned unexpected JSON for {slug}") from exc
    owner = data.get("owner") if isinstance(data, dict) else None
    owner_id = str((owner or {}).get("id") or "").strip() if isinstance(owner, dict) else ""
    repo_id = str(data.get("id") or "").strip() if isinstance(data, dict) else ""
    if not owner_id or not repo_id:
        raise RengloOpsError(f"GitHub response for {slug} has no owner id or repository id")
    return owner_id, repo_id


def insert_github_ids(text: str, owner_id: str, repo_id: str) -> str:
    """Add missing id lines under ``github:`` without rewriting the rest of the file."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r"^github:\s*$", line)), None)
    if start is None:
        raise RengloOpsError("renglo.yaml has no github: block")
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            break
        end += 1
    block = lines[start + 1 : end]

    def present(key: str) -> bool:
        return any(re.match(rf"^\s+{key}:", line) for line in block)

    extra: list[str] = []
    if owner_id and not present("owner_id"):
        extra.append(f"  owner_id: '{owner_id}'")
    if repo_id and not present("repo_id"):
        extra.append(f"  repo_id: '{repo_id}'")
    if not extra:
        body = text if text.endswith("\n") else text + "\n"
        return body
    insert_at = end
    for index in range(start + 1, end):
        if re.match(r"^\s+repo:", lines[index]):
            insert_at = index + 1
            break
    lines[insert_at:insert_at] = extra
    return "\n".join(lines) + "\n"


def ensure_github_ids(
    path: Path,
    *,
    lookup: GithubLookup | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Write missing GitHub ids before a deploy creates the OIDC role.

    ``renglo bom setup`` runs after the first stack deploy, so the role cannot
    wait for that command. A blank id is read from ``gh`` and inserted here.
    """
    from renglo_ops.model.tenant import load_tenant

    path = path.expanduser().resolve()
    tenant = load_tenant(path)
    if tenant.github_owner_id and tenant.github_repo_id:
        return []
    owner_id, repo_id = (lookup or fetch_github_ids)(tenant.github_repo)
    missing: list[tuple[str, str]] = []
    if not tenant.github_owner_id:
        missing.append(("github.owner_id", owner_id))
    if not tenant.github_repo_id:
        missing.append(("github.repo_id", repo_id))
    if dry_run:
        return [f"would set {field} to {value}" for field, value in missing]
    updated = insert_github_ids(path.read_text(encoding="utf-8"), owner_id, repo_id)
    path.write_text(updated, encoding="utf-8")
    return [f"{field} set to {value}" for field, value in missing]


def _strip_v(version: str) -> str:
    text = str(version or "").strip()
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        return text[1:]
    return text


def manifest_versions(tenant: Tenant) -> list[str]:
    versions: list[str] = []
    for raw in (tenant.release_bom, tenant.release_console):
        text = _strip_v(raw)
        if text and text not in versions:
            versions.append(text)
    return versions


def _on_disk_manifest_versions(root: Path) -> set[str]:
    found: set[str] = set()
    folders = [root / "bom", root / "console_bom"]
    peers = root / "peers_bom"
    if peers.is_dir():
        folders.extend(path for path in peers.iterdir() if path.is_dir())
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.glob("v*.json"):
            found.add(_strip_v(path.stem))
    return {item for item in found if item}


def _has_tracked_manifest(root: Path) -> bool:
    """True when this BOM repo already tracks a versioned manifest."""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return False
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", "bom", "console_bom", "peers_bom"],
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return False
    return any(re.search(r"(^|/)v[^/]+\.json$", line) for line in listed.stdout.splitlines())


def assign_first_release(root: Path, tenant: Tenant) -> tuple[list[str], bool]:
    """Name the first manifest ``0.0.1``.

    A file whose version is only the platform pin does not count as an earlier
    BOM. A repo that already tracks a manifest keeps its release pins.
    """
    if _has_tracked_manifest(root):
        return _fill_blank_release(tenant), False
    platform = _strip_v(tenant.platform)
    earlier = {item for item in _on_disk_manifest_versions(root) if item != platform}
    if earlier:
        return _fill_blank_release(tenant), False
    notes: list[str] = []
    if tenant.release_bom != FIRST_BOM_VERSION:
        tenant.release_bom = FIRST_BOM_VERSION
        notes.append("release.bom set to 0.0.1")
    if tenant.release_console != FIRST_BOM_VERSION:
        tenant.release_console = FIRST_BOM_VERSION
        notes.append("release.console set to 0.0.1")
    return notes, True


def _fill_blank_release(tenant: Tenant) -> list[str]:
    notes: list[str] = []
    if not tenant.release_bom:
        tenant.release_bom = FIRST_BOM_VERSION
        notes.append("release.bom set to 0.0.1")
    if not tenant.release_console:
        tenant.release_console = tenant.release_bom
        notes.append(f"release.console set to {tenant.release_console}")
    return notes


def remove_other_manifests(root: Path, keep: str) -> list[str]:
    """Delete manifest files other than ``v{keep}.json``."""
    keep_name = f"v{_strip_v(keep)}.json"
    removed: list[str] = []
    folders = [root / "bom", root / "console_bom"]
    peers = root / "peers_bom"
    if peers.is_dir():
        folders.extend(path for path in peers.iterdir() if path.is_dir())
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("v*.json")):
            if path.name == keep_name:
                continue
            path.unlink()
            removed.append(path.relative_to(root).as_posix())
    return removed


def _layout():
    """``bom_layout`` imports its neighbors by bare name, as the release runner does."""
    release = str(Path(__file__).resolve().parent)
    if release not in sys.path:
        sys.path.insert(0, release)
    from bom_layout import Placement, write_split_boms

    return Placement, write_split_boms


def build_master(
    tenant: Tenant,
    version: str,
    pins: dict[str, str],
    *,
    platform: str,
) -> tuple[dict[str, Any], list[PackageSlot], Any]:
    pin = _strip_v(version)
    placement_type, _write = _layout()
    catalog = _catalog(tenant.packages)
    python: dict[str, str] = {}
    npm: dict[str, str] = {}
    for slot in catalog:
        if slot.python and pins.get(slot.python):
            python[slot.python] = pins[slot.python]
        if slot.npm and pins.get(slot.npm):
            npm[slot.npm] = pins[slot.npm]
    placement = _placement(tenant, catalog, placement_type)
    master = {
        "version": f"v{pin}",
        "description": f"Packages for {tenant.name}.",
        "platform": _strip_v(platform),
        "python": python,
        "npm": npm,
        "repos": {},
    }
    return master, catalog, placement


def write_manifests(
    bom_root: Path,
    tenant: Tenant,
    pins: dict[str, str],
    *,
    force: bool,
    dry_run: bool,
) -> list[str]:
    del force
    notes: list[str] = []
    for version in manifest_versions(tenant):
        master, catalog, placement = build_master(
            tenant, version, pins, platform=tenant.platform
        )
        file_name = f"v{version}.json"
        if dry_run:
            notes.append(f"would write bom/{file_name} and console_bom/{file_name}")
            continue
        _placement_type, write_split_boms = _layout()
        del _placement_type
        written = write_split_boms(
            bom_root,
            version,
            master,
            catalog=catalog,
            placement=placement,
        )
        hub_rel = Path(written["hub"]).relative_to(bom_root).as_posix()
        console_rel = Path(written["console"]).relative_to(bom_root).as_posix()
        notes.append(f"wrote {hub_rel} and {console_rel}")
    if not manifest_versions(tenant):
        notes.append("no release.bom or release.console version to pin")
    return notes


def registry_for(tenant: Tenant, package: str, fmt: str) -> RegistryRef:
    """Registry row that hosts this package.

    npm uses ``scopes``. Python uses the domain the distribution is named after
    (``renglo-lib`` is domain ``renglo``, ``<namespace>-wl`` is that namespace).
    """
    if fmt == "npm":
        scope = ""
        if package.startswith("@") and "/" in package:
            scope = package.split("/", 1)[0]
        for item in tenant.registries:
            if scope and scope in item.scopes:
                return item
        raise RengloOpsError(
            f"{package} does not match a registries scope. "
            "Add that scope to the domain that publishes it."
        )
    matches = [
        item
        for item in tenant.registries
        if package == item.domain or package.startswith(f"{item.domain}-")
    ]
    if not matches:
        raise RengloOpsError(
            f"{package} does not match a registries domain. "
            "Python packages are read from the domain they are named after."
        )
    matches.sort(key=lambda item: len(item.domain), reverse=True)
    return matches[0]


_RC = re.compile(r"(?i)(?<![A-Za-z])rc(?![A-Za-z])")


def is_release_candidate(version: str) -> bool:
    """True for an rc tag such as ``0.0.20rc2`` or ``0.0.25-rc.2``."""
    return bool(_RC.search(version.strip()))


def latest_published(
    client: Any,
    registry: RegistryRef,
    package: str,
    fmt: str,
    *,
    account_id: str,
) -> str:
    """Newest Published version that is not an rc, or empty when none exists."""
    artifact_format, namespace, pkg = _coordinates(package, fmt)
    owner = registry.account.strip() or account_id.strip()
    if not owner:
        raise RengloOpsError(f"{registry.domain} has no account and no caller account was given")
    repository = registry.npm if artifact_format == "npm" else registry.python
    kwargs: dict[str, Any] = {
        "domain": sanitize_domain_name(registry.domain),
        "domainOwner": owner,
        "repository": repository,
        "format": artifact_format,
        "package": pkg,
        "status": "Published",
        "sortBy": "PUBLISHED_TIME",
        "maxResults": 100,
    }
    if namespace:
        kwargs["namespace"] = namespace
    token = ""
    try:
        while True:
            call = dict(kwargs)
            if token:
                call["nextToken"] = token
            page = client.list_package_versions(**call)
            for row in page.get("versions") or []:
                found = str(row.get("version") or "").strip()
                if found and not is_release_candidate(found):
                    return found
            token = str(page.get("nextToken") or "")
            if not token:
                return ""
    except Exception as exc:
        if _error_code(exc) == "ResourceNotFoundException":
            return ""
        raise RengloOpsError(str(exc)) from exc


def published_pins(
    tenant: Tenant,
    client: Any,
    *,
    account_id: str,
) -> tuple[dict[str, str], list[str]]:
    """Newest stable Published versions, and the packages that have none yet."""
    wanted: list[tuple[str, str]] = []
    for slot in _catalog(tenant.packages):
        if slot.python and slot.python != "renglo-ops":
            wanted.append((slot.python, "python"))
        if slot.npm:
            wanted.append((slot.npm, "npm"))
    pins: dict[str, str] = {}
    missing: list[str] = []
    for name, fmt in wanted:
        if name in pins:
            continue
        registry = registry_for(tenant, name, fmt)
        found = latest_published(client, registry, name, fmt, account_id=account_id)
        if not found:
            missing.append(f"{name} ({registry.domain})")
            continue
        pins[name] = found
    return pins, missing


def setup_bom(
    bom_root: Path,
    *,
    platform: str = "",
    dry_run: bool = False,
    force: bool = False,
    lookup: GithubLookup | None = None,
    codeartifact: Any = None,
    account_id: str = "",
    versions: dict[str, str] | None = None,
) -> list[str]:
    """Update ``renglo.yaml`` and write the pin files. Returns the lines to print."""
    del platform
    from renglo_ops.model.local import refuse_tool_output
    from renglo_ops.model.tenant import load_tenant

    bom_root = bom_root.expanduser().resolve()
    path = bom_root / "renglo.yaml"
    refuse_tool_output(path)
    tenant = load_tenant(path)
    notes = prepare_tenant(tenant)
    lookup = lookup or fetch_github_ids
    if not tenant.github_owner_id or not tenant.github_repo_id:
        owner_id, repo_id = lookup(tenant.github_repo)
        if not tenant.github_owner_id:
            tenant.github_owner_id = owner_id
            notes.append(f"github.owner_id set to {owner_id}")
        if not tenant.github_repo_id:
            tenant.github_repo_id = repo_id
            notes.append(f"github.repo_id set to {repo_id}")
    missing: list[str] = []
    if versions is not None:
        pins = dict(versions)
    elif codeartifact is None:
        raise RengloOpsError("CodeArtifact client is required to read published versions")
    else:
        pins, missing = published_pins(tenant, codeartifact, account_id=account_id)
    checkout = checked_out_platform_version()
    if not checkout:
        raise RengloOpsError("could not read the checked-out renglo-ops version")
    platform_note = ""
    if tenant.platform != checkout:
        tenant.platform = checkout
        platform_note = f"platform set to {checkout}"
        notes.append(platform_note)
    release_notes, first_bom = assign_first_release(bom_root, tenant)
    notes.extend(release_notes)
    notes.append(f"platform {tenant.platform} (checked-out renglo-ops)")
    for name in sorted(pins):
        if name == "renglo-ops":
            continue
        registry = _registry_for_known(tenant, name)
        notes.append(f"{name} {pins[name]} ({registry.domain})")
    if missing:
        notes.append("no stable Published version, left out: " + ", ".join(missing))
    structural = [
        note
        for note in notes
        if note == platform_note or note.startswith(("registries ", "packages ", "placement.", "release.", "env "))
    ]
    if dry_run:
        preview = [note for note in notes if note != platform_note or platform_note]
        if structural or any(note.startswith("github.") for note in notes):
            changed = [note for note in notes if note.startswith("github.") or note in structural]
            preview = [f"would update renglo.yaml ({'; '.join(changed)})", *[
                note for note in notes if note not in changed
            ]]
        notes = preview
        if first_bom:
            for stale in _other_manifests(bom_root, FIRST_BOM_VERSION):
                notes.append(f"would remove {stale}")
        notes.extend(write_manifests(bom_root, tenant, pins, force=force, dry_run=True))
        return notes
    original = path.read_text(encoding="utf-8")
    starter = [
        note
        for note in structural
        if note != platform_note and not note.startswith("release.")
    ]
    if starter:
        path.write_text(dump_tenant(tenant), encoding="utf-8")
    else:
        updated = original
        if platform_note:
            updated = _replace_platform(updated, tenant.platform)
        if any(note.startswith("release.bom") for note in release_notes):
            updated = _replace_release_field(updated, "bom", tenant.release_bom)
        if any(note.startswith("release.console") for note in release_notes):
            updated = _replace_release_field(updated, "console", tenant.release_console)
        if tenant.github_owner_id or tenant.github_repo_id:
            updated = insert_github_ids(updated, tenant.github_owner_id, tenant.github_repo_id)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
    if first_bom:
        for stale in remove_other_manifests(bom_root, FIRST_BOM_VERSION):
            notes.append(f"removed {stale}")
    notes.extend(write_manifests(bom_root, tenant, pins, force=force, dry_run=False))
    return notes


def _registry_for_known(tenant: Tenant, name: str) -> RegistryRef:
    fmt = "npm" if name.startswith("@") else "python"
    return registry_for(tenant, name, fmt)


def _coordinates(package: str, fmt: str) -> tuple[str, str, str]:
    if fmt == "npm":
        if not package.startswith("@") or "/" not in package[1:]:
            raise RengloOpsError(f"npm package must look like @scope/name, got {package}")
        namespace, pkg = package[1:].split("/", 1)
        return "npm", namespace.strip(), pkg.strip()
    normalized = re.sub(r"[-_.]+", "-", package).lower()
    return "pypi", "", normalized


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


def _replace_platform(text: str, version: str) -> str:
    if not re.search(r"(?m)^platform:", text):
        raise RengloOpsError("renglo.yaml has no platform field")
    return re.sub(r"(?m)^platform:\s*\S.*$", f"platform: {version}", text, count=1)


def _replace_release_field(text: str, field: str, version: str) -> str:
    block = re.search(r"(?ms)^release:\n(.*?)(?=^\S|\Z)", text)
    if not block:
        raise RengloOpsError("renglo.yaml has no release: block")
    section = block.group(0)
    new_section, count = re.subn(
        rf"(?m)^(\s+){re.escape(field)}:\s+\S+",
        rf"\1{field}: {version}",
        section,
        count=1,
    )
    if count != 1:
        raise RengloOpsError(f"could not update release.{field} in renglo.yaml")
    return text[: block.start()] + new_section + text[block.end() :]


def _other_manifests(root: Path, keep: str) -> list[str]:
    keep_name = f"v{_strip_v(keep)}.json"
    found: list[str] = []
    folders = [root / "bom", root / "console_bom"]
    peers = root / "peers_bom"
    if peers.is_dir():
        folders.extend(path for path in peers.iterdir() if path.is_dir())
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("v*.json")):
            if path.name == keep_name:
                continue
            found.append(path.relative_to(root).as_posix())
    return found


def _starter_registries(namespace: str) -> list[RegistryRef]:
    own = RegistryRef(
        domain=namespace,
        python="python-store",
        npm="npm-store",
        scopes=[f"@{namespace}"],
    )
    if namespace == "renglo":
        return [own]
    renglo = RegistryRef(
        domain="renglo",
        python="python-store",
        npm="npm-store",
        account=RENGLO_DOMAIN_OWNER,
        scopes=["@renglo"],
    )
    return [own, renglo]


def _starter_packages(namespace: str) -> dict[str, dict[str, str]]:
    wl = f"{namespace}-wl"
    return {
        "renglo-lib": {"python": "renglo-lib"},
        "renglo-api": {"python": "renglo-api"},
        "console": {"npm": "@renglo/console"},
        wl: {"python": wl, "npm": f"@{namespace}/wl"},
        "data": {"python": "renglo-data", "npm": "@renglo/data"},
        "schd": {"python": "renglo-schd", "npm": "@renglo/schd"},
    }


def _catalog(packages: dict[str, Any]) -> list[PackageSlot]:
    slots: list[PackageSlot] = []
    for handle, spec in packages.items():
        if isinstance(spec, dict):
            slots.append(
                PackageSlot(
                    id=str(handle),
                    python=str(spec.get("python") or "").strip(),
                    npm=str(spec.get("npm") or "").strip(),
                    repo=str(spec.get("repo") or "").strip(),
                )
            )
        else:
            slots.append(PackageSlot(id=str(handle), python=str(spec or "").strip()))
    return slots


def _python_name(catalog: list[PackageSlot], item: str) -> str:
    name = str(item or "").strip()
    for slot in catalog:
        if slot.id == name and slot.python:
            return slot.python
    for slot in catalog:
        if slot.python == name:
            return name
    return name


def _placement(tenant: Tenant, catalog: list[PackageSlot], placement_type: Any):
    hub = tuple(_python_name(catalog, item) for item in tenant.placement_hub)
    peers: dict[str, tuple[str, ...]] = {}
    for peer_id, cfg in tenant.placement_peers.items():
        raw: list[Any] = []
        if isinstance(cfg, dict):
            listed = cfg.get("extensions")
            if not isinstance(listed, list):
                listed = cfg.get("python") or []
            if isinstance(listed, list):
                raw = listed
        peers[str(peer_id)] = tuple(_python_name(catalog, item) for item in raw)
    return placement_type(hub_python=hub, peers=peers)
