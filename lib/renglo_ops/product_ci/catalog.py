"""Compare extension and white-label checkouts to the files bundled in renglo-ops.

Extension repos share one publish workflow. A white-label repo (``*-wl``)
shares two publish workflows plus ``wl_build.py``. Package manifests stay
in the product repo; this module only checks that the fields a release
needs are present.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

EXTENSION_WORKFLOW = ".github/workflows/publish-extension.yml"
STALE_EXTENSION_WORKFLOW = ".github/workflows/publish.yml"

WL_TEMPLATES: tuple[str, ...] = (
    ".github/workflows/publish-python.yml",
    ".github/workflows/publish-npm.yml",
    "wl_build.py",
)


class IssueKind(str, Enum):
    MISSING = "missing"
    CHANGED = "changed"
    STALE = "stale"
    CONFIG = "config"
    GIT = "git"


@dataclass(frozen=True)
class Issue:
    repo: Path
    rel: str
    kind: IssueKind
    detail: str = ""

    def display(self) -> str:
        path = f"{self.repo.name}/{self.rel}"
        if self.detail:
            return f"{path} ({self.detail})"
        return path


def templates_root() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").strip() + "\n"


def _template(kind: str, rel: str) -> Path:
    path = templates_root() / kind / rel
    if not path.is_file():
        raise FileNotFoundError(f"canonical template missing in renglo-ops: {kind}/{rel}")
    return path


def find_extension_repos(workspace: Path) -> list[Path]:
    root = workspace / "extensions"
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        has_python = (child / "package" / "pyproject.toml").is_file()
        has_npm = (child / "ui" / "package.json").is_file()
        if has_python or has_npm:
            found.append(child)
    return found


def find_wl_repos(workspace: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for base in (workspace / "dev", workspace):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), key=lambda path: path.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name != "wl" and not child.name.endswith("-wl"):
                continue
            resolved = child.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(child)
    return found


def diff_extensions(workspace: Path) -> list[Issue]:
    issues: list[Issue] = []
    for repo in find_extension_repos(workspace):
        issues.extend(diff_extension(repo))
    return issues


def diff_wl_repos(workspace: Path) -> list[Issue]:
    issues: list[Issue] = []
    for repo in find_wl_repos(workspace):
        issues.extend(diff_wl(repo))
    return issues


def diff_extension(repo: Path) -> list[Issue]:
    repo = repo.expanduser().resolve()
    issues: list[Issue] = []
    rel = EXTENSION_WORKFLOW
    dest = repo / rel
    template = _template("extension", rel)
    if not dest.is_file():
        issues.append(Issue(repo, rel, IssueKind.MISSING))
    elif _normalize(dest.read_text(encoding="utf-8")) != _normalize(template.read_text(encoding="utf-8")):
        issues.append(Issue(repo, rel, IssueKind.CHANGED))
    stale = repo / STALE_EXTENSION_WORKFLOW
    if stale.is_file():
        issues.append(
            Issue(repo, STALE_EXTENSION_WORKFLOW, IssueKind.STALE, "old name; sync removes it")
        )
    issues.extend(_extension_config_issues(repo))
    if dest.is_file():
        git = _git_detail(repo, rel)
        if git:
            issues.append(Issue(repo, rel, IssueKind.GIT, git))
    return issues


def diff_wl(repo: Path) -> list[Issue]:
    repo = repo.expanduser().resolve()
    issues: list[Issue] = []
    for rel in WL_TEMPLATES:
        dest = repo / rel
        template = _template("wl", rel)
        if not dest.is_file():
            issues.append(Issue(repo, rel, IssueKind.MISSING))
            continue
        if _normalize(dest.read_text(encoding="utf-8")) != _normalize(template.read_text(encoding="utf-8")):
            issues.append(Issue(repo, rel, IssueKind.CHANGED))
        git = _git_detail(repo, rel)
        if git:
            issues.append(Issue(repo, rel, IssueKind.GIT, git))
    issues.extend(_wl_config_issues(repo))
    return issues


def sync_extensions(workspace: Path, *, dry_run: bool = False) -> list[tuple[Path, str, str]]:
    """Copy the shared extension workflow.

    Each row is ``(repo, "write"|"remove", rel)``. Package configs are not rewritten.
    """
    changed: list[tuple[Path, str, str]] = []
    for repo in find_extension_repos(workspace):
        changed.extend(_sync_templates(repo, "extension", (EXTENSION_WORKFLOW,), dry_run=dry_run))
        stale = repo / STALE_EXTENSION_WORKFLOW
        if stale.is_file():
            changed.append((repo, "remove", STALE_EXTENSION_WORKFLOW))
            if not dry_run:
                stale.unlink()
    return changed


def sync_wl_repos(workspace: Path, *, dry_run: bool = False) -> list[tuple[Path, str, str]]:
    """Copy shared white-label workflows and wl_build.py. Does not rewrite package configs."""
    changed: list[tuple[Path, str, str]] = []
    for repo in find_wl_repos(workspace):
        changed.extend(_sync_templates(repo, "wl", WL_TEMPLATES, dry_run=dry_run))
    return changed


def _sync_templates(
    repo: Path, kind: str, rels: tuple[str, ...], *, dry_run: bool
) -> list[tuple[Path, str, str]]:
    written: list[tuple[Path, str, str]] = []
    for rel in rels:
        src = _template(kind, rel)
        dest = repo / rel
        desired = src.read_text(encoding="utf-8")
        if dest.is_file() and _normalize(dest.read_text(encoding="utf-8")) == _normalize(desired):
            continue
        written.append((repo, "write", rel))
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(desired, encoding="utf-8")
    return written


def summarize_issues(issues: list[Issue], *, sync_command: str) -> tuple[str, bool]:
    """Return ``(detail, failed)``. ``failed`` is true when a canonical file is not in git."""
    if not issues:
        return "", False
    counts: dict[IssueKind, int] = {}
    for issue in issues:
        counts[issue.kind] = counts.get(issue.kind, 0) + 1
    parts: list[str] = []
    for kind, singular, plural in (
        (IssueKind.MISSING, "missing", "missing"),
        (IssueKind.CHANGED, "out of date", "out of date"),
        (IssueKind.STALE, "stale", "stale"),
        (IssueKind.CONFIG, "package config", "package configs"),
        (IssueKind.GIT, "not in git", "not in git"),
    ):
        count = counts.get(kind, 0)
        if not count:
            continue
        label = singular if count == 1 else plural
        parts.append(f"{count} {label}")
    shown = [issue.display() for issue in issues]
    sample = ", ".join(shown[:4])
    if len(shown) > 4:
        sample += ", …"
    detail = f"{'; '.join(parts)} ({sample})"
    if sync_command and any(
        issue.kind in {IssueKind.MISSING, IssueKind.CHANGED, IssueKind.STALE} for issue in issues
    ):
        detail += f"; run {sync_command}"
    git_by_repo: dict[str, list[str]] = {}
    for issue in issues:
        if issue.kind is not IssueKind.GIT:
            continue
        git_by_repo.setdefault(issue.repo.name, []).append(issue.rel)
    hints = [_git_hint(name, rels) for name, rels in git_by_repo.items()]
    if hints:
        detail += "; " + "; ".join(hints)
    return detail, IssueKind.GIT in counts


def _git_hint(repo_name: str, rels: list[str]) -> str:
    if rels and all(rel.startswith(".github/") for rel in rels):
        target = ".github/"
    else:
        target = " ".join(rels)
    return f"in {repo_name}, git add {target} and commit"


def _git_detail(repo: Path, rel: str) -> str:
    if not (repo / ".git").is_dir():
        return ""
    if not (repo / rel).is_file():
        return ""
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", rel],
        capture_output=True,
    )
    if tracked.returncode == 0:
        return ""
    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", rel],
        capture_output=True,
    )
    if ignored.returncode == 0:
        return "gitignored"
    return "not tracked — git add and commit"


def _extension_config_issues(repo: Path) -> list[Issue]:
    issues: list[Issue] = []
    if (repo / "package").is_dir():
        rel = "package/pyproject.toml"
        require_blueprints = any((repo / "blueprints").glob("*.json")) if (repo / "blueprints").is_dir() else False
        issues.extend(
            _pyproject_issues(repo, repo / rel, rel, require_blueprints=require_blueprints)
        )
    if (repo / "ui").is_dir():
        rel = "ui/package.json"
        issues.extend(_package_json_issues(repo, repo / rel, rel, require_files=()))
    return issues


def _wl_config_issues(repo: Path) -> list[Issue]:
    """Package name, version, and platform. Membership lives in the BOM ``renglo.yaml``."""
    issues: list[Issue] = []
    issues.extend(_pyproject_issues(repo, repo / "pyproject.toml", "pyproject.toml"))
    issues.extend(
        _package_json_issues(repo, repo / "package.json", "package.json", require_files=())
    )
    return issues


def _pyproject_issues(
    repo: Path,
    path: Path,
    rel: str,
    *,
    require_blueprints: bool = False,
) -> list[Issue]:
    if not path.is_file():
        return [Issue(repo, rel, IssueKind.CONFIG, "missing")]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [Issue(repo, rel, IssueKind.CONFIG, f"unreadable ({exc.__class__.__name__})")]
    if not isinstance(data, dict):
        return [Issue(repo, rel, IssueKind.CONFIG, "not a table")]
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    issues: list[Issue] = []
    if not str(project.get("name") or "").strip():
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "missing [project] name"))
    if not str(project.get("version") or "").strip():
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "missing [project] version"))
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    renglo = tool.get("renglo") if isinstance(tool.get("renglo"), dict) else {}
    if not str(renglo.get("platform") or "").strip():
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "missing [tool.renglo] platform"))
    patterns = _package_data_patterns(tool)
    if require_blueprints and not any("blueprints/*.json" in pattern for pattern in patterns):
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "does not ship blueprints/*.json"))
    return issues


def _package_data_patterns(tool: dict) -> list[str]:
    setuptools = tool.get("setuptools") if isinstance(tool.get("setuptools"), dict) else {}
    package_data = setuptools.get("package-data")
    if not isinstance(package_data, dict):
        return []
    patterns: list[str] = []
    for value in package_data.values():
        if isinstance(value, list):
            patterns.extend(str(item) for item in value)
    return patterns


def _package_json_issues(
    repo: Path,
    path: Path,
    rel: str,
    *,
    require_files: tuple[str, ...],
) -> list[Issue]:
    if not path.is_file():
        return [Issue(repo, rel, IssueKind.CONFIG, "missing")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue(repo, rel, IssueKind.CONFIG, f"unreadable ({exc.__class__.__name__})")]
    if not isinstance(data, dict):
        return [Issue(repo, rel, IssueKind.CONFIG, "not an object")]
    issues: list[Issue] = []
    if not str(data.get("name") or "").strip():
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "missing name"))
    if not str(data.get("version") or "").strip():
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "missing version"))
    renglo = data.get("renglo") if isinstance(data.get("renglo"), dict) else {}
    if not str(renglo.get("platform") or "").strip():
        issues.append(Issue(repo, rel, IssueKind.CONFIG, "missing renglo.platform"))
    if require_files:
        listed = data.get("files")
        names = [str(item) for item in listed] if isinstance(listed, list) else []
        for required in require_files:
            if required not in names:
                issues.append(Issue(repo, rel, IssueKind.CONFIG, f"files does not include {required}"))
    return issues
