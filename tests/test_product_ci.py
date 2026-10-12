from __future__ import annotations

import json
from pathlib import Path

from unittest.mock import patch

from renglo.doctor import run_doctor
from renglo.product_workflows import cmd_extension_check, cmd_extension_sync, cmd_wl_sync
from renglo_ops.product_ci.catalog import (
    EXTENSION_WORKFLOW,
    IssueKind,
    diff_extension,
    diff_wl,
    sync_extensions,
    sync_wl_repos,
    templates_root,
)


def _extension(root: Path, name: str = "widget") -> Path:
    repo = root / "extensions" / name
    (repo / "package").mkdir(parents=True)
    (repo / "ui").mkdir()
    (repo / "package" / "pyproject.toml").write_text(
        '[project]\nname = "widget"\nversion = "0.0.1"\n\n[tool.renglo]\nplatform = "0.1.0"\n',
        encoding="utf-8",
    )
    (repo / "ui" / "package.json").write_text(
        json.dumps({"name": "@acme/widget", "version": "0.0.1", "renglo": {"platform": "0.1.0"}}),
        encoding="utf-8",
    )
    return repo


def _wl(root: Path) -> Path:
    repo = root / "dev" / "acme-wl"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "acme-wl"',
                'version = "0.0.1"',
                "",
                "[tool.setuptools.package-data]",
                'wl = ["locales/*.json", "assets/*"]',
                "",
                "[tool.renglo]",
                'platform = "0.1.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        json.dumps(
            {
                "name": "@acme/wl",
                "version": "0.0.1",
                "renglo": {"platform": "0.1.0"},
                "files": ["locales", "assets"],
            }
        ),
        encoding="utf-8",
    )
    return repo


def test_extension_template_matches_sample() -> None:
    sample = Path(__file__).resolve().parents[1] / "samples" / "publish-extension.yml"
    packaged = templates_root() / "extension" / EXTENSION_WORKFLOW
    assert packaged.read_text(encoding="utf-8") == sample.read_text(encoding="utf-8")


def test_extension_sync_replaces_old_workflow_name(tmp_path: Path) -> None:
    repo = _extension(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "publish.yml").write_text("name: old\n", encoding="utf-8")
    issues = diff_extension(repo)
    kinds = {issue.kind for issue in issues}
    assert IssueKind.MISSING in kinds
    assert IssueKind.STALE in kinds
    assert any(issue.rel == EXTENSION_WORKFLOW for issue in issues)
    written = sync_extensions(tmp_path)
    assert (repo / EXTENSION_WORKFLOW).is_file()
    assert not (repo / ".github" / "workflows" / "publish.yml").is_file()
    assert any(action == "remove" for _repo, action, _rel in written)
    remaining = [issue for issue in diff_extension(repo) if issue.kind is not IssueKind.CONFIG]
    assert remaining == []


def test_extension_config_requires_platform(tmp_path: Path) -> None:
    repo = _extension(tmp_path)
    (repo / "ui" / "package.json").write_text(
        json.dumps({"name": "@acme/widget", "version": "0.0.1"}),
        encoding="utf-8",
    )
    issues = [issue for issue in diff_extension(repo) if issue.kind is IssueKind.CONFIG]
    assert any("renglo.platform" in issue.detail and issue.rel == "ui/package.json" for issue in issues)


def test_wl_sync_roundtrip(tmp_path: Path) -> None:
    repo = _wl(tmp_path)
    assert any(issue.kind is IssueKind.MISSING for issue in diff_wl(repo))
    sync_wl_repos(tmp_path)
    remaining = diff_wl(repo)
    assert remaining == []
    text = (repo / "wl_build.py").read_text(encoding="utf-8")
    assert "_DATA_FILES" not in text
    assert sync_wl_repos(tmp_path) == []


def test_doctor_names_the_repo_folder(tmp_path: Path, capsys) -> None:
    repo = _extension(tmp_path, "widget")
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "publish.yml").write_text("name: old\n", encoding="utf-8")
    with patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")):
        checks = run_doctor(tmp_path)
    ext = next(check for check in checks if check.label == "Extension publish workflows")
    assert "widget/.github/workflows/publish-extension.yml" in ext.detail
    assert "widget/.github/workflows/publish.yml" in ext.detail
    assert "renglo extension workflows sync" in ext.detail
    wl = next(check for check in checks if check.label == "White-label repo")
    assert "dev/" in wl.detail

    code = cmd_extension_sync(tmp_path, dry_run=False)
    assert code == 0
    out = capsys.readouterr().out
    assert "Wrote widget/.github/workflows/publish-extension.yml" in out
    assert "Removed widget/.github/workflows/publish.yml" in out
    assert cmd_extension_check(tmp_path) == 0


def test_wl_sync_command_prefixes_repo(tmp_path: Path, capsys) -> None:
    _wl(tmp_path)
    assert cmd_wl_sync(tmp_path, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "Wrote acme-wl/.github/workflows/publish-python.yml" in out
    assert "Wrote acme-wl/wl_build.py" in out
