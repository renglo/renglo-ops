"""Pre Sign-up links a verified external identity onto the existing user."""

import importlib.util
from pathlib import Path

_HANDLER = (
    Path(__file__).resolve().parents[1]
    / "lib"
    / "renglo_ops"
    / "cdk"
    / "hub"
    / "assets"
    / "pre_signup"
    / "handler.py"
)


def _handler():
    spec = importlib.util.spec_from_file_location("pre_signup_handler", _HANDLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _native_user():
    return {
        "Username": "native-user",
        "Enabled": True,
        "UserStatus": "CONFIRMED",
        "Attributes": [
            {"Name": "email", "Value": "ada@example.com"},
            {"Name": "email_verified", "Value": "true"},
        ],
    }


class _Client:
    def __init__(self, users, link_error=None):
        self.users = users
        self.link_error = link_error
        self.linked = None

    def list_users(self, **kwargs):
        assert kwargs["UserPoolId"] == "pool"
        assert kwargs["Filter"] == 'email = "ada@example.com"'
        return {"Users": self.users}

    def admin_link_provider_for_user(self, **kwargs):
        if self.link_error:
            raise self.link_error
        self.linked = kwargs


def _external_event(*, verified="true", username="Google_1099"):
    return {
        "triggerSource": "PreSignUp_ExternalProvider",
        "userPoolId": "pool",
        "userName": username,
        "request": {
            "userAttributes": {
                "email": "ada@example.com",
                "email_verified": verified,
            }
        },
    }


def test_external_signup_links_a_verified_existing_email():
    handler = _handler()
    assert (
        handler.external_signup_action(
            "PreSignUp_ExternalProvider",
            email_taken=True,
            email_verified=True,
            can_link=True,
        )
        == "link"
    )
    assert (
        handler.external_signup_action(
            "PreSignUp_ExternalProvider",
            email_taken=False,
            email_verified=True,
            can_link=False,
        )
        == "allow"
    )
    assert (
        handler.external_signup_action(
            "PreSignUp_SignUp",
            email_taken=True,
            email_verified=True,
            can_link=True,
        )
        == "allow"
    )


def test_native_event_is_returned_without_calling_cognito():
    handler = _handler()
    event = {"triggerSource": "PreSignUp_AdminCreateUser", "request": {"userAttributes": {}}}
    assert handler.lambda_handler(event, None) is event


def test_existing_verified_email_links_google_and_stops_the_new_user(monkeypatch):
    handler = _handler()
    client = _Client([_native_user()])
    monkeypatch.setattr(handler, "_cognito_client", lambda: client)
    try:
        handler.lambda_handler(_external_event(), None)
    except Exception as exc:
        assert str(exc) == "ACCOUNT_LINKED"
    else:
        raise AssertionError("expected the linked sign-up to stop")
    assert client.linked["DestinationUser"] == {
        "ProviderName": "Cognito",
        "ProviderAttributeValue": "native-user",
    }
    assert client.linked["SourceUser"] == {
        "ProviderName": "Google",
        "ProviderAttributeName": "Cognito_Subject",
        "ProviderAttributeValue": "1099",
    }


def test_password_user_wins_over_another_federated_profile(monkeypatch):
    handler = _handler()
    federated = {
        "Username": "Okta_abc",
        "Enabled": True,
        "UserStatus": "EXTERNAL_PROVIDER",
        "Attributes": [
            {"Name": "email", "Value": "ada@example.com"},
            {"Name": "email_verified", "Value": "true"},
        ],
    }
    client = _Client([federated, _native_user()])
    monkeypatch.setattr(handler, "_cognito_client", lambda: client)
    try:
        handler.lambda_handler(_external_event(), None)
    except Exception as exc:
        assert str(exc) == "ACCOUNT_LINKED"
    else:
        raise AssertionError("expected the linked sign-up to stop")
    assert client.linked["DestinationUser"]["ProviderAttributeValue"] == "native-user"


def test_google_links_when_cognito_reports_the_email_unverified(monkeypatch):
    handler = _handler()
    client = _Client([_native_user()])
    monkeypatch.setattr(handler, "_cognito_client", lambda: client)
    try:
        handler.lambda_handler(_external_event(verified="false"), None)
    except Exception as exc:
        assert str(exc) == "ACCOUNT_LINKED"
    else:
        raise AssertionError("expected Google to link onto the existing user")
    assert client.linked["SourceUser"]["ProviderName"] == "Google"


def test_unverified_oidc_email_does_not_link(monkeypatch):
    handler = _handler()
    client = _Client([_native_user()])
    monkeypatch.setattr(handler, "_cognito_client", lambda: client)
    try:
        handler.lambda_handler(_external_event(verified="false", username="Okta_subject"), None)
    except Exception as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected an unverified email to be rejected")
    assert client.linked is None


def test_new_email_is_allowed(monkeypatch):
    handler = _handler()
    client = _Client([])
    monkeypatch.setattr(handler, "_cognito_client", lambda: client)
    event = _external_event()
    assert handler.lambda_handler(event, None) is event
    assert client.linked is None


def test_already_linked_provider_still_asks_for_another_sign_in(monkeypatch):
    handler = _handler()

    class _Already(Exception):
        def __init__(self):
            super().__init__("User already linked")
            self.response = {
                "Error": {"Code": "AliasExistsException", "Message": "User already linked"}
            }

    client = _Client([_native_user()], link_error=_Already())
    monkeypatch.setattr(handler, "_cognito_client", lambda: client)
    try:
        handler.lambda_handler(_external_event(), None)
    except Exception as exc:
        assert str(exc) == "ACCOUNT_LINKED"
    else:
        raise AssertionError("expected another sign-in after an existing link")
