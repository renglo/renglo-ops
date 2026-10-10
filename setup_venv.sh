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

pip install --isolated --index-url "$PYPI_INDEX" -e "./lib[cdk,dev]"
pip install --isolated --index-url "$PYPI_INDEX" -e "./cli[cdk,dev]"

# The CDK CLI is a Node program, so it cannot come from pip. Keep it inside the
# venv rather than installing it globally: no sudo, and no clash with another
# project's cdk version. A missing CLI only blocks deploys, so never fail here.
#
# 2.1145+ rejects DynamoDB acknowledgement ids that contain '::'.
# 2.1131 still crashes in the approval diff when changeset Metadata has no
# new value ("oldValue and newValue are both undefined"). The patch below
# skips that rewrite. Re-apply it whenever this CLI is reinstalled.
CDK_CLI_VERSION="2.1131.0"
if command -v npm >/dev/null 2>&1; then
    echo "Installing the AWS CDK CLI ${CDK_CLI_VERSION} from $NPM_REGISTRY..."
    if npm install --silent --no-fund --no-audit \
        --registry "$NPM_REGISTRY" --prefix "$VENV_NAME" "aws-cdk@${CDK_CLI_VERSION}"; then
        ln -sf "../node_modules/.bin/cdk" "$VENV_NAME/bin/cdk"
        python3 - "$VENV_NAME/node_modules/aws-cdk/lib/index.js" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
old = 'change.setOtherChange("Metadata", new Difference(value.newValue, value.newValue));'
new = 'if (value.newValue !== void 0) change.setOtherChange("Metadata", new Difference(value.newValue, value.newValue));'
if old not in text:
    if new not in text:
        raise SystemExit(f"CDK CLI patch point not found in {path}")
else:
    path.write_text(text.replace(old, new, 1))
PY
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
