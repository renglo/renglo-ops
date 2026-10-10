"""Ship and compare the standard *-bom GitHub Actions tree."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

CANONICAL_REL_PATHS: tuple[str, ...] = (
    ".github/workflows/deploy.yml",
    ".github/workflows/deploy_console.yml",
    ".github/workflows/deploy_peers.yml",
    ".github/actions/setup-renglo-ops/action.yml",
    ".github/scripts/production_pins.py",
    ".github/scripts/staging_matrix.py",
)


class DriftKind(str, Enum):
    OK = "ok"
    MISSING = "missing"
    CHANGED = "changed"


@dataclass(frozen=True)
class DriftRow:
    rel_path: str
    kind: DriftKind


def templates_root() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").strip() + "\n"


def _template_path(rel: str) -> Path:
    return templates_root() / rel


def bom_ci_git_tracking_issues(bom_root: Path) -> list[str]:
    """Canonical BOM CI paths that exist on disk but are not in the git index."""
    root = bom_root.expanduser().resolve()
    if not (root / ".git").is_dir():
        return []
    issues: list[str] = []
    for rel in CANONICAL_REL_PATHS:
        dest = root / rel
        if not dest.is_file():
            continue
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
            capture_output=True,
        )
        if tracked.returncode == 0:
            continue
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", rel],
            capture_output=True,
        )
        if ignored.returncode == 0:
            issues.append(f"{rel} (gitignored — use /scripts not scripts in .gitignore)")
        else:
            issues.append(f"{rel} (not tracked — git add and commit)")
    return issues


def diff_bom_ci(bom_root: Path) -> list[DriftRow]:
    """Compare a BOM checkout to the canonical files bundled in renglo-ops."""
    root = bom_root.expanduser().resolve()
    rows: list[DriftRow] = []
    for rel in CANONICAL_REL_PATHS:
        dest = root / rel
        src = _template_path(rel)
        if not src.is_file():
            raise FileNotFoundError(f"canonical template missing in renglo-ops: {rel}")
        if not dest.is_file():
            rows.append(DriftRow(rel, DriftKind.MISSING))
            continue
        if _normalize(dest.read_text(encoding="utf-8")) != _normalize(src.read_text(encoding="utf-8")):
            rows.append(DriftRow(rel, DriftKind.CHANGED))
        else:
            rows.append(DriftRow(rel, DriftKind.OK))
    return rows


def sync_bom_ci(bom_root: Path, *, dry_run: bool = False) -> list[str]:
    """Copy canonical workflow files into a BOM repo. Returns paths written."""
    root = bom_root.expanduser().resolve()
    written: list[str] = []
    for rel in CANONICAL_REL_PATHS:
        src = _template_path(rel)
        dest = root / rel
        if not src.is_file():
            raise FileNotFoundError(f"canonical template missing in renglo-ops: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and _normalize(dest.read_text(encoding="utf-8")) == _normalize(
            src.read_text(encoding="utf-8")
        ):
            continue
        if dry_run:
            written.append(rel)
            continue
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(rel)
    return written
