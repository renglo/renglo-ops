#!/usr/bin/env bash
# Upload a Vite dist folder to AWS Amplify Hosting (manual deployment API).
set -euo pipefail

APP_ID="${1:?Amplify app id required}"
BRANCH="${2:?Amplify branch name required}"
DIST_DIR="${3:-console/dist}"

if [[ ! -d "$DIST_DIR" ]]; then
  echo "Dist directory not found: ${DIST_DIR}" >&2
  exit 1
fi

ZIP_FILE="$(mktemp /tmp/console-dist-XXXXXX.zip)"
trap 'rm -f "$ZIP_FILE"' EXIT

echo "Zipping ${DIST_DIR}..."
rm -f "$ZIP_FILE"
(cd "$DIST_DIR" && zip -qr "$ZIP_FILE" .)

echo "Creating Amplify deployment for app=${APP_ID} branch=${BRANCH}..."
DEPLOY_JSON="$(aws amplify create-deployment --app-id "$APP_ID" --branch-name "$BRANCH")"
JOB_ID="$(echo "$DEPLOY_JSON" | jq -r '.jobId')"
ZIP_URL="$(echo "$DEPLOY_JSON" | jq -r '.zipUploadUrl')"

if [[ -z "$JOB_ID" || "$JOB_ID" == "null" || -z "$ZIP_URL" || "$ZIP_URL" == "null" ]]; then
  echo "create-deployment returned unexpected payload:" >&2
  echo "$DEPLOY_JSON" >&2
  exit 1
fi

echo "Uploading artifact (job ${JOB_ID})..."
curl -fsS -X PUT -H "Content-Type: application/zip" --data-binary @"$ZIP_FILE" "$ZIP_URL"

echo "Starting deployment..."
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID"

echo "Waiting for Amplify job ${JOB_ID}..."
for attempt in $(seq 1 60); do
  STATUS="$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" | jq -r '.job.summary.status')"
  echo "  attempt ${attempt}/60 status=${STATUS}"
  case "$STATUS" in
    SUCCEED)
      echo "Amplify deployment succeeded."
      exit 0
      ;;
    FAILED | CANCELLED)
      echo "Amplify deployment ${STATUS}." >&2
      aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" >&2 || true
      exit 1
      ;;
  esac
  sleep 15
done

echo "Timed out waiting for Amplify deployment." >&2
exit 1
