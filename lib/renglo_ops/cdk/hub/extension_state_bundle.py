"""Build and emit the extension state manifest bundled with CDK synth output.

The manifest is extension-agnostic at bootstrap runtime: only the JSON
in cdk/output is consumed post-deploy.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from stack_names import stack_b_id
from extension_loader import (
    bundle_extension_infra,
    load_extension_config,
    load_extension_manifest,
    resolve_extension_folder,
)

_DEFAULT_BLUEPRINTS_DIR = "extension-blueprints"


def _load_cdk_hooks(
    extension_folder: Path,
    *,
    workspace_root: Path | None = None,
    extension_path: str = "",
) -> dict[str, Any]:
    hooks_path = extension_folder / "installer" / "cdk_hooks.json"
    if hooks_path.is_file():
        return json.loads(hooks_path.read_text(encoding="utf-8"))
    if workspace_root and extension_path.strip():
        hooks_path = workspace_root / extension_path.strip() / "installer" / "cdk_hooks.json"
        if hooks_path.is_file():
            return json.loads(hooks_path.read_text(encoding="utf-8"))
    return {}


def _resolve_blueprints_source(
    extension_folder: Path,
    cdk_hooks: dict[str, Any],
    *,
    workspace_root: Path | None = None,
    extension_path: str = "",
) -> Path | None:
    upload_cfg = cdk_hooks.get("upload_blueprints") or {}
    rel = str(upload_cfg.get("blueprints_dir", "")).strip()
    if rel:
        candidate = (extension_folder / "installer" / rel).resolve()
        if candidate.is_dir():
            return candidate
    for candidate in (extension_folder / "blueprints", extension_folder / "installer" / "blueprints"):
        if candidate.is_dir():
            return candidate
    if workspace_root and extension_path.strip():
        ext_root = workspace_root / extension_path.strip()
        for candidate in (ext_root / "blueprints", ext_root / "installer" / "blueprints"):
            if candidate.is_dir():
                return candidate
    return None


def build_extension_state_manifest(
    *,
    env_name: str,
    extension_path: str,
    cdk_extension: dict[str, Any],
    extension_config: dict[str, Any],
) -> dict[str, Any]:
    state_cfg = cdk_extension.get("state") or {}

    runtime_keys: list[str] = list(state_cfg.get("runtime_stack_outputs") or [])
    for bucket in cdk_extension.get("s3_buckets", []):
        output_var = str(bucket.get("output_var", "")).strip()
        if output_var and output_var not in runtime_keys:
            runtime_keys.append(output_var)
    if "EXTERNAL_HANDLERS" not in runtime_keys:
        runtime_keys.append("EXTERNAL_HANDLERS")

    inventory_keys: list[str] = list(state_cfg.get("inventory_stack_outputs") or [])

    secret_keys: list[str] = list(state_cfg.get("secret_keys") or [])
    if not secret_keys:
        secrets_block = extension_config.get("SECRETS") or {}
        if isinstance(secrets_block, dict):
            secret_keys = [str(k) for k in secrets_block if not str(k).startswith("_")]

    return {
        "schema_version": 1,
        "env_name": env_name,
        "extension_path": extension_path,
        "extension_stack": stack_b_id(env_name),
        "runtime_stack_outputs": runtime_keys,
        "inventory_stack_outputs": inventory_keys,
        "secret_keys": secret_keys,
        "blueprints_dir": str(state_cfg.get("blueprints_dir") or _DEFAULT_BLUEPRINTS_DIR),
    }


def emit_extension_state_bundle(
    output_dir: Path,
    *,
    env_name: str,
    extension_path: str,
    workspace_root: Path | None = None,
) -> Path | None:
    """Write extension-state.json and copy extension blueprints into output_dir."""
    if not extension_path.strip():
        return None

    extension_folder = resolve_extension_folder(extension_path, deploy_root=output_dir)
    cdk_extension = load_extension_manifest(extension_folder)
    extension_config = load_extension_config(extension_folder)
    cdk_hooks = _load_cdk_hooks(extension_folder, workspace_root=workspace_root, extension_path=extension_path)

    manifest = build_extension_state_manifest(
        env_name=env_name,
        extension_path=extension_path.strip(),
        cdk_extension=cdk_extension,
        extension_config=extension_config,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "extension-state.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    blueprints_source = _resolve_blueprints_source(
        extension_folder,
        cdk_hooks,
        workspace_root=workspace_root,
        extension_path=extension_path,
    )
    if blueprints_source is not None:
        dest = output_dir / manifest["blueprints_dir"]
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(blueprints_source, dest)
        print(f"  Extension blueprints copied to {dest}")
    else:
        print(f"  [warn] No extension blueprints directory found under {extension_folder}")

    print(f"  Extension state manifest: {manifest_path}")
    return manifest_path
