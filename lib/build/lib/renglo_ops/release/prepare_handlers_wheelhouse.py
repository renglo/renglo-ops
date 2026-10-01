#!/usr/bin/env python3
"""Build a handlers wheelhouse + thin assets tree for peer / overflow builds.

Produces::

    <out>/wheelhouse/            # .whl / .tar.gz (find-links)
    <out>/handlers-assets/
      lambda_router.py           # from primary sdist (first package that has one)
      handlers_config.json
      extras/<dist>/handlers_config.json

Local monorepo::

    python scripts/prepare_handlers_wheelhouse.py \\
      --from-monorepo extensions/acmewidget/package,extensions/acmeextra/package \\
      --out .handlers-build

Pre-downloaded artifacts (CI)::

    python scripts/prepare_handlers_wheelhouse.py \\
      --from-artifacts /tmp/wheels \\
      --packages acme-widget,acme-extra \\
      --out .handlers-build

ECS / large image (same wheelhouse, plus [large-dependencies] wheels)::

    python scripts/prepare_handlers_wheelhouse.py \\
      --from-monorepo ... --with-large-deps --out .handlers-build
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from stage_extension_blueprints import stage_extension_blueprints

_SDIST_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.+-]+)-(?P<version>[0-9][^/]*)\.(?:tar\.gz|zip)$",
    re.IGNORECASE,
)


def normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def read_project_name(package_dir: Path) -> str:
    pyproject = package_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise FileNotFoundError(f"pyproject.toml not found under {package_dir}")
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    name = str((data.get("project") or {}).get("name") or "").strip()
    if not name:
        raise ValueError(f"project.name missing in {pyproject}")
    return name


def _artifact_matches(path: Path, want_norm: str) -> bool:
    if path.name.endswith((".tar.gz", ".zip")):
        m = _SDIST_RE.match(path.name)
        return bool(m and normalize_dist_name(m.group("name")) == want_norm)
    if path.suffix == ".whl":
        stem = path.name[: -len(".whl")]
        parts = stem.split("-")
        return len(parts) >= 2 and normalize_dist_name(parts[0]) == want_norm
    return False


def _find_artifacts(wheels_dir: Path, dist_name: str) -> list[Path]:
    want = normalize_dist_name(dist_name)
    found = [p for p in sorted(wheels_dir.iterdir()) if p.is_file() and _artifact_matches(p, want)]
    return found


def _unpack_sdist_root(archive: Path, dest: Path) -> Path:
    """Unpack sdist into dest; return the single top-level project directory."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest, filter="data")
    elif archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        raise RuntimeError(f"unsupported sdist: {archive}")
    roots = [p for p in dest.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"{archive.name}: expected one top-level directory, got {len(roots)}")
    return roots[0]


def _build_local_package(package_dir: Path, wheelhouse: Path) -> str:
    """Stage blueprints in a temp tree, build sdist+wheel into wheelhouse. Returns dist name."""
    package_dir = package_dir.resolve()
    if not (package_dir / "pyproject.toml").is_file():
        raise FileNotFoundError(f"not a package dir: {package_dir}")
    dist_name = read_project_name(package_dir)
    extension_root = package_dir.parent
    src_blueprints = extension_root / "blueprints"
    has_blueprints = src_blueprints.is_dir() and any(src_blueprints.glob("*.json"))

    with tempfile.TemporaryDirectory(prefix="handlers-prepare-") as raw:
        work = Path(raw) / extension_root.name
        dest_pkg = work / "package"
        shutil.copytree(
            package_dir,
            dest_pkg,
            ignore=shutil.ignore_patterns(
                "*.egg-info", "build", "dist", "__pycache__", ".pytest_cache"
            ),
        )
        if has_blueprints:
            shutil.copytree(src_blueprints, work / "blueprints")
            staged = stage_extension_blueprints(extension_root=work)
            if staged:
                print(f"  staged blueprints -> {staged}")
        print(f"build {dist_name} from {package_dir}")
        subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(wheelhouse), str(dest_pkg)],
            check=True,
        )
    return dist_name


