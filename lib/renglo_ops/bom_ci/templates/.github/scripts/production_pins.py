"""Read a release pin from the current renglo.yaml.

Staging jobs pass stage ``staging`` and get the staging block when it
exists. Production jobs get the top-level pins.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _strip_v(version: str) -> str:
    text = version.strip().strip("'\"")
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        return text[1:]
    return text


def pin(path: Path, field: str, stage: str, peer_id: str = "") -> str:
    """Production pin, or the staging-block pin when that stage is open.

    A missing staging value falls back to production, so a file with no
    staging block deploys the same pins to both stages.
    """
    data = _load(path)
    release = data.get("release") or {}
    if not isinstance(release, dict):
        release = {}
    staging = data.get("staging") or {}
    if not isinstance(staging, dict):
        staging = {}
    if field == "bom":
        production = str(release.get("bom") or "")
        staged = str(staging.get("bom") or "")
    elif field == "console":
        production = str(release.get("console") or "")
        staged = str(staging.get("console") or "")
    elif field == "platform":
        production = str(data.get("platform") or "")
        staged = str(staging.get("platform") or "")
    elif field == "peer":
        peers = (data.get("placement") or {}).get("peers") or {}
        cfg = peers.get(peer_id) if isinstance(peers, dict) else None
        production = str(cfg.get("peers_bom") or "") if isinstance(cfg, dict) else ""
        raw = (staging.get("peers") or {}).get(peer_id) if isinstance(staging.get("peers"), dict) else None
        if isinstance(raw, dict):
            staged = str(raw.get("peers_bom") or "")
        else:
            staged = str(raw or "")
        if not staged:
            staged = str(staging.get("bom") or "")
    else:
        raise SystemExit(f"unknown pin field {field!r}")
    if stage not in {"staging", "production"}:
        raise SystemExit(f"unknown stage {stage!r}")
    chosen = staged if stage == "staging" and staged else production
    return _strip_v(chosen)


def main() -> int:
    if len(sys.argv) >= 5 and sys.argv[1] == "pin":
        peer_id = sys.argv[5] if len(sys.argv) == 6 else ""
        if sys.argv[3] == "peer" and not peer_id:
            print("usage: production_pins.py pin renglo.yaml peer <stage> <peer-id>", file=sys.stderr)
            return 2
        print(pin(Path(sys.argv[2]), sys.argv[3], sys.argv[4], peer_id))
        return 0
    print(
        "usage: production_pins.py pin renglo.yaml bom|console|platform <stage>\n"
        "       production_pins.py pin renglo.yaml peer <stage> <peer-id>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
