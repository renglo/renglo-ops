"""Cognito Pre Sign-up: reject external federation when the email already exists.

The decision matches renglo.auth.federated_signup.should_deny_external_signup.
Native sign-up and admin creation are left unchanged.
"""

from __future__ import annotations

_DENY_MESSAGE = (
    "An account with this email already exists. Sign in with your password."
)


def should_deny_external_signup(trigger_source: str, email_taken: bool) -> bool:
    return trigger_source == "PreSignUp_ExternalProvider" and bool(email_taken)


def lambda_handler(event, context):
    trigger = str((event or {}).get("triggerSource") or "")
    if trigger != "PreSignUp_ExternalProvider":
        return event
    email = _email(event)
    if not email:
        return event
    try:
        taken = _email_taken(str(event.get("userPoolId") or ""), email)
    except Exception as exc:
        raise Exception("Could not verify whether this email already has an account.") from exc
    if should_deny_external_signup(trigger, taken):
        raise Exception(_DENY_MESSAGE)
    return event


def _email(event: dict) -> str:
    attributes = (event.get("request") or {}).get("userAttributes") or {}
    return str(attributes.get("email") or "").strip()


def _email_taken(user_pool_id: str, email: str) -> bool:
    if not user_pool_id:
        raise ValueError("userPoolId is required")
    import boto3

    client = boto3.client("cognito-idp")
    escaped = email.replace("\\", "\\\\").replace('"', '\\"')
    response = client.list_users(
        UserPoolId=user_pool_id,
        Filter=f'email = "{escaped}"',
        Limit=1,
    )
    return bool(response.get("Users"))
