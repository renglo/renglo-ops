#!/usr/bin/env python3
"""Detect deploy_targets.yml changes and emit scope + filter JSON for matrix narrowing.

Outputs to stdout (and optional GITHUB_OUTPUT):
  scope=full|new_only
  filter_json=<path>   # written to a temp file when new_only

Rules:
  workflow_dispatch -> full
  push changing files other than deploy_targets.yml -> full (caller should skip this script)
  push only deploy_targets.yml -> new_only with delta filter
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

VALID_STAGES = ("staging", "production")


def _load_targets(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _stage_enabled(stage_cfg: Any) -> bool:
    if stage_cfg is True:
        return True
    if isinstance(stage_cfg, dict):
        return stage_cfg.get("enabled", True) is not False
    return False


def _enabled_pairs(data: dict) -> set[tuple[str, str]]:
    tenants_raw = data.get("tenants") or {}
    if not isinstance(tenants_raw, dict):
        return set()
    pairs: set[tuple[str, str]] = set()
    for tenant_key, tenant_cfg in tenants_raw.items():
        if not isinstance(tenant_cfg, dict):
            continue
        tenant = str(tenant_key).strip()
        stages = tenant_cfg.get("stages") or {}
        if not isinstance(stages, dict):
            continue
        for stage_name, stage_cfg in stages.items():
            stage = str(stage_name).strip().lower()
            if stage in VALID_STAGES and _stage_enabled(stage_cfg):
                pairs.add((tenant, stage))
    return pairs


def _tenant_keys(data: dict) -> set[str]:
    tenants_raw = data.get("tenants") or {}
    if not isinstance(tenants_raw, dict):
        return set()
    return {str(k).strip() for k in tenants_raw}


def _git_show_file(ref: str, relpath: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{relpath}"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _write_github_output(key: str, value: str) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT", "")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect new tenants/stages in deploy_targets.yml")
    parser.add_argument("targets_file", help="Path to deploy_targets.yml")
    parser.add_argument(
        "--pipeline",
        choices=("backend", "console", "handlers"),
        default="backend",
        help="handlers filters by tenant only; backend/console by tenant+stage",
    )
    parser.add_argument(
        "--force-scope",
        choices=("full", "new_only"),
        default="",
        help="Override auto detection (e.g. workflow_dispatch passes full)",
    )
    args = parser.parse_args()

    targets_path = Path(args.targets_file)
    if not targets_path.is_file():
        print(f"File not found: {targets_path}", file=sys.stderr)
        return 1

    if args.force_scope == "full" or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        scope = "full"
        filter_path = ""
    else:
        relpath = targets_path.as_posix()
        old_text = _git_show_file("HEAD^", relpath)
        if old_text is None:
            scope = "full"
            filter_path = ""
        else:
            old_data = yaml.safe_load(old_text) or {}
            new_data = _load_targets(targets_path)
            old_pairs = _enabled_pairs(old_data)
            new_pairs = _enabled_pairs(new_data)
            old_tenants = _tenant_keys(old_data)
            new_tenants = _tenant_keys(new_data)

            added_pairs = new_pairs - old_pairs
            added_tenants = new_tenants - old_tenants

            if not added_pairs and not added_tenants:
                scope = "new_only"
                include: list[dict[str, str]] = []
            else:
                scope = "new_only"
                if args.pipeline == "handlers":
                    include = [{"tenant": t} for t in sorted(added_tenants)]
                else:
                    include = [{"tenant": t, "stage": s} for t, s in sorted(added_pairs)]

            if scope == "new_only":
                tmp = tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".json",
                    delete=False,
                    encoding="utf-8",
                )
                json.dump({"include": include}, tmp)
                tmp.close()
                filter_path = tmp.name
            else:
                filter_path = ""

    print(f"scope={scope}")
    if filter_path:
        print(f"filter_json={filter_path}")
    else:
        print("filter_json=")

    _write_github_output("scope", scope)
    if filter_path:
        _write_github_output("filter_json", filter_path)
    else:
        _write_github_output("filter_json", "")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
