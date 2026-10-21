"""CognitoStack: Cognito user pool + app client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import Aws, CfnOutput, Duration, Fn, RemovalPolicy, SecretValue, Tags
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as aws_lambda
from constructs import Construct

from platform_defaults import cognito_token_validity_hours
from renglo_ops.auth.providers import AuthProvider, detail_fields

_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "pre_signup"

_LOCAL_DEV_CALLBACK_URLS = (
    "http://localhost:5173/",
    "http://localhost:5173/callback",
    "http://127.0.0.1:5173/",
    "http://127.0.0.1:5173/callback",
    "http://localhost:5174/",
    "http://localhost:5174/callback",
    "http://127.0.0.1:5174/",
    "http://127.0.0.1:5174/callback",
)
_LOCAL_DEV_LOGOUT_URLS = (
    "http://localhost:5173/",
    "http://localhost:5173/login",
    "http://127.0.0.1:5173/",
    "http://127.0.0.1:5173/login",
    "http://localhost:5174/",
    "http://localhost:5174/login",
    "http://127.0.0.1:5174/",
    "http://127.0.0.1:5174/login",
)

_SUPPORTED = {
    "Google": cognito.UserPoolClientIdentityProvider.GOOGLE,
    "Facebook": cognito.UserPoolClientIdentityProvider.FACEBOOK,
    "LoginWithAmazon": cognito.UserPoolClientIdentityProvider.AMAZON,
    "SignInWithApple": cognito.UserPoolClientIdentityProvider.APPLE,
}


def _admin_user_invitation(console_setup_base_url: Any) -> cognito.UserInvitationConfig:
    """Invitation email for admin-create-user (must include {username} and {####})."""
    setup_link = Fn.join("", [console_setup_base_url, "invite?setup=admin&email={username}"])
    email_body = Fn.join(
        "",
        [
            "Your admin account has been created.\n\n",
            "Email: {username}\n",
            "Temporary password (copy only the password below — not any punctuation after it):\n",
            "{####}\n\n",
            "Open this link to enter your name and set a new password:\n",
            setup_link,
            "\n",
        ],
    )
    return cognito.UserInvitationConfig(
        email_subject="Complete your admin account setup",
        email_body=email_body,
        sms_message=(
            "Admin account created. Username: {username}. "
            "Temporary password: {####}. "
            "Complete setup in the console invite screen."
        ),
    )


class AuthStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        aws_region: str,
        console_setup_base_url: Any,
        console_callback_urls: list[str] | None = None,
        console_logout_urls: list[str] | None = None,
        auth_providers: list[AuthProvider] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=env_name,
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            user_invitation=_admin_user_invitation(console_setup_base_url),
            removal_policy=RemovalPolicy.DESTROY,
        )
        Tags.of(user_pool).add("renglo:env", env_name)

        user_pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=env_name),
        )

        external = [item for item in (auth_providers or []) if item.type != "native" and item.enabled]
        identity_providers = [
            _identity_provider(self, user_pool, provider) for provider in external
        ]
        _add_pre_signup(self, user_pool, env_name=env_name)

        logout_urls = [*(console_logout_urls or []), *_LOCAL_DEV_LOGOUT_URLS]
        callback_urls = [*(console_callback_urls or []), *_LOCAL_DEV_CALLBACK_URLS]
        oauth_settings = None
        if console_callback_urls or external:
            oauth_settings = cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=callback_urls,
                logout_urls=logout_urls,
            )

        supported = None
        if external:
            supported = [cognito.UserPoolClientIdentityProvider.COGNITO]
            for provider in external:
                supported.append(_supported_provider(provider))

        token_validity = Duration.hours(cognito_token_validity_hours())

        client_kwargs: dict[str, Any] = {}
        if supported:
            client_kwargs["supported_identity_providers"] = supported

        app_client = user_pool.add_client(
            "AppClient",
            user_pool_client_name=f"{env_name}_app",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            access_token_validity=token_validity,
            id_token_validity=token_validity,
            o_auth=oauth_settings,
            **client_kwargs,
        )
        for identity_provider in identity_providers:
            app_client.node.add_dependency(identity_provider)

        cognito_domain = f"{env_name}.auth.{aws_region}.amazoncognito.com"

        self.user_pool = user_pool
        self.user_pool_id = user_pool.user_pool_id
        self.user_pool_arn = user_pool.user_pool_arn
        self.app_client_id = app_client.user_pool_client_id
        self.cognito_domain = cognito_domain

        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolArn", value=user_pool.user_pool_arn)
        CfnOutput(self, "AppClientId", value=app_client.user_pool_client_id)
        CfnOutput(self, "CognitoDomain", value=cognito_domain)


def _supported_provider(provider: AuthProvider):
    if provider.type == "social":
        try:
            return _SUPPORTED[provider.cognito_provider_name]
        except KeyError as exc:
            raise ValueError(
                f"provider {provider.id} is not a Cognito social provider"
            ) from exc
    return cognito.UserPoolClientIdentityProvider.custom(provider.cognito_provider_name)


def _identity_provider(scope: Construct, user_pool: cognito.UserPool, provider: AuthProvider):
    details: dict[str, str] = {}
    for key, value in detail_fields(provider).items():
        if isinstance(value, tuple) and value and value[0] == "secret":
            details[key] = SecretValue.secrets_manager(
                provider.secret,
                json_field=value[1],
            ).unsafe_unwrap()
        else:
            details[key] = str(value)
    safe_id = "".join(ch if ch.isalnum() else "" for ch in provider.id) or "provider"
    return cognito.CfnUserPoolIdentityProvider(
        scope,
        f"IdP{safe_id}",
        user_pool_id=user_pool.user_pool_id,
        provider_name=provider.cognito_provider_name,
        provider_type=_cfn_provider_type(provider),
        provider_details=details,
        attribute_mapping=dict(provider.attribute_mapping),
    )


def _cfn_provider_type(provider: AuthProvider) -> str:
    if provider.type == "social":
        return provider.cognito_provider_name
    if provider.type == "oidc":
        return "OIDC"
    if provider.type == "saml":
        return "SAML"
    raise ValueError(f"provider {provider.id} cannot be provisioned")


def _add_pre_signup(scope: Construct, user_pool: cognito.UserPool, *, env_name: str) -> None:
    fn = aws_lambda.Function(
        scope,
        "PreSignUp",
        function_name=f"{env_name}-auth-pre-signup",
        runtime=aws_lambda.Runtime.PYTHON_3_12,
        handler="handler.lambda_handler",
        code=aws_lambda.Code.from_asset(str(_ASSET_DIR)),
        timeout=Duration.seconds(10),
        memory_size=128,
    )
    # Do not use user_pool.user_pool_arn here. The pool's Pre Sign-up trigger
    # already references this function, so an ARN that Refs the pool makes
    # CloudFormation reject the stack (pool -> function -> policy -> pool).
    # The event carries the pool id; this account-wide resource only allows
    # the lookup and provider link the handler already scopes to that id.
    fn.add_to_role_policy(
        iam.PolicyStatement(
            actions=[
                "cognito-idp:ListUsers",
                "cognito-idp:AdminLinkProviderForUser",
            ],
            resources=[
                f"arn:{Aws.PARTITION}:cognito-idp:{Aws.REGION}:{Aws.ACCOUNT_ID}:userpool/*"
            ],
        )
    )
    user_pool.add_trigger(cognito.UserPoolOperation.PRE_SIGN_UP, fn)
