#!/bin/bash
# Local virtualenv for the renglo command. The directory is gitignored.
# Always installs from the public indexes. A machine pip.conf or npmrc pointed
# at CodeArtifact must not be used for this tool.
set -euo pipefail

cd "$(dirname "$0")"

PYPI_INDEX="https://pypi.org/simple"
NPM_REGISTRY="https://registry.npmjs.org"
VENV_NAME="${RENGLO_VENV_NAME:-.venv}"

if [ ! -d "$VENV_NAME" ]; then
    echo "Creating virtual environment..."
    python3.12 -m venv "$VENV_NAME"
fi

# shellcheck disable=SC1091
source "$VENV_NAME/bin/activate"

echo "Installing dependencies from PyPI..."
pip install --isolated --index-url "$PYPI_INDEX" --upgrade pip
pip install --isolated --index-url "$PYPI_INDEX" -e "./lib[cdk,dev]"
pip install --isolated --index-url "$PYPI_INDEX" -e ".[cdk,dev]"

# The CDK CLI is a Node program, so it cannot come from pip. Keep it inside the
# venv rather than installing it globally: no sudo, and no clash with another
# project's cdk version. A missing CLI only blocks deploys, so never fail here.
if command -v npm >/dev/null 2>&1; then
    echo "Installing the AWS CDK CLI from $NPM_REGISTRY..."
    if npm install --silent --no-fund --no-audit \
        --registry "$NPM_REGISTRY" --prefix "$VENV_NAME" aws-cdk; then
        ln -sf "../node_modules/.bin/cdk" "$VENV_NAME/bin/cdk"
    else
        echo "  CDK CLI install failed. Deploys need it; everything else works."
    fi
else
    echo "npm not found, so the AWS CDK CLI was skipped. Deploys need it."
    echo "  Install Node.js (https://nodejs.org), then re-run this script."
fi

echo ""
echo "Setup complete. Activate it with:"
echo "  source $VENV_NAME/bin/activate   # from the renglo-ops checkout"
echo ""
echo "Then run:"
echo "  renglo help"
echo "  renglo doctor"
