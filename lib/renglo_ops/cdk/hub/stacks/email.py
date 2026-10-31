"""SES identity for application transactional email (team invites, etc.).

SES does not provide an anonymous AWS-owned from-address for SendEmail.
Configure an application-owned address via customer-config ``email_from``.

DNS / verification modes (resilient for tenants with or without Route53):

- ``email`` identity: SES emails a verification link to ``email_from`` (needs an
  inbox that can receive that one message). No Route53 required.
- ``domain`` identity: verify the domain in ``email.from``. When that SES
  identity is already in the account, this stack reuses it and does not create
  DNS records. Otherwise it creates the domain identity, writing DKIM into
  ``email_hosted_zone_id`` when that is set.
"""

from __future__ import annotations

import os

from aws_cdk import ArnFormat, CfnOutput, Stack
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_ses as ses
from constructs import Construct

from renglo_ops.target_role import domain_identity_name


def ses_identity_registered(identity_name: str) -> bool:
    """True when this account already has the SES identity. A lookup failure refuses to create another one."""
    import boto3

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("CDK_DEFAULT_REGION")
        or "us-east-1"
    )
    client = boto3.client("ses", region_name=region)
    try:
        result = client.get_identity_verification_attributes(Identities=[identity_name])
    except Exception as exc:
        raise ValueError(
            f"Could not check whether SES identity {identity_name} already exists ({exc}). "
            "Refusing to create a second copy."
        ) from exc
    return identity_name in (result.get("VerificationAttributes") or {})


def _ses_identity_arn(identity: ses.EmailIdentity, identity_name: str) -> str:
    """ARN for IAM / runtime policy. Newer aws-cdk-lib exposes ``email_identity_arn``; 2.114.x does not."""
    direct = getattr(identity, "email_identity_arn", None)
    if direct is not None:
        return direct
    stack = Stack.of(identity)
    return stack.format_arn(
        service="ses",
        resource="identity",
        resource_name=identity_name,
        arn_format=ArnFormat.SLASH_RESOURCE_NAME,
    )


class EmailStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        email_from: str,
        email_identity_type: str = "email",
        email_hosted_zone_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        email_from = email_from.strip()
        if not email_from or "@" not in email_from:
            raise ValueError(
                "customer-config.json: email_from must be a valid address "
                "(e.g. no-reply@your-app-domain.com)"
            )

        identity_type = (email_identity_type or "email").strip().lower()
        if identity_type not in ("email", "domain"):
            raise ValueError(
                "customer-config.json: email_identity_type must be 'email' or 'domain'"
            )

        zone_id = (email_hosted_zone_id or "").strip()
        dns_mode = "email_inbox"
        route53_auto = False

        if identity_type == "domain":
            identity_name = domain_identity_name(email_from)
            if ses_identity_registered(identity_name):
                identity = ses.EmailIdentity.from_email_identity_name(
                    self,
                    "InviteFromIdentity",
                    identity_name,
                )
                dns_mode = "shared"
            elif zone_id:
                hosted_zone = route53.PublicHostedZone.from_public_hosted_zone_attributes(
                    self,
                    "EmailHostedZone",
                    hosted_zone_id=zone_id,
                    zone_name=identity_name,
                )
                identity = ses.EmailIdentity(
                    self,
                    "InviteFromIdentity",
                    identity=ses.Identity.public_hosted_zone(hosted_zone),
                )
                dns_mode = "route53_auto"
                route53_auto = True
            else:
                identity = ses.EmailIdentity(
                    self,
                    "InviteFromIdentity",
                    identity=ses.Identity.domain(identity_name),
                )
                dns_mode = "manual_dns"
                for index, record in enumerate(identity.dkim_records, start=1):
                    CfnOutput(
                        self,
                        f"DkimRecord{index}Name",
                        value=record.name,
                        description=f"DKIM CNAME name #{index} — add at your DNS provider",
                    )
                    CfnOutput(
                        self,
                        f"DkimRecord{index}Value",
                        value=record.value,
                        description=f"DKIM CNAME value #{index}",
                    )
        else:
            if zone_id:
                # Zone id is irrelevant for single-email verification; ignore safely.
                CfnOutput(
                    self,
                    "EmailHostedZoneIgnored",
                    value=zone_id,
                    description=(
                        "email_hosted_zone_id is ignored when email_identity_type=email"
                    ),
                )
            identity = ses.EmailIdentity(
                self,
                "InviteFromIdentity",
                identity=ses.Identity.email(email_from),
            )
            identity_name = email_from
            dns_mode = "email_inbox"

        self.email_from = email_from
        self.email_identity = identity
        self.email_identity_name = identity_name
        self.email_identity_arn = _ses_identity_arn(identity, identity_name)
        self.dns_mode = dns_mode
        self.route53_auto = route53_auto

        CfnOutput(self, "FromEmail", value=email_from)
        CfnOutput(self, "SesIdentityName", value=identity_name)
        CfnOutput(self, "SesIdentityType", value=identity_type)
        CfnOutput(
            self,
            "SesDnsMode",
            value=dns_mode,
            description=(
                "shared: this account already has the domain identity; no DNS records are created. "
                "route53_auto: DKIM records created in email_hosted_zone_id. "
                "manual_dns: add DkimRecord* outputs at your DNS provider. "
                "email_inbox: confirm the SES verification email to email_from."
            ),
        )
        if zone_id and route53_auto:
            CfnOutput(self, "EmailHostedZoneId", value=zone_id)
        CfnOutput(self, "EnvName", value=env_name)
