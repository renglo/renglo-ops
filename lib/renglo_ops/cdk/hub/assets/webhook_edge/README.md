# Platform webhook edge — testing guide

Stack B deploys a dedicated **webhook edge Lambda** plus an HTTP API. External producers (Meta WhatsApp, future channels) hit this URL; the Lambda ACKs quickly and enqueues EventBridge. Heavy work runs later at `POST /_schd/ingress` on the Renglo API.

```
Producer → HTTP API → {env}-webhook-edge → PutEvents
                              ↓
                    EventBridge rule {env}-renglo-webhook
                              ↓
              API Destination → POST /_schd/ingress (X-Renglo-Ingress-Secret)
                              ↓
                    extension handler (e.g. whatsapp/inbound)
```

Lambda source: this folder (`handler.py`, `channels.py`). CDK construct: `ops/launcher/cdk/stacks/webhook_ingress.py`.

---

## Prerequisites

1. **Stack B deployed** with `WebhookIngress` (after universal ingress work).
2. AWS CLI profile with read access to Lambda, EventBridge, CloudWatch, Secrets Manager.
3. For **full end-to-end** tests: Renglo API running (local or cloud) with:
   - `/_schd/ingress` route deployed
   - `RENGLO_INGRESS_SECRET` matching Secrets Manager `{env}/renglo/ingress-secret`
4. For **WhatsApp GET challenge OK**: `whatsapp_config.verify_token` set in Dynamo for the portfolio (`_all` org, ring `whatsapp_config`).

---

## Find your URLs and names

Replace `<env>` with your environment name (e.g. `example0731`).

```bash
export ENV=<env>
export AWS_PROFILE=<profile>
export AWS_REGION=us-east-1
```

### CloudFormation outputs (stack-b)

```bash
aws cloudformation describe-stacks \
  --stack-name "${ENV}-stack-b" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?contains(OutputKey, 'Webhook') || contains(OutputKey, 'Ingress')].[OutputKey,OutputValue]" \
  --output table
```

| Output | Use |
|--------|-----|
| `WebhookEdgeBaseUrl` | Public edge base (no trailing path) |
| `WebhookEdgeFunctionName` | Lambda name (`{env}-webhook-edge`) |
| `RengloIngressUrl` | API ingress target |
| `RengloIngressSecretArn` | Secret for `X-Renglo-Ingress-Secret` |

After `write-local-config`, the same values appear in `env_config.py` as `WEBHOOK_EDGE_BASE_URL` and `RENGLO_INGRESS_SECRET`.

### Lambda exists

```bash
aws lambda get-function \
  --function-name "${ENV}-webhook-edge" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query 'Configuration.[FunctionName,Runtime,LastModified,State]' \
  --output table
```

### EventBridge wiring

```bash
aws events describe-rule \
  --name "${ENV}-renglo-webhook" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"

aws events list-targets-by-rule \
  --rule "${ENV}-renglo-webhook" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

---

## URL shape

```text
{WEBHOOK_EDGE_BASE_URL}/{portfolio}/{org}/{channel}
```

| Channel | Example | Notes |
|---------|---------|--------|
| `whatsapp` | `…/abc123/_all/whatsapp` | Meta webhook + GET verify |
| Legacy | `…/abc123/_all` | Same Lambda; channel defaults to `whatsapp` |

Supported channels are defined in `channels.py` (v1: `whatsapp` only).

Set shell helpers:

```bash
export EDGE_BASE='https://q4nc3nz5la.execute-api.us-east-1.amazonaws.com'   # from outputs
export PORTFOLIO='<your-portfolio-id>'
export ORG='_all'   # or a concrete org id
```

---

## Level 1 — Edge only (no API / no Meta)

These tests confirm the HTTP API and Lambda respond. They do **not** require `RENGLO_INGRESS_SECRET` or a running Renglo API.

### Unknown channel → 404

```bash
curl -i "${EDGE_BASE}/${PORTFOLIO}/${ORG}/not-a-channel"
```

Expected: `HTTP/1.1 404` and body `Unknown channel: not-a-channel`.

### WhatsApp GET — bad verify token → 403

```bash
curl -i "${EDGE_BASE}/${PORTFOLIO}/${ORG}/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=12345"
```

Expected: `403 Forbidden` (unless Dynamo has `verify_token=wrong`).

### WhatsApp GET — good verify token → 200 + challenge

Use the same `verify_token` stored in WhatsApp Config for the portfolio:

```bash
curl -i "${EDGE_BASE}/${PORTFOLIO}/${ORG}/whatsapp?hub.mode=subscribe&hub.verify_token=<verify_token>&hub.challenge=12345"
```

Expected: `200` with body `12345` (plain text). This is what Meta’s “Verify and save” button expects.

### WhatsApp POST — always ACK 200

The edge does **not** verify Meta HMAC here; it only enqueues and returns 200.

```bash
curl -i -X POST "${EDGE_BASE}/${PORTFOLIO}/${ORG}/whatsapp" \
  -H 'Content-Type: application/json' \
  -H 'X-Hub-Signature-256: sha256=deadbeef' \
  -d '{"object":"whatsapp_business_account","entry":[]}'
