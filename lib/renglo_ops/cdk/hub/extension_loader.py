"""Load extension CDK manifest and config from monorepo or bundled deploy output."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

BUNDLED_EXTENSION_DIR = "extension"
_BUNDLED_MANIFEST = Path("installer") / "infra" / "cdk_extension.json"


def _app_root() -> Path:
    return Path(__file__).resolve().parent


def _monorepo_root() -> Path:
    # extension_loader.py lives in launcher/cdk/ → parents[1] is infra-installer root
    return _app_root().parents[1]


def bundled_extension_folder(deploy_root: Path | None = None) -> Path | None:
    """Return ./extension when the self-contained deploy bundle is present."""
    root = deploy_root or _app_root()
    folder = root / BUNDLED_EXTENSION_DIR
    if (folder / _BUNDLED_MANIFEST).is_file():
        return folder
    return None


def resolve_extension_folder(extension_path: str, *, deploy_root: Path | None = None) -> Path:
    bundled = bundled_extension_folder(deploy_root)
    if bundled is not None:
        return bundled

    rel = extension_path.strip()
    if not rel:
        raise FileNotFoundError(
            "extension_path is not set in customer-config.json and no bundled "
            f"'{BUNDLED_EXTENSION_DIR}/' folder was found next to app.py"
        )

    folder = _monorepo_root() / rel
    if not folder.is_dir():
        raise FileNotFoundError(f"extension_path folder not found: {folder}")
    return folder


def load_extension_manifest(extension_folder: Path) -> dict[str, Any]:
    manifest_path = extension_folder / _BUNDLED_MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Extension CDK manifest not found: {manifest_path}\n"
            "Create installer/infra/cdk_extension.json in the extension repo."
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_extension_config(extension_folder: Path) -> dict[str, Any]:
    config_path = extension_folder / "installer" / "infra" / "extension_config.json"
    if not config_path.is_file():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def bundle_extension_infra(
    output_dir: Path,
    *,
    workspace_root: Path,
    extension_path: str,
) -> Path | None:
    """Copy extension installer/infra into output_dir/extension/ for self-contained deploy."""
    rel = extension_path.strip()
    if not rel:
        return None

    infra_src = workspace_root / rel / "installer" / "infra"
    if not infra_src.is_dir():
        raise FileNotFoundError(f"Extension infra directory not found: {infra_src}")

    bundle_root = output_dir / BUNDLED_EXTENSION_DIR
    infra_dest = bundle_root / "installer" / "infra"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    shutil.copytree(infra_src, infra_dest)

    meta = {"source_repo": rel}
    (bundle_root / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return bundle_root
