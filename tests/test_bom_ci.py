from __future__ import annotations

from pathlib import Path

import subprocess

import pytest

from renglo_ops.bom_ci.catalog import (
    DriftKind,
    bom_ci_git_tracking_issues,
    diff_bom_ci,
    sync_bom_ci,
    templates_root,
)


def test_templates_shipped() -> None:
    root = templates_root()
    assert (root / ".github/workflows/deploy_peers.yml").is_file()
    assert (root / ".github/scripts/production_pins.py").is_file()


def test_diff_and_sync_roundtrip(tmp_path: Path) -> None:
    rows = diff_bom_ci(tmp_path)
    assert any(r.kind is DriftKind.MISSING for r in rows)
    written = sync_bom_ci(tmp_path)
    assert written
    rows_after = diff_bom_ci(tmp_path)
    assert all(r.kind is DriftKind.OK for r in rows_after)
    assert sync_bom_ci(tmp_path) == []


def test_git_tracking_detects_gitignore_scripts_pattern(tmp_path: Path) -> None:
    sync_bom_ci(tmp_path)
    (tmp_path / ".gitignore").write_text("scripts\n", encoding="utf-8")
    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        pytest.skip(f"git not available: {exc}")
    issues = bom_ci_git_tracking_issues(tmp_path)
    assert any("production_pins.py" in item and "gitignored" in item for item in issues)
