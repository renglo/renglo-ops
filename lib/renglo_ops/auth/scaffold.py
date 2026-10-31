"""Write the starting auth/providers.yaml into a tenant BOM repo.

The file names Secrets Manager entries. It does not contain client secrets.
The shape matches a working tenant file: email and password, Google enabled,
and Okta and Meta present but disabled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from renglo_ops.auth.providers import catalog_from_data, secret_name_for
from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.local import refuse_tool_output

_ENV_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RELATIVE = Path("auth") / "providers.yaml"

_STARTER = """\
# Authentication providers for this implementation.
# Client secrets are not stored here. Each non-native entry names a Secrets
# Manager secret in this account: {"client_id": "...", "client_secret": "..."}.
# Create __GOOGLE__ before deploying the hub stack with
# Google enabled. The identity provider's redirect URI is the Cognito
# /oauth2/idpresponse URL, not the Console callback.
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
    secret: __GOOGLE__
    attribute_mapping:
      email: email
      email_verified: email_verified
      given_name: given_name
      family_name: family_name
      username: sub

  # Further providers are more entries. Leave them disabled until the secret exists.
  - id: okta
    name: Okta
    type: oidc
    cognito_provider_name: Okta
    enabled: false
    display_name: Continue with Okta
    icon: okta
    display_order: 20
    secret: __OKTA__
    oidc:
      issuer: https://example.okta.com
    attribute_mapping:
      email: email
      email_verified: email_verified
      given_name: given_name
      family_name: family_name
      username: sub

  - id: facebook
    name: Meta
    type: social
    cognito_provider_name: Facebook
    enabled: false
    display_name: Continue with Meta
    icon: facebook
    display_order: 30
    secret: __FACEBOOK__
    attribute_mapping:
      email: email
      given_name: given_name
      family_name: family_name
      username: sub
"""


@dataclass(frozen=True)
class AuthConfigWrite:
    path: Path
    action: str


def require_env_name(env_name: str) -> str:
    """Environment names become the first segment of a Secrets Manager name."""
    name = env_name.strip()
    if not _ENV_NAME.fullmatch(name):
        raise RengloOpsError(
            "environment name must be letters, digits, '.', '_', or '-'; "
            f"got {env_name!r}"
        )
    return name


def starter_providers_yaml(env_name: str) -> str:
    """providers.yaml text for one environment. Google is enabled; Okta and Meta are not."""
    name = require_env_name(env_name)
    text = (
        _STARTER.replace("__GOOGLE__", secret_name_for(name, "google"))
        .replace("__OKTA__", secret_name_for(name, "okta"))
        .replace("__FACEBOOK__", secret_name_for(name, "facebook"))
    )
    catalog_from_data(yaml.safe_load(text), env_name=name, source="starter")
    return text


def install_auth_config(
    bom_root: Path,
    env_name: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> AuthConfigWrite:
    """Create auth/providers.yaml next to renglo.yaml. An identical file is left in place."""
    dest = (bom_root.expanduser().resolve() / _RELATIVE)
    refuse_tool_output(dest)
    text = starter_providers_yaml(env_name)
    if dest.exists() and not dest.is_file():
        raise RengloOpsError(f"{dest} exists and is not a file")
    if dest.is_file() and dest.read_text(encoding="utf-8") == text:
        return AuthConfigWrite(dest, "kept")
    if dest.exists() and not force:
        raise RengloOpsError(f"{dest} already exists. Pass --force to replace it")
    parent = dest.parent
    if parent.exists() and not parent.is_dir():
        raise RengloOpsError(f"{parent} is not a directory")
    if dry_run:
        return AuthConfigWrite(dest, "would-write")
    parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return AuthConfigWrite(dest, "wrote")
