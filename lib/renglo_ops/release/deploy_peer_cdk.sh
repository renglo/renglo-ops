#!/usr/bin/env bash
# Synth / deploy / destroy peer CDK stacks (first-time infra or compute-shape changes).
#
# Set the same shell variables as bootstrap (see docs/PEERS.md):
#   export ENV=<env_name>          # from launcher/cdk/customer-config.json
#   export AWS_PROFILE=<profile>
#   export PEER_ID=<peer-id>       # optional; omit for all catalog peers
#
# BOM git id and deploy_targets.yml are read from launcher + sibling *-bom by app.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_NAME="${BOM_VENV_NAME:-bom-venv}"
VENV_PYTHON="$HELPER_ROOT/$VENV_NAME/bin/python"
CDK_APP="../$VENV_NAME/bin/python app.py"

usage() {
  cat <<'EOF'
Usage: deploy_peer_cdk.sh <synth|deploy|destroy> [--peer-id <id>] [--profile <name>]

Required env (same as bootstrap peer CDK docs):
  ENV               AWS env prefix (customer-config.json env_name)
  AWS_PROFILE       account + region for CDK (or --profile)

Optional:
  PEER_ID           peers.<id> (or --peer-id); omit for all peers (--all)
  --profile         AWS profile (else AWS_PROFILE env)
  --tenant          CDK context override
  --bom-checkout    sibling *-bom folder override
  --targets         explicit deploy_targets.yml path
EOF
}

ACTION="${1:-}"
shift || true

PEER_ID="${PEER_ID:-}"
PROFILE="${AWS_PROFILE:-}"
TENANT=""
BOM_CHECKOUT=""
TARGETS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --peer-id) PEER_ID="$2"; shift 2 ;;
    --peer-id=*) PEER_ID="${1#*=}"; shift ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --tenant) TENANT="$2"; shift 2 ;;
    --tenant=*) TENANT="${1#*=}"; shift ;;
    --bom-checkout) BOM_CHECKOUT="$2"; shift 2 ;;
    --bom-checkout=*) BOM_CHECKOUT="${1#*=}"; shift ;;
    --targets) TARGETS="$2"; shift 2 ;;
    --targets=*) TARGETS="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

case "$ACTION" in
  synth|deploy|destroy) ;;
  ""|-h|--help) usage; exit 0 ;;
  *) echo "Unknown action: $ACTION (use synth, deploy, or destroy)" >&2; usage >&2; exit 1 ;;
esac

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: run bash setup-venv.sh from bom-helper first" >&2
  exit 1
fi

if [[ -z "${ENV:-}" ]]; then
  echo "ERROR: export ENV=<env_name> (launcher/cdk/customer-config.json env_name)" >&2
  exit 1
fi

CONTEXT=()
[[ -n "$PEER_ID" ]] && CONTEXT+=(--context "peer_id=${PEER_ID}")
[[ -n "$TENANT" ]] && CONTEXT+=(--context "tenant=${TENANT}")
[[ -n "$BOM_CHECKOUT" ]] && CONTEXT+=(--context "bom_checkout=${BOM_CHECKOUT}")
[[ -n "$TARGETS" ]] && CONTEXT+=(--context "targets=${TARGETS}")

if [[ -n "$PEER_ID" ]]; then
  OUTPUT_DIR="output/${PEER_ID}"
else
  OUTPUT_DIR="output/_all"
fi
mkdir -p "$OUTPUT_DIR"

CDK_ARGS=(--app "$CDK_APP" --output "$OUTPUT_DIR")
if ((${#CONTEXT[@]})); then
  CDK_ARGS+=("${CONTEXT[@]}")
fi
[[ -n "$PROFILE" ]] && CDK_ARGS+=(--profile "$PROFILE")

STACK=""
if [[ -n "$PEER_ID" ]]; then
  STACK="${ENV}-peer-${PEER_ID}"
fi

# CDK seed ZipFile is always index.py / index.handler. peer_packager then
# publishes the real zip and sets Handler to lambda_router.lambda_handler.
# A later CDK deploy resets Handler even when it leaves the zip in place —
# restore the published entry point when CodeSize shows a real package.
_aws() {
  if [[ -n "$PROFILE" ]]; then
    aws --region "${AWS_REGION:-us-east-1}" --profile "$PROFILE" "$@"
  else
    aws --region "${AWS_REGION:-us-east-1}" "$@"
  fi
}

_pin_published_handler() {
  local fn="$1"
  local handler size
  handler="$(_aws lambda get-function-configuration --function-name "$fn" --query Handler --output text)"
  size="$(_aws lambda get-function-configuration --function-name "$fn" --query CodeSize --output text)"
  if [[ "$size" -le 10000 ]]; then
    echo "  skip handler pin for ${fn} (seed zip, ${size} bytes)"
    return 0
  fi
  if [[ "$handler" == "lambda_router.lambda_handler" ]]; then
    echo "  handler already ${handler} (${fn})"
    return 0
  fi
  echo "+ pin ${fn} handler ${handler} → lambda_router.lambda_handler (zip ${size} bytes)"
  _aws lambda update-function-configuration --function-name "$fn" --handler lambda_router.lambda_handler >/dev/null
  _aws lambda wait function-updated --function-name "$fn"
}

_pin_published_handler_all() {
  local fn
  while IFS= read -r fn; do
    [[ -z "$fn" ]] && continue
    _pin_published_handler "$fn"
  done < <(_aws lambda list-functions --query "Functions[?starts_with(FunctionName, '${ENV}-peer-')].FunctionName" --output text | tr '\t' '\n')
}

cd "$HELPER_ROOT/cdk"

case "$ACTION" in
  synth)
    if [[ -n "$STACK" ]]; then
      echo "+ cdk synth ${STACK} --output ${OUTPUT_DIR} ..."
      cdk synth "$STACK" "${CDK_ARGS[@]}"
    else
      echo "+ cdk synth --output ${OUTPUT_DIR} ..."
      cdk synth "${CDK_ARGS[@]}"
    fi
    ;;
  deploy)
    if [[ -n "$STACK" ]]; then
      echo "+ cdk deploy ${STACK} --output ${OUTPUT_DIR} ..."
      cdk deploy "$STACK" "${CDK_ARGS[@]}" --require-approval never
      _pin_published_handler "$STACK"
    else
      echo "+ cdk deploy --all --output ${OUTPUT_DIR} ..."
      cdk deploy --all "${CDK_ARGS[@]}" --require-approval never
      _pin_published_handler_all
    fi
    ;;
  destroy)
    if [[ -n "$STACK" ]]; then
      echo "+ cdk destroy ${STACK} --output ${OUTPUT_DIR} ..."
      cdk destroy "$STACK" "${CDK_ARGS[@]}" --force
    else
      echo "+ cdk destroy --all --output ${OUTPUT_DIR} ..."
      cdk destroy --all "${CDK_ARGS[@]}" --force
    fi
    ;;
esac
