"""SES identity for application transactional email (team invites, etc.).

SES does not provide an anonymous AWS-owned from-address for SendEmail.
Configure an application-owned address via customer-config ``email_from``.

DNS / verification modes (resilient for tenants with or without Route53):

- ``email`` identity: SES emails a verification link to ``email_from`` (needs an
  inbox that can receive that one message). No Route53 required.
- ``domain`` identity + ``email_hosted_zone_id``: verify the domain and create
  DKIM (and related) records in that public hosted zone automatically.
- ``domain`` identity without a hosted zone id: create the SES domain identity
  only and export DKIM record names/values for the operator to add at their
  DNS provider (GoDaddy, Cloudflare, etc.).
"""

from __future__ import annotations

from aws_cdk import CfnOutput
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_ses as ses
from constructs import Construct


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

        domain = email_from.split("@", 1)[1].strip().lower()
        zone_id = (email_hosted_zone_id or "").strip()
        dns_mode = "email_inbox"
        route53_auto = False

        if identity_type == "domain" and zone_id:
            # Import existing public zone — no Route53 lookup at synth time.
            # zone_name must equal the email domain for SES publicHostedZone().
            hosted_zone = route53.PublicHostedZone.from_public_hosted_zone_attributes(
                self,
                "EmailHostedZone",
                hosted_zone_id=zone_id,
                zone_name=domain,
            )
            identity = ses.EmailIdentity(
                self,
                "InviteFromIdentity",
                identity=ses.Identity.public_hosted_zone(hosted_zone),
            )
            identity_name = domain
            dns_mode = "route53_auto"
            route53_auto = True
        elif identity_type == "domain":
            identity = ses.EmailIdentity(
                self,
                "InviteFromIdentity",
                identity=ses.Identity.domain(domain),
            )
            identity_name = domain
            dns_mode = "manual_dns"
            # Export DKIM CNAMEs so operators can paste them into any DNS host.
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
        self.email_identity_arn = identity.email_identity_arn
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
                "route53_auto: DKIM records created in email_hosted_zone_id. "
                "manual_dns: add DkimRecord* outputs at your DNS provider. "
                "email_inbox: confirm the SES verification email to email_from."
            ),
        )
        if zone_id and route53_auto:
            CfnOutput(self, "EmailHostedZoneId", value=zone_id)
        CfnOutput(self, "EnvName", value=env_name)