def _copy_artifacts_for_packages(
    artifacts_dir: Path, wheelhouse: Path, packages: list[str]
) -> None:
    for dist_name in packages:
        matches = _find_artifacts(artifacts_dir, dist_name)
        if not matches:
            raise RuntimeError(
                f"no artifact for {dist_name!r} under {artifacts_dir}"
            )
        for src in matches:
            dest = wheelhouse / src.name
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                print(f"  keep existing {dest.name}")
                continue
            print(f"  copy {src.name} -> wheelhouse/")
            shutil.copy2(src, dest)


def _preferred_sdist(wheelhouse: Path, dist_name: str) -> Path | None:
    sdists = [
        p
        for p in _find_artifacts(wheelhouse, dist_name)
        if p.name.endswith((".tar.gz", ".zip"))
    ]
    return sdists[0] if sdists else None


def _merge_handlers_configs(primary_path: Path, extra_paths: list[Path]) -> None:
    """Merge ``handlers`` maps from extra extension configs into the peer zip."""
    merged = json.loads(primary_path.read_text(encoding="utf-8"))
    handlers = dict(merged.get("handlers") or {})
    for extra_path in extra_paths:
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        handlers.update(extra.get("handlers") or {})
    merged = {"handlers": handlers}
    primary_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def extract_assets(wheelhouse: Path, assets_dir: Path, packages: list[str]) -> None:
    """Pull lambda_router.py / handlers_config.json from sdists into assets_dir."""
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True)
    extras = assets_dir / "extras"
    primary_router_set = False
    primary_config_set = False
    extra_config_paths: list[Path] = []

    for dist_name in packages:
        sdist = _preferred_sdist(wheelhouse, dist_name)
        if sdist is None:
            print(
                f"WARNING: no sdist for {dist_name}; skipping asset extract "
                f"(router/config may be missing)",
                file=sys.stderr,
            )
            continue
        with tempfile.TemporaryDirectory(prefix="handlers-assets-") as raw:
            root = _unpack_sdist_root(sdist, Path(raw))
            router = root / "lambda_router.py"
            config = root / "handlers_config.json"
            norm = normalize_dist_name(dist_name)
            if router.is_file() and not primary_router_set:
                shutil.copy2(router, assets_dir / "lambda_router.py")
                primary_router_set = True
                print(f"  primary lambda_router.py from {dist_name}")
            if config.is_file():
                if not primary_config_set:
                    shutil.copy2(config, assets_dir / "handlers_config.json")
                    primary_config_set = True
                    print(f"  primary handlers_config.json from {dist_name}")
                else:
                    dest = extras / norm
                    dest.mkdir(parents=True, exist_ok=True)
                    extra_config = dest / "handlers_config.json"
                    shutil.copy2(config, extra_config)
                    extra_config_paths.append(extra_config)
                    print(f"  extra handlers_config.json -> extras/{norm}/")

    primary_config = assets_dir / "handlers_config.json"
    if extra_config_paths:
        _merge_handlers_configs(primary_config, extra_config_paths)
        print(
            f"  merged handlers_config.json from {1 + len(extra_config_paths)} packages"
        )

    if not (assets_dir / "lambda_router.py").is_file():
        raise RuntimeError(
            "no lambda_router.py found in any sdist; ensure MANIFEST.in includes it"
        )
    if not (assets_dir / "handlers_config.json").is_file():
        raise RuntimeError(
            "no handlers_config.json found in any sdist; ensure MANIFEST.in includes it"
        )


LARGE_EXTRA = "large-dependencies"


def _extras_from_pyproject_text(text: str) -> set[str] | None:
    try:
        data = tomllib.loads(text)
    except Exception:
        return None
    optional = (data.get("project") or {}).get("optional-dependencies") or {}
    return {str(name).strip() for name in optional if str(name).strip()}


