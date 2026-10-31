"""Provider catalog validation and Cognito request shapes."""

from pathlib import Path

import pytest
import yaml

from renglo.cli import main
from renglo.help import render
from renglo_ops.auth.providers import (
    catalog_from_data,
    cognito_provider_request,
    load_provider_catalog,
    reference_public_providers,
    secret_name_for,
)
from renglo_ops.auth.scaffold import install_auth_config, starter_providers_yaml
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


def _tenant_yaml(name: str = "liga1030") -> str:
    return f"""
name: {name}
github:
  repo: Ligaajusco/liga-bom
email:
  from: noreply@example.com
  identity: email
accounts:
  staging:
    id: "339713094352"
    region: us-east-1
    enabled: true
"""


def test_starter_enables_email_and_google_and_names_secrets():
    text = starter_providers_yaml("liga1030")
    catalog = catalog_from_data(yaml.safe_load(text), env_name="liga1030", source="starter")
    assert [item.id for item in catalog.enabled()] == ["cognito", "google"]
    google = catalog.external_enabled()[0]
    assert google.secret == secret_name_for("liga1030", "google")
    assert "liga1030/renglo/auth/okta" in text
    assert "liga1030/renglo/auth/facebook" in text
    assert "__GOOGLE__" not in text

    with pytest.raises(RengloOpsError, match="environment name"):
        starter_providers_yaml("liga/1030")


def test_auth_init_writes_the_bom_file(tmp_path: Path, monkeypatch, capsys) -> None:
    bom = tmp_path / "ops" / "liga-bom"
    bom.mkdir(parents=True)
    (bom / "renglo.yaml").write_text(_tenant_yaml(), encoding="utf-8")
    monkeypatch.delenv("RENGLO_TENANT", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".renglo").mkdir()
    (tmp_path / ".renglo" / "local.yaml").write_text(
        "tenant: ops/liga-bom\nregion: us-east-1\n",
        encoding="utf-8",
    )

    assert main(["auth", "init", "--dry-run"]) == 0
    assert "would write" in capsys.readouterr().out
    assert not (bom / "auth").exists()

    assert main(["auth", "init"]) == 0
    written = bom / "auth" / "providers.yaml"
    assert written.is_file()
    out = capsys.readouterr().out
    assert "wrote" in out
    assert "liga1030/renglo/auth/google" in out
    catalog = load_provider_catalog(written, env_name="liga1030")
    assert [item["id"] for item in catalog.public_providers()] == ["cognito", "google"]

    assert main(["auth", "init"]) == 0
    assert "kept" in capsys.readouterr().out
    assert written.read_text(encoding="utf-8") == starter_providers_yaml("liga1030")


def test_auth_init_refuses_to_replace_a_different_file(tmp_path: Path, monkeypatch, capsys) -> None:
    bom = tmp_path / "liga-bom"
    bom.mkdir()
    (bom / "renglo.yaml").write_text(_tenant_yaml("acme1"), encoding="utf-8")
    dest = bom / "auth" / "providers.yaml"
    dest.parent.mkdir()
    dest.write_text("providers: []\n", encoding="utf-8")
    monkeypatch.delenv("RENGLO_TENANT", raising=False)
    monkeypatch.chdir(tmp_path)

    try:
        main(["auth", "init", "--bom", str(bom)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected refusal")
    assert "Pass --force" in capsys.readouterr().err
    assert dest.read_text(encoding="utf-8") == "providers: []\n"

    assert main(["auth", "init", "--bom", str(bom), "--force"]) == 0
    assert secret_name_for("acme1", "google") in dest.read_text(encoding="utf-8")


def test_auth_init_refuses_the_tool_checkout() -> None:
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(RengloOpsError, match="renglo-ops"):
        install_auth_config(root, "liga1030")


def test_help_lists_auth_init() -> None:
    auth = render("auth")
    assert "renglo auth init" in auth
    assert "renglo auth init --dry-run" in auth
    assert "renglo stack deploy --stack a,b" in auth
    assert "renglo registry deploy" not in auth


def test_reference_document_offers_email_google_and_okta():
    published = reference_public_providers()
    assert [item["cognito_provider_name"] for item in published] == ["COGNITO", "Google", "Okta"]
    assert [item["type"] for item in published] == ["native", "social", "oidc"]
    assert all("secret" not in item for item in published)
    assert published[1]["display_name"] == "Continue with Google"
    assert published[2]["display_name"] == "Continue with Okta"
