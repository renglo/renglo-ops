"""Azure package feeds.

Sibling of the CodeArtifact publisher stack in ``renglo_ops.cdk.registry``.
``registry.yaml`` is still parsed by ``renglo_ops.model.registry``. This
module will turn that description into a Python index URL, an npm
registry URL, and a publish identity for GitHub.

``reader_accounts`` is an AWS account list and stays on the CodeArtifact
stack. The image install step consumes the URLs returned here. An Azure
deploy imports this module and does not import ``renglo_ops.cdk.registry``
or ``renglo_ops.release.registry_targets``.
"""

from __future__ import annotations


def feed_endpoints(registry) -> dict[str, str]:
    """Python index URL and npm registry URL for this org's feeds."""
    raise NotImplementedError(
        "renglo_ops.azure.registry is a positioning placeholder. "
        "The AWS warehouse remains renglo_ops.cdk.registry."
    )
