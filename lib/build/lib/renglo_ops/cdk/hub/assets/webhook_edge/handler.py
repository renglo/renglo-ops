"""
Platform-native webhook edge Lambda.

Path: GET|POST /{portfolio}/{org}/{channel}

- GET: optional config-driven challenge (e.g. Meta hub.verify_token)
- POST: ACK 200 quickly, PutEvents uniform envelope for EventBridge → /_schd/ingress

Heavy authenticity checks (HMAC, OAuth, …) run in extension handlers after ingress.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
from typing import Any

import boto3

from channels import get_profile

ENVIRONMENT = os.environ.get("ENVIRONMENT", "")
DATA_TABLE = os.environ.get("DYNAMODB_DATA_TABLE", f"{ENVIRONMENT}_data" if ENVIRONMENT else "")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
EVENT_SOURCE = "custom.renglo.webhook"
EVENT_DETAIL_TYPE = "WebhookReceived"
SINGLETON_ID = "00000000-0000-0000-0000-000000000000"


def _response(status: int, body: str = "", content_type: str = "text/plain"):
    return {
        "statusCode": status,
        "headers": {"Content-Type": content_type},
        "body": body,
    }


def _path_parts(event: dict) -> tuple[str | None, str | None, str | None]:
    """Extract portfolio / org / channel from pathParameters or rawPath."""
    params = event.get("pathParameters") or {}
    portfolio = params.get("portfolio")
    org = params.get("org")
    channel = params.get("channel")
    if portfolio and org and channel:
        return str(portfolio), str(org), str(channel).lower()

    raw = event.get("rawPath") or event.get("path") or ""
    parts = [p for p in str(raw).split("/") if p]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2].lower()
    if len(parts) == 2:
        # Legacy WhatsApp shape /{portfolio}/{org} → channel=whatsapp
        return parts[0], parts[1], "whatsapp"
    return None, None, None


def _normalize_headers(event: dict) -> dict[str, str]:
    headers = event.get("headers") or {}
    return {str(k).lower(): str(v) for k, v in headers.items() if v is not None}


def _qs(event: dict) -> dict[str, str]:
    qs = event.get("queryStringParameters") or {}
    return {str(k): str(v) for k, v in qs.items() if v is not None}


def _qs_get(qs: dict[str, str], primary: str, alts: list[str] | None = None) -> str | None:
    if primary in qs:
        return qs[primary]
    for alt in alts or []:
        if alt in qs:
            return qs[alt]
    return None


def _load_config_value(portfolio: str, *, config_org: str, config_ring: str, config_key: str) -> str | None:
    if not DATA_TABLE:
        return None
    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(DATA_TABLE)
        key = {
            "portfolio_index": f"irn:data:{portfolio}",
            "doc_index": f"{config_org}:{config_ring}:{SINGLETON_ID}",
        }
        item = table.get_item(Key=key).get("Item") or {}
        attrs = item.get("attributes") or {}
        value = str(attrs.get(config_key) or "").strip()
        return value or None
    except Exception as exc:
        print(f"Failed to load config {config_ring}.{config_key}: {exc}")
        return None


def _handle_challenge(
    event: dict,
    portfolio: str,
    org: str,
    channel: str,
    profile: dict[str, Any],
):
    challenge_cfg = profile.get("challenge") or {}
    if not challenge_cfg:
        return _response(405, "Method Not Allowed")

    qs = _qs(event)
    mode = _qs_get(qs, challenge_cfg.get("mode_query", ""), challenge_cfg.get("mode_query_alts"))
    token = _qs_get(qs, challenge_cfg.get("token_query", ""), challenge_cfg.get("token_query_alts"))
    challenge = _qs_get(
        qs, challenge_cfg.get("challenge_query", ""), challenge_cfg.get("challenge_query_alts")
    )
    mode_value = challenge_cfg.get("mode_value") or "subscribe"

    expected = _load_config_value(
        portfolio,
        config_org=challenge_cfg.get("config_org") or "_all",
        config_ring=challenge_cfg.get("config_ring") or "",
        config_key=challenge_cfg.get("config_key") or "",
    )
    # Env fallback for local/dev without Dynamo config
    if not expected:
        expected = os.environ.get("WEBHOOK_VERIFY_TOKEN") or os.environ.get("META_WEBHOOK_VERIFY_TOKEN")

    if (
        mode == mode_value
        and expected
        and token is not None
        and challenge is not None
        and hmac.compare_digest(str(token), str(expected))
    ):
        print(f"Challenge OK for {portfolio}/{org}/{channel}")
        return _response(200, str(challenge))

    print(f"Challenge FAILED for {portfolio}/{org}/{channel}")
    return _response(403, "Forbidden")


def _edge_gate_ok(headers: dict[str, str], portfolio: str, profile: dict[str, Any]) -> bool:
    gate = profile.get("edge_gate")
    if not gate:
        return True
    header_name = str(gate.get("header") or "").lower()
    config_key = gate.get("config_key") or ""
    presented = headers.get(header_name, "")
    expected = _load_config_value(
        portfolio,
        config_org=gate.get("config_org") or "_all",
        config_ring=gate.get("config_ring") or "",
        config_key=config_key,
    )
    if not expected:
        expected = os.environ.get("WEBHOOK_EDGE_GATE_SECRET")
    if not expected:
        return True
    return bool(presented) and hmac.compare_digest(str(presented), str(expected))


def _decode_body(event: dict) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception:
            body = ""
    return body if isinstance(body, str) else str(body)


def _forward_headers(headers: dict[str, str], profile: dict[str, Any]) -> dict[str, str]:
    allow = [str(h).lower() for h in (profile.get("forward_headers") or [])]
    if not allow:
        return {}
    return {k: headers[k] for k in allow if k in headers}


def _handle_post(
    event: dict,
    portfolio: str,
    org: str,
    channel: str,
    profile: dict[str, Any],
):
    headers = _normalize_headers(event)
    if not _edge_gate_ok(headers, portfolio, profile):
        return _response(401, "Unauthorized")

    body = _decode_body(event)
    forwarded = _forward_headers(headers, profile)
    detail = {
        "type": "webhook",
        "portfolio": portfolio,
        "org": org,
        "channel": channel,
        "raw_body": body,
        "headers": forwarded,
        "query": _qs(event),
    }
    # Convenience for WhatsApp inbound (also derived from headers at API)
    if "x-hub-signature-256" in forwarded:
        detail["signature_header"] = forwarded["x-hub-signature-256"]
    elif "x-hub-signature" in forwarded:
        detail["signature_header"] = forwarded["x-hub-signature"]

    try:
        events = boto3.client("events")
        events.put_events(
            Entries=[
                {
                    "Source": EVENT_SOURCE,
                    "DetailType": EVENT_DETAIL_TYPE,
                    "Detail": json.dumps(detail),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
        print(f"EventBridge enqueued for {portfolio}/{org}/{channel}")
    except Exception as exc:
        # Still ACK to avoid producer retry storms; processing can be retried manually.
        print(f"EventBridge put_events failed: {exc}")

    return _response(200, "")


def lambda_handler(event, context):
    print("Event:", json.dumps(event)[:2000])
    portfolio, org, channel = _path_parts(event)
    if not portfolio or not org or not channel:
        return _response(400, "Expected /{portfolio}/{org}/{channel}")

    profile = get_profile(channel)
    if profile is None:
        return _response(404, f"Unknown channel: {channel}")

    method = (
        (event.get("requestContext") or {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()

    if method == "GET":
        return _handle_challenge(event, portfolio, org, channel, profile)
    if method == "POST":
        return _handle_post(event, portfolio, org, channel, profile)
    return _response(405, "Method Not Allowed")
