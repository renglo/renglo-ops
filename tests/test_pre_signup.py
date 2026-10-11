"""Pre Sign-up Lambda denies external federation when the email is taken."""

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


def test_external_signup_decision_matches_every_provider():
    handler = _handler()
    assert handler.should_deny_external_signup("PreSignUp_ExternalProvider", True)
    assert not handler.should_deny_external_signup("PreSignUp_ExternalProvider", False)
    assert not handler.should_deny_external_signup("PreSignUp_SignUp", True)
    assert not handler.should_deny_external_signup("PreSignUp_AdminCreateUser", True)


def test_native_event_is_returned_without_calling_cognito():
    handler = _handler()
    event = {"triggerSource": "PreSignUp_AdminCreateUser", "request": {"userAttributes": {}}}
    assert handler.lambda_handler(event, None) is event
