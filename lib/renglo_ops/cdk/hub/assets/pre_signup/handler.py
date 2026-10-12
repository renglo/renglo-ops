"""Cognito Pre Sign-up: link a verified external identity to the existing user.

The decision matches renglo.auth.federated_signup.external_signup_action.
Native sign-up and admin creation are left unchanged. Cognito cannot finish
the current attempt as the existing user, so a successful link raises
ACCOUNT_LINKED and the Console starts the same provider sign-in once more.
"""

from __future__ import annotations

EXTERNAL_SIGNUP = "PreSignUp_ExternalProvider"
ACCOUNT_LINKED = "ACCOUNT_LINKED"
_DENY_MESSAGE = (
    "An account with this email already exists. Sign in with your password."
)
_LOOKUP_MESSAGE = "Could not verify whether this email already has an account."
_LINK_MESSAGE = "Could not link this sign-in to the existing account."
# Google verifies the address before it returns the email scope. Cognito still
# reports email_verified as false until that claim is mapped on the provider.
_TRUSTED_EMAIL_PROVIDERS = {"Google"}


def email_is_verified(value: object) -> bool:
    return str(value or "").strip().lower() == "true"


def external_signup_action(
    trigger_source: str,
    *,
    email_taken: bool,
    email_verified: bool,
    can_link: bool,
) -> str:
    """Return allow, link, or deny. Keep this identical to renglo.auth.federated_signup."""
    if trigger_source != EXTERNAL_SIGNUP or not email_taken:
        return "allow"
    if email_verified and can_link:
        return "link"
    return "deny"


def split_provider_username(username: str) -> tuple[str, str] | None:
    """Return (provider name, subject) from a Cognito federated username."""
    provider_name, separator, subject = str(username or "").partition("_")
    if not separator or not provider_name or not subject:
        return None
    return provider_name, subject


def choose_link_user(users: list[dict]) -> dict | None:
    """Prefer an enabled password user over another federated profile."""
    enabled = [
        user
        for user in users
        if user.get("Enabled", True) and str(user.get("Username") or "").strip()
    ]
    native = [
        user
        for user in enabled
        if str(user.get("UserStatus") or "") != "EXTERNAL_PROVIDER"
    ]
    pool = native or enabled
    if not pool:
        return None
    verified = [user for user in pool if _attribute(user, "email_verified") == "true"]
    return (verified or pool)[0]


def destination_user(user: dict | None) -> dict | None:
    if not user:
        return None
    username = str(user.get("Username") or "").strip()
    if not username:
        return None
    if str(user.get("UserStatus") or "") == "EXTERNAL_PROVIDER":
        parsed = split_provider_username(username)
        if not parsed:
            return None
        provider_name, subject = parsed
        return {
            "ProviderName": provider_name,
            "ProviderAttributeName": "Cognito_Subject",
            "ProviderAttributeValue": subject,
        }
    return {
        "ProviderName": "Cognito",
        "ProviderAttributeValue": username,
    }


def lambda_handler(event, context):
    trigger = str((event or {}).get("triggerSource") or "")
    if trigger != EXTERNAL_SIGNUP:
        return event
    email = _email(event)
    if not email:
        return event
    pool_id = str((event or {}).get("userPoolId") or "")
    if not pool_id:
        raise Exception(_LOOKUP_MESSAGE)
    try:
        client = _cognito_client()
        users = _users_with_email(client, pool_id, email)
    except Exception as exc:
        raise Exception(_LOOKUP_MESSAGE) from exc
    source = split_provider_username(str((event or {}).get("userName") or ""))
    destination = destination_user(choose_link_user(users))
    provider_name = source[0] if source else ""
    verified = email_is_verified(_attributes(event).get("email_verified")) or (
        provider_name in _TRUSTED_EMAIL_PROVIDERS
    )
    action = external_signup_action(
        trigger,
        email_taken=bool(users),
        email_verified=verified,
        can_link=destination is not None and source is not None,
    )
    if action == "allow":
        return event
    if action == "deny" or destination is None or source is None:
        raise Exception(_DENY_MESSAGE)
    provider_name, provider_subject = source
    try:
        _link_provider(
            client,
            pool_id,
            destination=destination,
            provider_name=provider_name,
            provider_subject=provider_subject,
        )
    except Exception as exc:
        raise Exception(_LINK_MESSAGE) from exc
    raise Exception(ACCOUNT_LINKED)


def _email(event: dict) -> str:
    return str(_attributes(event).get("email") or "").strip()


def _attributes(event: dict) -> dict:
    return (event.get("request") or {}).get("userAttributes") or {}


def _attribute(user: dict, name: str) -> str:
    for item in user.get("Attributes") or []:
        if item.get("Name") == name:
            return str(item.get("Value") or "").strip().lower()
    return ""


def _cognito_client():
    import boto3

    return boto3.client("cognito-idp")


def _users_with_email(client, user_pool_id: str, email: str) -> list:
    escaped = email.replace("\\", "\\\\").replace('"', '\\"')
    found: list = []
    token = None
    for _ in range(5):
        kwargs = {
            "UserPoolId": user_pool_id,
            "Filter": f'email = "{escaped}"',
            "Limit": 60,
        }
        if token:
            kwargs["PaginationToken"] = token
        response = client.list_users(**kwargs)
        found.extend(response.get("Users") or [])
        token = response.get("PaginationToken")
        if not token:
            break
    return found


def _already_linked(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    error = response.get("Error") or {}
    code = str(error.get("Code") or "")
    message = str(error.get("Message") or exc).lower()
    if code == "AliasExistsException":
        return True
    return "already" in message and "link" in message


def _link_provider(
    client,
    user_pool_id: str,
    *,
    destination: dict,
    provider_name: str,
    provider_subject: str,
) -> None:
    try:
        client.admin_link_provider_for_user(
            UserPoolId=user_pool_id,
            DestinationUser=destination,
            SourceUser={
                "ProviderName": provider_name,
                "ProviderAttributeName": "Cognito_Subject",
                "ProviderAttributeValue": provider_subject,
            },
        )
    except Exception as exc:
        if _already_linked(exc):
            return
        raise
