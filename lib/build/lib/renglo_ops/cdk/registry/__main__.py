"""Synth the CodeArtifact publisher stack from registry.yaml.

    RENGLO_REGISTRY=/path/to/registry.yaml AWS_PROFILE=name python -m renglo_ops.cdk.registry
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _require_profile() -> str:
    profile = (os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE") or "").strip()
    if not profile or profile == "default":
        raise SystemExit(
            "Refusing to synth the registry without an explicit AWS profile.\n"
            "  export AWS_PROFILE=<publisher-profile>"
        )
    print(f"Using AWS profile: {profile}", file=sys.stderr)
    return profile


def main() -> None:
    _require_profile()
    import aws_cdk as cdk

    from renglo_ops.model.registry import load_registry

    raw = os.environ.get("RENGLO_REGISTRY", "").strip()
    if not raw:
        raise SystemExit("Set RENGLO_REGISTRY to registry.yaml")
    registry = load_registry(Path(raw))
    outdir = os.environ.get("CDK_OUTDIR", "").strip()
    app = cdk.App(outdir=outdir or None)
    from renglo_ops.cdk.registry.stack import PublisherStack

    PublisherStack(
        app,
        f"{registry.name}-publisher",
        publisher_name=registry.name,
        github_org=registry.github_org,
        github_publish_repos=registry.publish_repos or ["*"],
        reader_aws_accounts=registry.reader_accounts,
        python_repository=registry.python,
        npm_repository=registry.npm,
    )
    app.synth()


if __name__ == "__main__":
    main()
