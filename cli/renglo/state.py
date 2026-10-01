"""Write local developer files into the product workspace, never into this package."""

from __future__ import annotations

import shutil
from pathlib import Path

from renglo_ops.localdev_render import run_write_local_config
from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.local import refuse_tool_output, workspace_root
from renglo_ops.model.tenant import Tenant


def local_config(
    tenant: Tenant,
    *,
    profile: str,
    region: str,
    apply: bool,
    dry_run: bool = False,
) -> dict[str, str]:
    start = tenant.path.parent if tenant.path else Path.cwd()
    workspace = workspace_root(start)
    preview = workspace / ".renglo" / "local-dev"
    refuse_tool_output(preview)
    if apply:
        api = workspace / "dev" / "renglo-api"
        console = workspace / "console"
        if not api.is_dir() or not console.is_dir():
            raise RengloOpsError(
                f"{workspace} has no dev/renglo-api and console. Use without --apply to preview."
            )
        existing = api / "env_config.py"
        if existing.is_file():
            preview.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing, preview / "env_config.py")
    out = run_write_local_config(
        env_name=tenant.name,
        aws_profile=profile or None,
        aws_region=region or None,
        output_dir=preview,
        dry_run=dry_run,
    )
    written = {"preview": str(out)}
    if apply and not dry_run:
        api = workspace / "dev" / "renglo-api"
        console = workspace / "console"
        for name in ("env_config.py", "run.sh"):
            dest = api / name
            refuse_tool_output(dest)
            shutil.copy2(preview / name, dest)
            written[name] = str(dest)
        env_file = console / ".env.development"
        refuse_tool_output(env_file)
        shutil.copy2(preview / ".env.development", env_file)
        written[".env.development"] = str(env_file)
    return written
