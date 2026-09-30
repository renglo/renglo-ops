#!/usr/bin/env python3
"""Print resolved peer CDK context as shell-friendly KEY=value lines."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_CDK_DIR = Path(__file__).resolve().parents[1] / "cdk"
sys.path.insert(0, str(_CDK_DIR))

from peer_deploy_context import resolve_peer_deploy_context  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve peer CDK laptop context")
    parser.add_argument("--tenant", default="")
    parser.add_argument("--env-name", default="")
    parser.add_argument("--bom-repo", default="")
    parser.add_argument("--bom-checkout", default="")
    parser.add_argument("--targets", default="")
    args = parser.parse_args()
    helper_root = Path(__file__).resolve().parents[1]
    ctx = resolve_peer_deploy_context(
        helper_root=helper_root,
        tenant=args.tenant.strip(),
        env_name=args.env_name.strip(),
        bom_repo=args.bom_repo.strip(),
        bom_checkout=args.bom_checkout.strip(),
        targets=args.targets.strip(),
    )
    for key, val in ctx.items():
        print(f"{key.upper()}={val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
