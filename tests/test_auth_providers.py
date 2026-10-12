"""Provider catalog validation and Cognito request shapes."""

from pathlib import Path

import pytest

from renglo_ops.auth.providers import (
    catalog_from_data,
    cognito_provider_request,
    load_provider_catalog,
    reference_public_providers,
)
from renglo_ops.model.errors import RengloOpsError


def _entry(**overrides):
    base = {
        "id": "google",
        "name": "Google",
        "type": "social",
        "cognito_provider_name": "Google",
        "enabled": True,
        "display_name": "Continue with Google",
        "icon": "google",
        "display_order": 10,
        "secret": "example/renglo/auth/google",
    }
    base.update(overrides)
    return base


def _catalog(*providers, env_name="example"):
    return catalog_from_data({"providers": list(providers)}, env_name=env_name)


def test_missing_file_is_native_only():
    catalog = load_provider_catalog(Path("/no/such/auth/providers.yaml"), env_name="example")
    assert catalog.providers == []
    assert catalog.public_json() == "[]"
    assert catalog.external_enabled() == []


def test_disabled_provider_is_omitted_and_unknown_type_is_refused():
    catalog = _catalog(
        {
            "id": "cognito",
            "name": "Email and Password",
            "type": "native",
            "cognito_provider_name": "COGNITO",
            "enabled": True,
            "display_name": "Email and password",
            "display_order": 0,
        },
        _entry(enabled=False),
    )
    published = catalog.public_providers()
    assert [item["id"] for item in published] == ["cognito"]
    assert "secret" not in published[0]

    with pytest.raises(RengloOpsError, match="unknown type"):
        _catalog(_entry(id="other", type="ldap", cognito_provider_name="Ldap", enabled=True))


def test_enabled_provider_without_secret_name_fails():
    with pytest.raises(RengloOpsError, match="no secret name"):
        _catalog(_entry(secret=""), env_name="")


def test_same_loader_builds_social_oidc_and_saml_requests():
    catalog = _catalog(
        _entry(),
        _entry(
            id="facebook",
            name="Meta",
            cognito_provider_name="Facebook",
            secret="example/renglo/auth/facebook",
        ),
        _entry(
            id="okta",
            name="Okta",
            type="oidc",
            cognito_provider_name="Okta",
            secret="example/renglo/auth/okta",
            oidc={"issuer": "https://example.okta.com"},
        ),
        _entry(
            id="okta-saml",
            name="Okta SAML",
            type="saml",
            cognito_provider_name="OktaSAML",
            secret="example/renglo/auth/okta-saml",
            saml={"metadata_url": "https://example.okta.com/metadata"},
        ),
    )
    by_id = {item.id: cognito_provider_request(item) for item in catalog.external_enabled()}

    assert by_id["google"]["ProviderType"] == "Google"
    assert by_id["google"]["ProviderDetails"]["client_secret"] == (
        "secret:example/renglo/auth/google:client_secret"
    )
    assert catalog.public_json().find("client_secret") == -1

    assert by_id["facebook"]["ProviderType"] == "Facebook"
    assert by_id["facebook"]["ProviderName"] == "Facebook"

    assert by_id["okta"]["ProviderType"] == "OIDC"
    assert by_id["okta"]["ProviderDetails"]["oidc_issuer"] == "https://example.okta.com"
    assert by_id["okta"]["AttributeMapping"]["username"] == "sub"

    assert by_id["okta-saml"]["ProviderType"] == "SAML"
    assert by_id["okta-saml"]["ProviderDetails"]["MetadataURL"] == (
        "https://example.okta.com/metadata"
    )


def test_reference_document_offers_email_google_and_okta():
    published = reference_public_providers()
    assert [item["cognito_provider_name"] for item in published] == ["COGNITO", "Google", "Okta"]
    assert [item["type"] for item in published] == ["native", "social", "oidc"]
    assert all("secret" not in item for item in published)
    assert published[1]["display_name"] == "Continue with Google"
    assert published[2]["display_name"] == "Continue with Okta"
