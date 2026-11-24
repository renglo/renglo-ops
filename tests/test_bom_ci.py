from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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


def test_apply_console_lock_pins_exact_versions(tmp_path: Path) -> None:
    script = templates_root() / ".github/scripts/apply_console_lock.py"
    bom = tmp_path / "console_bom.json"
    bom.write_text(
        json.dumps({"npm": {"@renglo/console": "1.2.3", "@skbrk/ols": "0.0.6"}}),
        encoding="utf-8",
    )
    lock = tmp_path / "lock.json"
    lock.write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    console = tmp_path / "console"
    console.mkdir()
    (console / "package.json").write_text(
        json.dumps(
            {
                "name": "@renglo/console",
                "version": "1.2.3",
                "dependencies": {"react": "^18.0.0"},
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(script), str(bom), str(lock), str(console)],
        check=True,
    )
    package = json.loads((console / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["@skbrk/ols"] == "0.0.6"
    assert package["dependencies"]["react"] == "^18.0.0"
    assert (console / "package-lock.json").read_text(encoding="utf-8") == '{"lockfileVersion": 3}\n'


def test_apply_console_lock_rejects_a_different_host_version(tmp_path: Path) -> None:
    script = templates_root() / ".github/scripts/apply_console_lock.py"
    bom = tmp_path / "console_bom.json"
    bom.write_text(json.dumps({"npm": {"@renglo/console": "9.9.9"}}), encoding="utf-8")
    lock = tmp_path / "lock.json"
    lock.write_text("{}\n", encoding="utf-8")
    console = tmp_path / "console"
    console.mkdir()
    (console / "package.json").write_text(
        json.dumps({"name": "@renglo/console", "version": "1.2.3", "dependencies": {}}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(bom), str(lock), str(console)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "9.9.9" in completed.stderr
