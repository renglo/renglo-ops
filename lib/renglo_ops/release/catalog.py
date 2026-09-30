"""Read a tenant catalog from renglo.yaml, projecting the legacy deploy-targets shape."""

from __future__ import annotations

from pathlib import Path

import yaml

from renglo_ops.model.legacy import deploy_targets_dict
from renglo_ops.model.tenant import load_tenant


def catalog_data(root: Path) -> dict | None:
    renglo = root / "renglo.yaml"
    if renglo.is_file():
        return deploy_targets_dict(load_tenant(renglo))
    legacy = root / "deploy_targets.yml"
    if legacy.is_file():
        data = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else None
    return None


def legacy_text(root: Path) -> str | None:
    data = catalog_data(root)
    if data is None:
        return None
    return yaml.safe_dump(data, sort_keys=False)