def _extras_from_pkg_info_text(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        if line.lower().startswith("provides-extra:"):
            extra = line.split(":", 1)[1].strip()
            if extra:
                found.add(extra)
    return found


def extras_declared_in_artifact(path: Path) -> set[str] | None:
    """Return extras named in a wheel/sdist, or None if metadata cannot be read."""
    name = path.name
    if name.endswith(".whl"):
        try:
            with zipfile.ZipFile(path) as zf:
                meta_names = [
                    n
                    for n in zf.namelist()
                    if n.endswith(".dist-info/METADATA") and n.count("/") == 1
                ]
                if not meta_names:
                    return None
                text = zf.read(meta_names[0]).decode("utf-8", errors="replace")
            return _extras_from_pkg_info_text(text)
        except (OSError, zipfile.BadZipFile):
            return None
    if name.endswith(".tar.gz") or name.endswith(".zip"):
        try:
            if name.endswith(".tar.gz"):
                texts = {}
                with tarfile.open(path, "r:gz") as tf:
                    for member in tf.getmembers():
                        leaf = member.name.rsplit("/", 1)[-1]
                        if (
                            not member.isfile()
                            or member.name.count("/") != 1
                            or leaf not in {"pyproject.toml", "PKG-INFO"}
                        ):
                            continue
                        handle = tf.extractfile(member)
                        if handle is None:
                            continue
                        texts[leaf] = handle.read().decode("utf-8", errors="replace")
            else:
                with zipfile.ZipFile(path) as zf:
                    texts = {
                        n.rsplit("/", 1)[-1]: zf.read(n).decode("utf-8", errors="replace")
                        for n in zf.namelist()
                        if n.count("/") == 1
                        and n.rsplit("/", 1)[-1] in {"pyproject.toml", "PKG-INFO"}
                    }
        except (OSError, tarfile.TarError, zipfile.BadZipFile):
            return None
        if "pyproject.toml" in texts:
            parsed = _extras_from_pyproject_text(texts["pyproject.toml"])
            if parsed is not None:
                return parsed
        if "PKG-INFO" in texts:
            return _extras_from_pkg_info_text(texts["PKG-INFO"])
        return None
    return None


def package_declares_extra(
    wheelhouse: Path, dist_name: str, extra: str = LARGE_EXTRA
) -> bool | None:
    """True/False when metadata is readable; None if artifacts cannot be inspected."""
    artifacts = _find_artifacts(wheelhouse, dist_name)
    if not artifacts:
        return None
    want = extra.strip().lower()
    saw_metadata = False
    for path in artifacts:
        declared = extras_declared_in_artifact(path)
        if declared is None:
            continue
        saw_metadata = True
        if any(name.lower() == want for name in declared):
            return True
    if saw_metadata:
        return False
    return None


def _artifact_version(path: Path) -> str | None:
    """Best-effort version from a wheel or sdist filename."""
    if path.suffix == ".whl":
        parts = path.name[: -len(".whl")].split("-")
        if len(parts) >= 2:
            return parts[1]
        return None
    m = _SDIST_RE.match(path.name)
    return m.group("version") if m else None


def version_in_wheelhouse(wheelhouse: Path, dist_name: str) -> str | None:
    """Pick the highest version for ``dist_name`` already present in wheelhouse."""
    versions = [
        v
        for p in _find_artifacts(wheelhouse, dist_name)
        if (v := _artifact_version(p))
    ]
    if not versions:
        return None
    try:
        from packaging.version import Version

        return str(max(versions, key=Version))
    except Exception:
        return sorted(versions)[-1]


def pin_specs(
    packages: list[str],
    wheelhouse: Path | None = None,
    *,
    extra: str | None = None,
) -> list[str]:
    """Return pip specs, pinning ``==version`` when that dist is already in wheelhouse.

    Avoids CodeArtifact older builds (e.g. 0.0.4) fighting a local monorepo wheel
    (0.0.5) during ``pip download name[large-dependencies]``.
    """
    out: list[str] = []
    for name in packages:
        name = name.strip()
        if not name:
            continue
        # Strip a caller-provided extra so we can re-apply cleanly.
        base = name
        had_extra = None
        if "[" in name and name.endswith("]"):
            base, rest = name.split("[", 1)
            had_extra = rest[:-1]
            base = base.strip()
        use_extra = extra if extra is not None else had_extra
        spec = f"{base}[{use_extra}]" if use_extra else base
        ver = version_in_wheelhouse(wheelhouse, base) if wheelhouse is not None else None
        if ver:
            spec = f"{spec}=={ver}"
        out.append(spec)
    return out


def large_extra_specs(
    packages: list[str], wheelhouse: Path | None = None
) -> list[str]:
    """Return ``name[large-dependencies]==ver`` specs for packages that declare the extra.

    ``renglo-lib`` and most handler dists have no ``[large-dependencies]`` extra.
    Pip download of ``name[extra]==ver`` is strict in CI and fails for those pins.
    Unreadable artifacts are still requested so a missing extra is a pip error
    rather than a silent skip of the only heavy package.
    """
    names: list[str] = []
    skipped: list[str] = []
    for name in packages:
        base = name.strip()
        if not base:
            continue
        if "[" in base and base.endswith("]"):
            base = base.split("[", 1)[0].strip()
        if wheelhouse is not None and package_declares_extra(wheelhouse, base) is False:
            skipped.append(base)
            continue
        names.append(base)
    for base in skipped:
        print(
            f"skip [{LARGE_EXTRA}] for {base} (extra not declared in wheelhouse artifact)",
            file=sys.stderr,
        )
    return pin_specs(names, wheelhouse, extra=LARGE_EXTRA)


def download_deps(
    wheelhouse: Path,
    packages: list[str],
    *,
    platform: str = "manylinux2014_x86_64",
    python_version: str = "3.12",
    strict: bool = False,
    extra_index_urls: list[str] | None = None,
    wheels_only: bool = False,
) -> None:
    """Download transitive deps into wheelhouse so Docker can use --no-index.

    Default pass (``wheels_only=False``): host-native download with
    ``--prefer-binary``. Resolves BOM sdists already in the wheelhouse and pulls
    transitive deps (boto3, Flask, …). Peer/handler CI runs on linux/amd64,
    matching Lambda.

    ``wheels_only=True`` (ECS ``[large-dependencies]``): cross-platform
    ``--only-binary=:all:`` with ``--platform`` / ``--python-version`` tags.
    Pip requires wheels-only when platform pins are set and deps are resolved.

    When ``strict`` is True, any pip failure raises instead of warning and
    continuing with an incomplete wheelhouse.

    ``extra_index_urls`` (e.g. PyPI) helps when CodeArtifact does not mirror
    public wheels (boto3, numpy, tensorflow, …).
    """
    if not packages:
        return
    mode = "cross-platform wheels" if wheels_only else "host-native"
    print(
        f"pip download deps for: {', '.join(packages)} "
        f"({mode}, strict={strict})"
    )
    base = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(wheelhouse),
        "--find-links",
        str(wheelhouse),
    ]
    if wheels_only:
        abi = f"cp{python_version.replace('.', '')}"
        base.extend(
            [
                "--platform",
                platform,
                "--python-version",
                python_version,
                "--implementation",
                "cp",
                "--abi",
                abi,
                "--only-binary=:all:",
            ]
        )
    else:
        base.append("--prefer-binary")
    for url in extra_index_urls or []:
        base.extend(["--extra-index-url", url])
    try:
        subprocess.run([*base, *packages], check=True)
        return
    except subprocess.CalledProcessError:
        print(
            "WARNING: bulk pip download failed; trying per-package "
            "(private deps may be missing until CodeArtifact login)",
            file=sys.stderr,
        )
    failures: list[str] = []
    for pkg in packages:
        try:
            subprocess.run([*base, pkg], check=True)
        except subprocess.CalledProcessError as exc:
            failures.append(pkg)
            print(
                f"WARNING: pip download failed for {pkg!r} (exit {exc.returncode}); "
                f"ensure the dist and its deps are already in the wheelhouse",
                file=sys.stderr,
            )
    if failures and strict:
        raise RuntimeError(
            "pip download failed for required specs: "
            + ", ".join(failures)
            + ". Clean the wheelhouse and/or pin local package versions."
        )