```

Expected: `200` with empty body.

### CloudWatch — confirm enqueue

```bash
aws logs tail "/aws/lambda/${ENV}-webhook-edge" \
  --follow --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

After POST, look for:

```text
EventBridge enqueued for <portfolio>/<org>/whatsapp
```

If you see `EventBridge put_events failed`, check the Lambda role has `events:PutEvents` on the default bus.

---

## Level 2 — EventBridge → API ingress

Confirms the rule, connection, and API destination reach Renglo.

### Get ingress secret

```bash
export INGRESS_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id "${ENV}/renglo/ingress-secret" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query SecretString --output text)
echo "secret length: ${#INGRESS_SECRET}"
```

Set the same value on the API (`RENGLO_INGRESS_SECRET` in `dev/renglo-api/env_config.py` for local, or backend Lambda env for cloud).

### Call ingress directly (bypass edge)

Useful to verify the API dispatcher without EventBridge:

```bash
export API_BASE='https://o4gvifzdxj.execute-api.us-east-1.amazonaws.com/production'  # BASE_URL

curl -i -X POST "${API_BASE}/_schd/ingress" \
  -H "Content-Type: application/json" \
  -H "X-Renglo-Ingress-Secret: ${INGRESS_SECRET}" \
  -d '{
    "detail": {
      "type": "webhook",
      "channel": "whatsapp",
      "portfolio": "'"${PORTFOLIO}"'",
      "org": "'"${ORG}"'",
      "raw_body": "{\"object\":\"whatsapp_business_account\",\"entry\":[]}",
      "signature_header": "sha256=deadbeef"
    }
  }'
```

Expected with local API + handlers installed: `200` or `400` from `whatsapp/inbound` (HMAC / link gate), **not** `401 Unauthorized`.

Wrong secret → `401`.

### Full path after POST to edge

1. Run the Level 1 POST curl.
2. Tail **edge** logs (enqueue).
3. Tail **Renglo API** logs (local terminal or `/aws/lambda/{env}-backend-production`).
4. Look for `Processing ingress type=webhook`.

EventBridge delivery is async; allow a few seconds. If the edge enqueues but the API never sees traffic, check:

- EventBridge rule `{env}-renglo-webhook` is `ENABLED`
- Target role (`{env}_tt_role`) has `events:InvokeApiDestination`
- API destination URL is `{BASE_URL}/_schd/ingress`
- Connection header `X-Renglo-Ingress-Secret` matches API config

---

## Level 3 — Meta (production-like)

1. In Meta Developer Console → WhatsApp → Configuration, set callback URL:

   ```text
   {WEBHOOK_EDGE_BASE_URL}/{portfolio}/{org}/whatsapp
   ```

2. Set **Verify token** equal to `whatsapp_config.verify_token` in the console.
3. Click **Verify and save** (exercises GET challenge).
4. Subscribe to `messages`.
5. Send a test message from a linked phone (exercises POST → EventBridge → ingress → `whatsapp/inbound`).

Unlinked senders should get a “connect in console” response, not an agent reply.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `404 Unknown channel` | Typo in path or channel not in `channels.py` |
| GET always `403` | `verify_token` mismatch or missing in Dynamo `whatsapp_config` |
| POST `200` but no API activity | EventBridge target/connection/secret; or API not running |
| API `401` on ingress | `RENGLO_INGRESS_SECRET` missing or wrong on API |
| API `400` from inbound | Expected — HMAC, unlinked user, or bad payload; edge did its job |
| `Cannot find asset webhook_edge` on deploy | Re-run `bootstrap/install.py synth` (assets must be copied into output `cdk/assets/`) |

---

## Regenerate local config after deploy

```bash
cd ops

python3.12 bootstrap/install.py write-state \
  --env-name "$ENV" --aws-profile "$AWS_PROFILE" --aws-region "$AWS_REGION"

python3.12 bootstrap/install.py write-local-config \
  --env-name "$ENV" --aws-profile "$AWS_PROFILE" --aws-region "$AWS_REGION"
```

Copy `WEBHOOK_EDGE_BASE_URL` and `RENGLO_INGRESS_SECRET` from `bootstrap/output/<env>/local-dev/env_config.py` into `dev/renglo-api/env_config.py`, then restart the local API.

---

## Related docs

- [extensions/whatsapp/README.md](../../../../extensions/whatsapp/README.md) — WhatsApp tenancy, Meta setup, linking
- [ops/bootstrap/README.md](../../../bootstrap/README.md) — Stack A/B deploy and `write-local-config`
