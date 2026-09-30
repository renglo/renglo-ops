# Lambda container image for Renglo platform backends.
#
# Build context is the BOM repo root (matching CI):
#   docker build -f Dockerfile -t <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag> .
#
# Expects in context:
#   wheels/*.whl              pre-downloaded from CodeArtifact (BOM python pins,
#                             including renglo-<ext> once an extension is pinned)
#   extensions/<name>/package leftover local trees; sibling blueprints/ is staged
#                             into the wheel at install time (git layout unchanged)
#   dev/<core-package>        leftover local trees (only if still cloned)
#
# CI logs into CodeArtifact on the host and runs download_python_packages.py so
# the image never contains a registry token.
#
# Lambda entrypoints are provided by renglo-api (renglo_api.application, renglo_api.lambda_handler).
# Config: prefer Lambda environment variables; env_config.py is not copied (often gitignored).

FROM public.ecr.aws/lambda/python:3.12

ARG PIP_NO_CACHE_DIR=1

WORKDIR /tmp/build
COPY scripts/install_backend_packages.py /tmp/install_backend_packages.py
COPY scripts/stage_extension_blueprints.py /tmp/stage_extension_blueprints.py
COPY wheels ./wheels
COPY dev ./dev
COPY extensions ./extensions

RUN python -m pip install --upgrade pip setuptools wheel \
    && python /tmp/install_backend_packages.py --root /tmp/build

WORKDIR ${LAMBDA_TASK_ROOT}

CMD ["renglo_api.lambda_handler.lambda_handler"]
