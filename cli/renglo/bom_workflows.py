"""Check and sync canonical BOM GitHub Actions files from renglo-ops."""

from __future__ import annotations

from pathlib import Path

from renglo_ops.bom_ci.catalog import (
    DriftKind,
    bom_ci_git_tracking_issues,
    diff_bom_ci,
    sync_bom_ci,
)
from renglo_ops.model.local import find_local, load_local
from renglo_ops.model.tenant import find_tenant_file


def resolve_bom_root(start: Path, explicit: str) -> Path:
    if explicit.strip():
        return Path(explicit).expanduser().resolve()
    tenant_path = find_tenant_file(start)
    if tenant_path is not None:
        return tenant_path.parent.resolve()
    local_path = find_local(start)
    if local_path is not None:
        local = load_local(local_path)
        candidate = (local_path.parent.parent / local.tenant).resolve()
        if candidate.is_dir():
            return candidate
    raise SystemExit(
        "Could not find a BOM repo. Pass --bom or run from a checkout with renglo.yaml."
    )


def cmd_check(bom_root: Path) -> int:
    rows = diff_bom_ci(bom_root)
    missing = [r.rel_path for r in rows if r.kind is DriftKind.MISSING]
    changed = [r.rel_path for r in rows if r.kind is DriftKind.CHANGED]
    git_issues = bom_ci_git_tracking_issues(bom_root)
    if not missing and not changed and not git_issues:
        print(f"BOM CI workflows match renglo-ops ({bom_root})")
        return 0
    print(f"BOM CI drift under {bom_root}:")
    for path in missing:
        print(f"  missing  {path}")
    for path in changed:
        print(f"  changed  {path}")
    for detail in git_issues:
        print(f"  git      {detail}")
    print("Run: renglo bom workflows sync")
    if git_issues:
        print("Then: git add .github/ && git commit (CI only sees tracked files)")
    return 1


def cmd_sync(bom_root: Path, *, dry_run: bool) -> int:
    written = sync_bom_ci(bom_root, dry_run=dry_run)
    if not written:
        print(f"BOM CI workflows already match renglo-ops ({bom_root})")
        return 0
    label = "Would write" if dry_run else "Wrote"
    for rel in written:
        print(f"{label} {rel}")
    if not dry_run:
        for detail in bom_ci_git_tracking_issues(bom_root):
            print(f"Note: {detail}")
    return 0