def prepare(
    *,
    out_dir: Path,
    from_monorepo: list[Path],
    from_artifacts: Path | None,
    packages: list[str] | None,
    skip_deps: bool,
    with_large_deps: bool = False,
) -> list[str]:
    out_dir = out_dir.resolve()
    wheelhouse = out_dir / "wheelhouse"
    assets_dir = out_dir / "handlers-assets"
    wheelhouse.mkdir(parents=True, exist_ok=True)

    ordered: list[str] = []
    for pkg_dir in from_monorepo:
        name = _build_local_package(pkg_dir, wheelhouse)
        ordered.append(name)

    if packages:
        # Explicit order wins when provided; monorepo names must be a subset or match.
        for name in packages:
            if name not in ordered and normalize_dist_name(name) not in {
                normalize_dist_name(n) for n in ordered
            }:
                ordered.append(name)
        # Reorder: use --packages as install order when given
        by_norm = {normalize_dist_name(n): n for n in ordered}
        reordered: list[str] = []
        for name in packages:
            key = normalize_dist_name(name)
            reordered.append(by_norm.get(key, name))
        for name in ordered:
            if normalize_dist_name(name) not in {normalize_dist_name(n) for n in reordered}:
                reordered.append(name)
        ordered = reordered

    if from_artifacts is not None:
        if not ordered and not packages:
            raise ValueError("--from-artifacts requires --packages (install order)")
        target_names = packages or ordered
        _copy_artifacts_for_packages(from_artifacts, wheelhouse, target_names)
        if not ordered:
            ordered = list(target_names)

    if not ordered:
        raise ValueError("no packages to prepare; pass --from-monorepo and/or --packages")

    if not skip_deps:
        # renglo-lib and extension sdists live in the wheelhouse; pull transitive
        # deps (boto3, Flask, …) from PyPI/CodeArtifact for offline Docker install.
        download_deps(
            wheelhouse,
            pin_specs(ordered, wheelhouse),
            strict=True,
            extra_index_urls=["https://pypi.org/simple"],
        )
        if with_large_deps:
            extras = large_extra_specs(ordered, wheelhouse)
            if extras:
                print(f"pip download [{LARGE_EXTRA}] for: {', '.join(extras)}")
                # CodeArtifact often lags public ML wheels (numpy>=2.3, tensorflow, …).
                download_deps(
                    wheelhouse,
                    extras,
                    strict=True,
                    extra_index_urls=["https://pypi.org/simple"],
                    wheels_only=True,
                )
            else:
                print(
                    f"no packages declare [{LARGE_EXTRA}]; skipping extra pip download",
                    file=sys.stderr,
                )

    extract_assets(wheelhouse, assets_dir, ordered)

    meta = {
        "packages": ordered,
        "with_large_deps": with_large_deps,
        "wheelhouse": str(wheelhouse),
        "assets": str(assets_dir),
    }
    (out_dir / "prepare_manifest.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare handlers wheelhouse/ + handlers-assets/ for Lambda builds"
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory (creates wheelhouse/ and handlers-assets/)",
    )
    parser.add_argument(
        "--from-monorepo",
        default="",
        help="Comma-separated package/ dirs to build (python -m build)",
    )
    parser.add_argument(
        "--from-artifacts",
        default="",
        help="Directory of already-downloaded sdists/wheels to copy into wheelhouse",
    )
    parser.add_argument(
        "--packages",
        default="",
        help="Ordered dist names (PyPI names). Required with --from-artifacts alone.",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Do not pip download transitive deps into the wheelhouse",
    )
    parser.add_argument(
        "--with-large-deps",
        action="store_true",
        help=(
            f"Also pip download name[{LARGE_EXTRA}] for each package "
            "(ECS / --large image wheelhouse)"
        ),
    )
    args = parser.parse_args()

    monorepo = [
        Path(p.strip())
        for p in args.from_monorepo.split(",")
        if p.strip()
    ]
    artifacts = Path(args.from_artifacts) if args.from_artifacts.strip() else None
    packages = [
        p.strip() for p in args.packages.split(",") if p.strip()
    ] or None

    if not monorepo and artifacts is None:
        print(
            "ERROR: pass --from-monorepo and/or --from-artifacts",
            file=sys.stderr,
        )
        return 1
    if artifacts is not None and not artifacts.is_dir():
        print(f"ERROR: artifacts directory not found: {artifacts}", file=sys.stderr)
        return 1
    for pkg in monorepo:
        if not pkg.is_dir():
            print(f"ERROR: package dir not found: {pkg}", file=sys.stderr)
            return 1
    if args.with_large_deps and args.skip_deps:
        print(
            "ERROR: --with-large-deps requires dependency download; omit --skip-deps",
            file=sys.stderr,
        )
        return 1

    try:
        ordered = prepare(
            out_dir=Path(args.out),
            from_monorepo=monorepo,
            from_artifacts=artifacts,
            packages=packages,
            skip_deps=args.skip_deps,
            with_large_deps=args.with_large_deps,
        )
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"prepared {len(ordered)} package(s): {', '.join(ordered)}")
    if args.with_large_deps:
        print(f"  large deps: [{LARGE_EXTRA}] downloaded into wheelhouse")
    print(f"  wheelhouse: {Path(args.out).resolve() / 'wheelhouse'}")
    print(f"  assets:     {Path(args.out).resolve() / 'handlers-assets'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
