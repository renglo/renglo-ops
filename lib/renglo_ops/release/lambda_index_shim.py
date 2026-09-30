"""CDK seed Handler is ``index.handler`` (inline ZipFile is always ``index.py``).

The published zip's real entry point is ``lambda_router.lambda_handler``.
This module is that function under the seed name, so a CDK update that
resets Handler still reaches the router.
"""

from lambda_router import lambda_handler as handler

__all__ = ["handler"]
