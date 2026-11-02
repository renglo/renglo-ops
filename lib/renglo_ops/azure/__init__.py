"""Azure provider for the renglo CLI.

Sibling of ``renglo_ops.cdk``. The CLI commands stay the same. When
``renglo.yaml`` sets ``cloud: azure``, those commands call this package.
The AWS provider remains ``renglo_ops.cdk`` and is not imported here.

This section will own:

- the HTTP edge, beside ``cdk/hub/stacks/app.py``
- workload identity, beside the IAM in the hub and peer stacks
- peer compute, beside ``cdk/peer``

The BOM, package pins, and hub-versus-peer placement stay in
``renglo_ops.model`` and are shared. Account ids, regions, and
CodeArtifact settings stay on the AWS side.

Feeds live in ``renglo_ops.azure.registry``. Dispatch from ``cli.py``
is not wired yet.
"""

from __future__ import annotations

CLOUD = "azure"


def deploy(stage: str) -> None:
    """Deploy the hub and peers for one stage. Selected by ``cloud: azure``."""
    raise NotImplementedError(
        "renglo_ops.azure is a positioning placeholder. "
        "AWS deploys still run through renglo_ops.cdk."
    )
