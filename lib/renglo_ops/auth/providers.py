"""Load auth/providers.yaml and project the public provider list.

The yaml names Secrets Manager entries. It does not contain client secrets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from renglo_ops.model.errors import RengloOpsError

PROVIDER_TYPES = ("native", "social", "oidc", "saml")
SOCIAL_PROVIDER_NAMES = ("Google", "Facebook", "LoginWithAmazon", "SignInWithApple")
_SECRET = "secret"

_PUBLIC_FIELDS = (
    "id",
    "name",
    "type",
    "cognito_provider_name",
    "enabled",
    "display_name",
    "icon",
    "display_order",
)

_REFERENCE_YAML = """
providers:
  - id: cognito
    name: Email and Password
    type: native
    cognito_provider_name: COGNITO
    enabled: true
    display_name: Email and password
    icon: mail
    display_order: 0
  - id: google
    name: Google
    type: social
    cognito_provider_name: Google
    enabled: true
    display_name: Continue with Google
    icon: google
    display_order: 10
    secret: reference/renglo/auth/google
    attribute_mapping:
      email: email
      email_verified: email_verified
      given_name: given_name
      family_name: family_name
      username: sub
  - id: okta
    name: Okta
    type: oidc
    cognito_provider_name: Okta
    enabled: true
    display_name: Continue with Okta
    icon: okta
    display_order: 20
    secret: reference/renglo/auth/okta
    oidc:
      issuer: https://example.okta.com
    attribute_mapping:
      email: email
      email_verified: email_verified
      given_name: given_name
      family_name: family_name
      username: sub
