"""Create + attach extension actions managed policies (CDK)."""

from __future__ import annotations

from typing import Any

from aws_cdk import aws_iam as iam
from constructs import Construct

from extension_actions import ExtensionActionsSpec, peer_actions_policy_name


def attach_extension_action_specs(
    scope: Construct,
    *,
    env_name: str,
    specs: list[ExtensionActionsSpec],
    roles: list[Any],
    peer_id: str | None = None,
) -> list[iam.ManagedPolicy]:
    """Attach each spec's policy document to the given roles.

    Hub (peer_id omitted): physical name from ``cdk_extension.json`` ``policy_name``.
    Peer: ``{env}-peer-{peerId}-{handle}-actions`` so Stack B names never collide.
    """
    created: list[iam.ManagedPolicy] = []
    attachable = [role for role in roles if role is not None]
    if not attachable or not specs:
        return created
    for spec in specs:
        if peer_id:
            policy_name = peer_actions_policy_name(env_name, peer_id, spec.handle)
            construct_id = f"PeerExtActions{spec.handle}"
        else:
            policy_name = spec.hub_policy_name(env_name)
            construct_id = f"HubExtActions{spec.handle}"
        policy = iam.ManagedPolicy(
            scope,
            construct_id,
            managed_policy_name=policy_name,
            document=iam.PolicyDocument.from_json(spec.document),
            description=spec.policy_description,
        )
        for role in attachable:
            policy.attach_to_role(role)
        created.append(policy)
    return created
