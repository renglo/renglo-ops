"""Channel ingress profiles for the platform webhook edge Lambda.

Profiles are data-only: which headers to forward, optional GET challenge,
optional edge shared-secret gate. Producer crypto stays in extension handlers.
"""

from __future__ import annotations

from typing import Any

# channel id → profile
CHANNEL_PROFILES: dict[str, dict[str, Any]] = {
    "whatsapp": {
        "forward_headers": ["x-hub-signature-256", "x-hub-signature"],
        "challenge": {
            "method": "GET",
            "token_query": "hub.verify_token",
            "challenge_query": "hub.challenge",
            "mode_query": "hub.mode",
            "mode_value": "subscribe",
            # Dynamo: {org=_all, ring=whatsapp_config} attributes.verify_token
            "config_org": "_all",
            "config_ring": "whatsapp_config",
            "config_key": "verify_token",
            # Also accept alternate query key spellings used by some gateways
            "token_query_alts": ["hub_verify_token"],
            "challenge_query_alts": ["hub_challenge"],
            "mode_query_alts": ["hub_mode"],
        },
        "edge_gate": None,
    },
}


def get_profile(channel: str) -> dict[str, Any] | None:
    return CHANNEL_PROFILES.get(str(channel or "").strip().lower())
