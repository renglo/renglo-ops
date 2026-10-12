"""Check and sync canonical extension and white-label files from renglo-ops."""

from __future__ import annotations

from pathlib import Path

from renglo_ops.model.local import workspace_root
from renglo_ops.product_ci.catalog import (
    IssueKind,
    diff_extensions,
    diff_wl_repos,
    find_extension_repos,
    find_wl_repos,
    sync_extensions,
    sync_wl_repos,
)


def cmd_extension_check(start: Path) -> int:
    workspace = workspace_root(start)
    repos = find_extension_repos(workspace)
    if not (workspace / "extensions").is_dir():
        print(f"No extensions/ directory under {workspace}")
        return 0
    if not repos:
        print(f"No extension checkouts under {workspace / 'extensions'}")
        return 0
    return _report(
        diff_extensions(workspace),
        ok=f"Extension publish workflows and package configs match renglo-ops ({len(repos)} repos)",
        sync_command="renglo extension workflows sync",
    )


def cmd_extension_sync(start: Path, *, dry_run: bool) -> int:
    workspace = workspace_root(start)
    changed = sync_extensions(workspace, dry_run=dry_run)
    _print_changed(changed, dry_run=dry_run, already="Extension publish workflows already match renglo-ops")
    if dry_run:
        return 0
    return _report_remaining(diff_extensions(workspace))


def cmd_wl_check(start: Path) -> int:
    workspace = workspace_root(start)
    repos = find_wl_repos(workspace)
    if not repos:
        print(f"No *-wl checkout under {workspace / 'dev'}")
        return 1
    names = ", ".join(repo.name for repo in repos)
    return _report(
        diff_wl_repos(workspace),
        ok=f"White-label workflows and package configs match renglo-ops ({names})",
        sync_command="renglo wl sync",
    )


def cmd_wl_sync(start: Path, *, dry_run: bool) -> int:
    workspace = workspace_root(start)
    if not find_wl_repos(workspace):
        print(f"No *-wl checkout under {workspace / 'dev'}")
        return 1
    changed = sync_wl_repos(workspace, dry_run=dry_run)
    _print_changed(changed, dry_run=dry_run, already="White-label workflows already match renglo-ops")
    if dry_run:
        return 0
    return _report_remaining(diff_wl_repos(workspace))


def _print_changed(changed: list[tuple[Path, str, str]], *, dry_run: bool, already: str) -> None:
    if not changed:
        print(already)
        return
    for repo, action, rel in changed:
        if action == "remove":
            label = "Would remove" if dry_run else "Removed"
        else:
            label = "Would write" if dry_run else "Wrote"
        print(f"{label} {repo.name}/{rel}")


def _report(issues: list, *, ok: str, sync_command: str) -> int:
    if not issues:
        print(ok)
        return 0
    print("Drift:")
    for issue in issues:
        print(f"  {issue.kind.value:<8} {issue.display()}")
    fixable = any(
        issue.kind in {IssueKind.MISSING, IssueKind.CHANGED, IssueKind.STALE} for issue in issues
    )
    if fixable:
        print(f"Run: {sync_command}")
    git = [issue for issue in issues if issue.kind is IssueKind.GIT]
    if git:
        names = []
        for issue in git:
            if issue.repo.name not in names:
                names.append(issue.repo.name)
        listed = ", ".join(names)
        print(f"Then: in {listed}, git add the listed files and commit")
    return 1


def _report_remaining(issues: list) -> int:
    """After a sync, workflow copies are current. Config and git issues remain."""
    issues = [
        issue
        for issue in issues
        if issue.kind not in {IssueKind.MISSING, IssueKind.CHANGED, IssueKind.STALE}
    ]
    if not issues:
        return 0
    print("Still to fix:")
    for issue in issues:
        print(f"  {issue.kind.value:<8} {issue.display()}")
    git = [issue for issue in issues if issue.kind is IssueKind.GIT]
    if git:
        names = []
        for issue in git:
            if issue.repo.name not in names:
                names.append(issue.repo.name)
        print(f"Then: in {', '.join(names)}, git add the listed files and commit")
    return 1
