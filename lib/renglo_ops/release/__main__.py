"""Dispatch a release script: python -m renglo_ops.release <script> [args].

A path argument named renglo.yaml is projected to the legacy catalog shape for
scripts that still open deploy_targets.yml.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

from renglo_ops.model.legacy import deploy_targets_dict
from renglo_ops.model.tenant import load_tenant


def _projected(args: list[str]) -> tuple[list[str], list[str]]:
    """Project catalog paths. Leave ``--overlay-env renglo.yaml`` as the tenant file.

    Returns the rewritten argv and paths created for it. Callers delete those
    paths after the script runs.
    """
    out: list[str] = []
    created: list[str] = []
    overlay = False
    for arg in args:
        if overlay:
            out.append(arg)
            overlay = False
            continue
        if arg == "--overlay-env":
            out.append(arg)
            overlay = True
            continue
        projected, made = _project(arg)
        out.append(projected)
        if made:
            created.append(projected)
    return out, created


def _project(arg: str) -> tuple[str, bool]:
    """Rewrite a ``renglo.yaml`` argument into a legacy catalog file beside it.

    Scripts such as ``render_deploy_matrix`` treat the targets file's parent as
    the BOM checkout (``peers_bom/``, ``bom/``). A file under the system temp
    directory makes that lookup miss the real pins.
    """
    path = Path(arg)
    if path.name != "renglo.yaml" or not path.is_file():
        return arg, False
    resolved = path.resolve()
    handle = tempfile.NamedTemporaryFile(
        "w",
        prefix=".renglo-",
        suffix="-deploy-targets.yml",
        delete=False,
        dir=resolved.parent,
        encoding="utf-8",
    )
    yaml.safe_dump(deploy_targets_dict(load_tenant(resolved)), handle, sort_keys=False)
    handle.close()
    return handle.name, True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        names = sorted(
            path.stem
            for path in Path(__file__).resolve().parent.glob("*.py")
            if path.stem not in {"__init__", "__main__", "catalog"}
        )
        print("usage: python -m renglo_ops.release <script> [args]")
        for name in names:
            print(f"  {name}")
        return 0
    script = Path(__file__).resolve().parent / f"{args[0]}.py"
    if not script.is_file():
        print(f"unknown release script: {args[0]}", file=sys.stderr)
        return 2
    import runpy

    release_dir = str(script.parent)
    if release_dir not in sys.path:
        sys.path.insert(0, release_dir)
    argv, created = _projected(args[1:])
    sys.argv = [str(script), *argv]
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        for path in created:
            Path(path).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
