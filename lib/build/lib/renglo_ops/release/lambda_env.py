"""Filter and size-check env maps before UpdateFunctionConfiguration.

Lambda's Environment.Variables payload cannot exceed 4KB. Deploy input also
carries console-only and CI-only keys that must not be copied onto the
backend function.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable

LAMBDA_ENV_MAX_BYTES = 4096
LAMBDA_KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")

RESERVED_LAMBDA_ENV_KEYS = frozenset(
    {
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_EXECUTION_ENV",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_LAMBDA_FUNCTION_NAME",
        "AWS_LAMBDA_FUNCTION_VERSION",
        "AWS_LAMBDA_FUNCTION_MEMORY_SIZE",
        "AWS_LAMBDA_LOG_GROUP_NAME",
        "AWS_LAMBDA_LOG_STREAM_NAME",
        "AWS_LAMBDA_RUNTIME_API",
        "AWS_LAMBDA_INITIALIZATION_TYPE",
        "AWS_XRAY_CONTEXT_MISSING",
        "AWS_XRAY_DAEMON_ADDRESS",
    }
)

CI_ONLY_ENV_PREFIXES = ("CODEDEPLOY_", "AWS_GITHUB_", "AWS_ECR_")
CI_ONLY_ENV_KEYS = frozenset(
    {
        "AWS_ECR_REPOSITORY",
        "LAMBDA_BACKEND_ARN",
        "AWS_GITHUB_OIDC_ROLE_ARN",
    }
)
CONSOLE_ONLY_ENV_PREFIXES = ("VITE_", "AMPLIFY_")
# Light vs heavy lives on SSM peer-routes. Soak alias and overflow identity
# must not be copied onto the hub or a peer Lambda.
HANDLERS_ONLY_ENV_KEYS = frozenset(
    {
        "EXTERNAL_HANDLERS_PEER_MAP",
        "EXTERNAL_HANDLERS_HEAVY",
        "EXTERNAL_HANDLERS_ECS_HANDLERS",
        "EXTERNAL_HANDLERS_PEER_ROUTING",
        "ECR_IMAGE_URI",
    }
)

# Overflow singleton identity — peer-routes owns routing.
OVERFLOW_IDENTITY_ENV_KEYS = frozenset(
    {
        "LAMBDA_EXTERNAL_HANDLERS_ARN",
        "LAMBDA_HANDLERS_FUNCTION_NAME",
        "LAMBDA_FUNCTION_NAME",
        "LAMBDA_ALIAS",
        "ECS_CLUSTER",
        "ECS_TASK_DEFINITION",
        "ECS_RESULTS_BUCKET",
        "ECS_LAUNCH_TYPE",
        "ECS_NETWORK_MODE",
        "ECS_VPC",
        "ECS_SUBNETS",
        "ECS_SECURITY_GROUPS",
    }
)

CRITICAL_HANDLER_ENV_KEYS = ("DYNAMODB_RINGDATA_TABLE", "DYNAMODB_ENTITY_TABLE")


def is_reserved_env_key(key: str) -> bool:
    k = key.strip()
    if not k or k in RESERVED_LAMBDA_ENV_KEYS or k in CI_ONLY_ENV_KEYS:
        return True
    if k.startswith("AWS_LAMBDA_"):
        return True
    return any(k.startswith(prefix) for prefix in CI_ONLY_ENV_PREFIXES)


def is_console_only_env_key(key: str) -> bool:
    k = key.strip()
    return any(k.startswith(prefix) for prefix in CONSOLE_ONLY_ENV_PREFIXES)


def env_payload_size(env: dict[str, str]) -> int:
    """UTF-8 bytes of keys + values (matches AWS Measured size)."""
    return sum(len(k.encode("utf-8")) + len(v.encode("utf-8")) for k, v in env.items())


def filter_lambda_env(
    source: dict[str, str],
    *,
    log: bool = False,
) -> dict[str, str]:
    """Drop reserved, CI-only, console-only, and invalid Lambda keys."""
    out: dict[str, str] = {}
    skipped: list[str] = []
    for raw_key, raw_val in source.items():
        key = str(raw_key).strip()
        if raw_val is None:
            continue
        value = str(raw_val)
        if (
            is_reserved_env_key(key)
            or is_console_only_env_key(key)
            or key in HANDLERS_ONLY_ENV_KEYS
            or key in OVERFLOW_IDENTITY_ENV_KEYS
        ):
            skipped.append(key)
            continue
        if not LAMBDA_KEY_RE.fullmatch(key):
            skipped.append(key)
            continue
        out[key] = value
    if log and skipped:
        print(f"Skipping {len(skipped)} non-runtime Lambda env key(s): {', '.join(skipped)}", file=sys.stderr)
    return out


def tenant_dynamodb_vars(env_name: str) -> dict[str, str]:
    """Table names are `{env}_*` — same mapping as bootstrap config_builder."""
    prefix = env_name.strip()
    return {
        "DYNAMODB_ENTITY_TABLE": f"{prefix}_entities",
        "DYNAMODB_BLUEPRINT_TABLE": f"{prefix}_blueprints",
        "DYNAMODB_RINGDATA_TABLE": f"{prefix}_data",
        "DYNAMODB_REL_TABLE": f"{prefix}_rel",
        "DYNAMODB_CHAT_TABLE": f"{prefix}_chat",
        "DYNAMODB_SESSION_TABLE": f"{prefix}_session",
        "DYNAMODB_SEARCH_TABLE": f"{prefix}_search",
        "DYNAMODB_GRAPH_TABLE": f"{prefix}_graph",
    }


def peer_base_env(env_name: str) -> dict[str, str]:
    return {"WL_NAME": env_name.strip(), **tenant_dynamodb_vars(env_name)}


def filter_peer_lambda_env(source: dict[str, str], *, log: bool = False) -> dict[str, str]:
    """Runtime env for a peer Lambda: no console/CI/reserved keys, no overflow identity."""
    filtered = filter_lambda_env(source, log=log)
    out: dict[str, str] = {}
    skipped: list[str] = []
    for key, value in filtered.items():
        if key in OVERFLOW_IDENTITY_ENV_KEYS or key in HANDLERS_ONLY_ENV_KEYS:
            skipped.append(key)
            continue
        out[key] = value
    if log and skipped:
        print(
            f"Skipping {len(skipped)} overflow identity key(s): {', '.join(skipped)}",
            file=sys.stderr,
        )
    return out


def merge_peer_lambda_env(
    env_name: str,
    extra: dict[str, str] | None = None,
    *,
    log: bool = False,
) -> dict[str, str]:
    """SSM deploy-input (filtered) plus deterministic table names / WL_NAME."""
    merged = filter_peer_lambda_env(extra or {}, log=log)
    merged.update(peer_base_env(env_name))
    missing = [k for k in CRITICAL_HANDLER_ENV_KEYS if not merged.get(k)]
    if missing:
        raise RuntimeError(
            "peer Lambda env missing "
            + ", ".join(missing)
            + f" (env_name={env_name!r})"
        )
    assert_under_lambda_limit(merged)
    return merged


def assert_under_lambda_limit(env: dict[str, str], *, limit: int = LAMBDA_ENV_MAX_BYTES) -> int:
    size = env_payload_size(env)
    if size <= limit:
        return size
    ranked = sorted(env.items(), key=lambda item: -(len(item[0]) + len(item[1])))
    top: Iterable[str] = (f"{k}={len(k) + len(v)}B" for k, v in ranked[:8])
    raise RuntimeError(
        f"Lambda environment exceeds the {limit} byte limit "
        f"(measured {size} bytes, {len(env)} keys). Largest: {', '.join(top)}"
    )