"""


def secret_name_for(env_name: str, provider_id: str) -> str:
    """Default Secrets Manager name for one provider in one environment."""
    return f"{env_name}/renglo/auth/{provider_id}"


@dataclass
class AuthProvider:
    id: str
    name: str
    type: str
    cognito_provider_name: str
    enabled: bool
    display_name: str
    icon: str = ""
    display_order: int = 0
    secret: str = ""
    attribute_mapping: dict[str, str] = field(default_factory=dict)
    issuer: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    jwks_uri: str = ""
    metadata_url: str = ""


@dataclass
class ProviderCatalog:
    providers: list[AuthProvider] = field(default_factory=list)
    source: str = ""

    def enabled(self) -> list[AuthProvider]:
        return [item for item in self.providers if item.enabled]

    def external_enabled(self) -> list[AuthProvider]:
        return [item for item in self.enabled() if item.type != "native"]

    def public_providers(self) -> list[dict[str, Any]]:
        published: list[dict[str, Any]] = []
        for item in sorted(self.enabled(), key=lambda provider: (provider.display_order, provider.id)):
            published.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "type": item.type,
                    "cognito_provider_name": item.cognito_provider_name,
                    "enabled": True,
                    "display_name": item.display_name,
                    "icon": item.icon,
                    "display_order": item.display_order,
                }
            )
        return published

    def public_json(self) -> str:
        return json.dumps(self.public_providers(), separators=(",", ":"))


def load_provider_catalog(path: Path, *, env_name: str) -> ProviderCatalog:
    """Load a tenant provider file. A missing file means native login only."""
    if not path.is_file():
        return ProviderCatalog(providers=[], source="absent")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return catalog_from_data(raw, env_name=env_name, source=str(path))


def reference_public_providers() -> list[dict[str, Any]]:
    """Public list the Console uses to show email, Google, and Okta."""
    catalog = catalog_from_data(
        yaml.safe_load(_REFERENCE_YAML),
        env_name="reference",
        source="reference",
    )
    return catalog.public_providers()


def catalog_from_data(raw: Any, *, env_name: str, source: str = "") -> ProviderCatalog:
    if not isinstance(raw, dict):
        raise RengloOpsError("auth/providers.yaml must be a mapping")
    entries = raw.get("providers")
    if not isinstance(entries, list) or not entries:
        raise RengloOpsError("auth/providers.yaml providers must be a non-empty list")
    providers = [_provider(entry, index, env_name) for index, entry in enumerate(entries)]
    _validate(providers)
    return ProviderCatalog(providers=providers, source=source)


def cognito_provider_request(provider: AuthProvider) -> dict[str, Any]:
    """CreateIdentityProvider shape. Secret values are named, not included."""
    if provider.type == "native":
        raise RengloOpsError(f"{provider.id} is native and is not a Cognito identity provider")
    provider_type = _cognito_provider_type(provider)
    details: dict[str, Any] = {}
    for key, value in _detail_fields(provider).items():
        if isinstance(value, tuple) and value[0] == _SECRET:
            details[key] = f"secret:{provider.secret}:{value[1]}"
        else:
            details[key] = value
    return {
        "ProviderName": provider.cognito_provider_name,
        "ProviderType": provider_type,
        "ProviderDetails": details,
        "AttributeMapping": dict(provider.attribute_mapping or _default_attribute_mapping()),
    }


def detail_fields(provider: AuthProvider) -> dict[str, Any]:
    """ProviderDetails values. A ('secret', field) tuple is read from Secrets Manager."""
    if provider.type == "native":
        return {}
    return _detail_fields(provider)


def _provider(entry: Any, index: int, env_name: str) -> AuthProvider:
    if not isinstance(entry, dict):
        raise RengloOpsError(f"providers[{index}] must be a mapping")
    provider_id = str(entry.get("id") or "").strip()
    provider_type = str(entry.get("type") or "").strip()
    if not provider_id:
        raise RengloOpsError(f"providers[{index}] is missing id")
    if provider_type not in PROVIDER_TYPES:
        enabled = bool(entry.get("enabled", False))
        if enabled or provider_type:
            raise RengloOpsError(
                f"provider {provider_id} has unknown type {provider_type or '(empty)'}"
            )
    secret = str(entry.get("secret") or "").strip()
    if provider_type != "native" and not secret:
        if env_name:
            secret = secret_name_for(env_name, provider_id)
    oidc = entry.get("oidc") or {}
    saml = entry.get("saml") or {}
    if oidc and not isinstance(oidc, dict):
        raise RengloOpsError(f"provider {provider_id} oidc must be a mapping")
    if saml and not isinstance(saml, dict):
        raise RengloOpsError(f"provider {provider_id} saml must be a mapping")
    mapping = entry.get("attribute_mapping") or {}
    if mapping and not isinstance(mapping, dict):
        raise RengloOpsError(f"provider {provider_id} attribute_mapping must be a mapping")
    attribute_mapping = {str(key): str(value) for key, value in mapping.items()}
    if not attribute_mapping and provider_type != "native":
        attribute_mapping = _default_attribute_mapping()
    try:
        display_order = int(entry.get("display_order") or 0)
    except (TypeError, ValueError) as exc:
        raise RengloOpsError(f"provider {provider_id} display_order must be an integer") from exc
    name = str(entry.get("name") or provider_id).strip()
    return AuthProvider(
        id=provider_id,
        name=name,
        type=provider_type,
        cognito_provider_name=str(entry.get("cognito_provider_name") or "").strip(),
        enabled=bool(entry.get("enabled", False)),
        display_name=str(entry.get("display_name") or name).strip(),
        icon=str(entry.get("icon") or "").strip(),
        display_order=display_order,
        secret=secret,
        attribute_mapping=attribute_mapping,
        issuer=str((oidc or {}).get("issuer") or "").strip(),
        authorize_url=str((oidc or {}).get("authorize_url") or "").strip(),
        token_url=str((oidc or {}).get("token_url") or "").strip(),
        userinfo_url=str((oidc or {}).get("userinfo_url") or "").strip(),
        jwks_uri=str((oidc or {}).get("jwks_uri") or "").strip(),
        metadata_url=str((saml or {}).get("metadata_url") or "").strip(),
    )


def _validate(providers: list[AuthProvider]) -> None:
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for provider in providers:
        if provider.id in seen_ids:
            raise RengloOpsError(f"duplicate provider id {provider.id}")
        seen_ids.add(provider.id)
        if provider.type not in PROVIDER_TYPES:
            raise RengloOpsError(f"provider {provider.id} has unknown type {provider.type}")
        if not provider.enabled:
            continue
        if not provider.cognito_provider_name:
            raise RengloOpsError(f"provider {provider.id} is missing cognito_provider_name")
        if provider.cognito_provider_name in seen_names:
            raise RengloOpsError(
                f"duplicate cognito_provider_name {provider.cognito_provider_name}"
            )
        seen_names.add(provider.cognito_provider_name)
        if provider.type == "native":
            continue
        if not provider.secret:
            raise RengloOpsError(f"provider {provider.id} is enabled but has no secret name")
        if provider.type == "social" and provider.cognito_provider_name not in SOCIAL_PROVIDER_NAMES:
            raise RengloOpsError(
                f"provider {provider.id} social name {provider.cognito_provider_name} "
                "is not a Cognito built-in"
            )
        if provider.type == "oidc" and not _oidc_complete(provider):
            raise RengloOpsError(
                f"provider {provider.id} needs oidc.issuer or authorize, token, and userinfo URLs"
            )
        if provider.type == "saml" and not provider.metadata_url and not provider.secret:
            raise RengloOpsError(f"provider {provider.id} needs saml.metadata_url or a secret")


def _oidc_complete(provider: AuthProvider) -> bool:
    if provider.issuer:
        return True
    return bool(provider.authorize_url and provider.token_url and provider.userinfo_url)


def _cognito_provider_type(provider: AuthProvider) -> str:
    if provider.type == "social":
        return provider.cognito_provider_name
    if provider.type == "oidc":
        return "OIDC"
    if provider.type == "saml":
        return "SAML"
    raise RengloOpsError(f"provider {provider.id} cannot be provisioned")


def _default_attribute_mapping() -> dict[str, str]:
    return {
        "email": "email",
        "email_verified": "email_verified",
        "given_name": "given_name",
        "family_name": "family_name",
        "username": "sub",
    }


def _detail_fields(provider: AuthProvider) -> dict[str, Any]:
    scopes = "openid email profile"
    if provider.type == "social" and provider.cognito_provider_name == "SignInWithApple":
        return {
            "client_id": (_SECRET, "client_id"),
            "team_id": (_SECRET, "team_id"),
            "key_id": (_SECRET, "key_id"),
            "private_key": (_SECRET, "private_key"),
            "authorize_scopes": scopes,
        }
    if provider.type == "social":
        return {
            "client_id": (_SECRET, "client_id"),
            "client_secret": (_SECRET, "client_secret"),
            "authorize_scopes": scopes,
        }
    if provider.type == "oidc":
        details: dict[str, Any] = {
            "client_id": (_SECRET, "client_id"),
            "client_secret": (_SECRET, "client_secret"),
            "authorize_scopes": scopes,
            "attributes_request_method": "GET",
        }
        if provider.issuer:
            details["oidc_issuer"] = provider.issuer
        else:
            details["authorize_url"] = provider.authorize_url
            details["token_url"] = provider.token_url
            details["attributes_url"] = provider.userinfo_url
            if provider.jwks_uri:
                details["jwks_uri"] = provider.jwks_uri
        return details
    if provider.type == "saml":
        if provider.metadata_url:
            return {"MetadataURL": provider.metadata_url}
        return {"MetadataFile": (_SECRET, "metadata")}
    raise RengloOpsError(f"provider {provider.id} cannot be provisioned")
