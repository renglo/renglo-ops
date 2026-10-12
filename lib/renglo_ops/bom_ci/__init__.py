"""Canonical GitHub Actions layout for tenant BOM repositories."""

from renglo_ops.bom_ci.catalog import (
    CANONICAL_REL_PATHS,
    diff_bom_ci,
    sync_bom_ci,
    templates_root,
)

__all__ = [
    "CANONICAL_REL_PATHS",
    "diff_bom_ci",
    "sync_bom_ci",
    "templates_root",
]
