"""Adjust a deploy matrix JSON so staging rows use the staging pin block.

The installed renglo-ops still emits production pins for every row. These
commands read renglo.yaml in the checkout and fix the matrix before CI
fans out.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from production_pins import pin


def _rows(payload: str) -> list[dict]:
    data = json.loads(payload)
    rows = data.get("include") or []
    if not isinstance(rows, list):
        raise SystemExit("matrix include must be a list")
    return rows


def _dump(rows: list[dict]) -> None:
    print(json.dumps({"include": rows}, separators=(",", ":")))


def rewrite_peers(payload: str, tenant_file: Path) -> None:
    rewritten: list[dict] = []
    for row in _rows(payload):
        if row.get("deploy_stage") == "staging":
            version = pin(tenant_file, "peer", "staging", str(row.get("peer") or ""))
            if version:
                row = dict(row)
                row["handlers_bom"] = version
                row["handlers_bom_file"] = f"peers_bom/{row['peer']}/v{version}.json"
        rewritten.append(row)
    _dump(rewritten)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "rewrite-peers":
        rewrite_peers(sys.stdin.read(), Path("renglo.yaml"))
        return 0
    print("usage: staging_matrix.py rewrite-peers", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
