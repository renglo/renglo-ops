"""Move webhook portfolios between the production and staging APIs.

The edge Lambda stays on the fast path. These commands only edit EventBridge
rules, which match the portfolio already present on the event.
"""

from __future__ import annotations

import json
import re
from typing import Any

from renglo_ops.model.errors import RengloOpsError

EVENT_SOURCE = "custom.renglo.webhook"
EVENT_DETAIL_TYPE = "WebhookReceived"
_TARGET_ID = "renglo-ingress-staging"
_PORTFOLIO = re.compile(r"^[a-z0-9]{4,64}$")


def production_rule_name(env_name: str) -> str:
    return f"{env_name}-renglo-webhook"


def staging_rule_name(env_name: str) -> str:
    return f"{env_name}-renglo-webhook-staging"


def staging_destination_name(env_name: str) -> str:
    return f"{env_name}-renglo-process-staging"


def production_destination_name(env_name: str) -> str:
    return f"{env_name}-renglo-process"


def edge_name(env_name: str) -> str:
    return f"{env_name}-webhook-edge"


def connection_name(env_name: str) -> str:
    return f"{env_name}-renglo-ingress"


def check_portfolio(portfolio: str) -> str:
    text = (portfolio or "").strip()
    if "/" in text:
        raise RengloOpsError("pass the portfolio id only, for example b3853d35c1cf")
    if not _PORTFOLIO.fullmatch(text):
        raise RengloOpsError("portfolio id must be 4-64 lowercase letters or digits")
    return text


def ingress_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RengloOpsError("BASE_URL is missing from platform-vars/staging")
    return f"{base}/_schd/ingress"


def match_all_pattern() -> dict[str, Any]:
    return {"source": [EVENT_SOURCE], "detail-type": [EVENT_DETAIL_TYPE]}


def staging_pattern(portfolios: list[str]) -> dict[str, Any]:
    return {
        "source": [EVENT_SOURCE],
        "detail-type": [EVENT_DETAIL_TYPE],
        "detail": {"portfolio": list(portfolios)},
    }


def production_pattern(excluded: list[str]) -> dict[str, Any]:
    if not excluded:
        return match_all_pattern()
    return {
        "source": [EVENT_SOURCE],
        "detail-type": [EVENT_DETAIL_TYPE],
        "detail": {"portfolio": [{"anything-but": list(excluded)}]},
    }


def portfolios_in_pattern(pattern: dict[str, Any] | None, *, excluded: bool) -> list[str]:
    detail = (pattern or {}).get("detail") or {}
    raw = detail.get("portfolio") or []
    found: list[str] = []
    for item in raw:
        if isinstance(item, str):
            if not excluded:
                found.append(item)
            continue
        if excluded and isinstance(item, dict) and "anything-but" in item:
            banned = item["anything-but"]
            if isinstance(banned, str):
                found.append(banned)
            elif isinstance(banned, list):
                found.extend(str(value) for value in banned)
    return _sorted(found)


def _sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


def _missing(exc: Exception) -> bool:
    return _error_code(exc) == "ResourceNotFoundException"


def _load_pattern(rule: dict[str, Any] | None) -> dict[str, Any]:
    if not rule:
        return {}
    raw = rule.get("EventPattern") or "{}"
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _describe_rule(events: Any, name: str) -> dict[str, Any] | None:
    try:
        return events.describe_rule(Name=name)
    except Exception as exc:
        if _missing(exc):
            return None
        raise


def _put_pattern(events: Any, name: str, pattern: dict[str, Any], *, description: str) -> None:
    kwargs: dict[str, Any] = {
        "Name": name,
        "State": "ENABLED",
        "EventPattern": json.dumps(pattern, separators=(",", ":")),
    }
    if description:
        kwargs["Description"] = description
    events.put_rule(**kwargs)


def _delete_rule(events: Any, name: str) -> None:
    try:
        listed = events.list_targets_by_rule(Rule=name)
    except Exception as exc:
        if _missing(exc):
            return
        raise
    ids = [item["Id"] for item in (listed.get("Targets") or []) if item.get("Id")]
    if ids:
        events.remove_targets(Rule=name, Ids=ids)
    events.delete_rule(Name=name)


def _ensure_destination(events: Any, *, env_name: str, endpoint: str) -> str:
    name = staging_destination_name(env_name)
    try:
        current = events.describe_api_destination(Name=name)
    except Exception as exc:
        if not _missing(exc):
            raise
        try:
            connection = events.describe_connection(Name=connection_name(env_name))
        except Exception as conn_exc:
            if _missing(conn_exc):
                raise RengloOpsError(
                    f"EventBridge connection {connection_name(env_name)} is missing; deploy stack-b first"
                ) from conn_exc
            raise
        created = events.create_api_destination(
            Name=name,
            ConnectionArn=connection["ConnectionArn"],
            InvocationEndpoint=endpoint,
            HttpMethod="POST",
            InvocationRateLimitPerSecond=20,
        )
        return str(created["ApiDestinationArn"])
    if str(current.get("InvocationEndpoint") or "").rstrip("/") != endpoint.rstrip("/"):
        events.update_api_destination(
            Name=name,
            InvocationEndpoint=endpoint,
            HttpMethod="POST",
            InvocationRateLimitPerSecond=20,
        )
        current = events.describe_api_destination(Name=name)
    return str(current["ApiDestinationArn"])


def _put_staging_target(events: Any, *, env_name: str, destination_arn: str, role_arn: str) -> None:
    events.put_targets(
        Rule=staging_rule_name(env_name),
        Targets=[
            {
                "Id": _TARGET_ID,
                "Arn": destination_arn,
                "RoleArn": role_arn,
                "HttpParameters": {"HeaderParameters": {"Content-Type": "application/json"}},
            }
        ],
    )


def _staging_portfolios(events: Any, env_name: str) -> list[str]:
    rule = _describe_rule(events, staging_rule_name(env_name))
    return portfolios_in_pattern(_load_pattern(rule), excluded=False)


def _production_excluded(events: Any, env_name: str) -> list[str]:
    rule = _describe_rule(events, production_rule_name(env_name))
    if rule is None:
        raise RengloOpsError(
            f"EventBridge rule {production_rule_name(env_name)} is missing; deploy stack-b first"
        )
    return portfolios_in_pattern(_load_pattern(rule), excluded=True)


def _describe_destination(events: Any, name: str) -> dict[str, Any] | None:
    try:
        return events.describe_api_destination(Name=name)
    except Exception as exc:
        if _missing(exc):
            return None
        raise


def _endpoint(destination: dict[str, Any] | None) -> str:
    if not destination:
        return "absent"
    return str(destination.get("InvocationEndpoint") or "").strip() or "absent"


def _lambda_edge(lambda_client: Any, env_name: str) -> dict[str, Any] | None:
    try:
        return lambda_client.get_function_configuration(FunctionName=edge_name(env_name))
    except Exception as exc:
        if _missing(exc):
            return None
        raise


def _http_api(apigw: Any, env_name: str) -> dict[str, Any] | None:
    name = edge_name(env_name)
    token = ""
    while True:
        kwargs: dict[str, Any] = {}
        if token:
            kwargs["NextToken"] = token
        page = apigw.get_apis(**kwargs)
        for item in page.get("Items") or []:
            if item.get("Name") == name:
                return item
        token = str(page.get("NextToken") or "")
        if not token:
            return None


def _present(value: Any, *, missing: str = "absent") -> str:
    text = str(value or "").strip()
    return text or missing


def format_webhook_report(
    events: Any,
    env_name: str,
    *,
    lambda_client: Any,
    apigw: Any,
) -> str:
    """Operator view of the edge callback and whether any portfolio is on staging."""
    edge = _lambda_edge(lambda_client, env_name)
    http_api = _http_api(apigw, env_name)
    production_rule = _describe_rule(events, production_rule_name(env_name))
    staging_rule = _describe_rule(events, staging_rule_name(env_name))
    production_destination = _describe_destination(events, production_destination_name(env_name))
    staging_destination = _describe_destination(events, staging_destination_name(env_name))
    staged = portfolios_in_pattern(_load_pattern(staging_rule), excluded=False)
    excluded = portfolios_in_pattern(_load_pattern(production_rule), excluded=True)
    callback_base = str((http_api or {}).get("ApiEndpoint") or "").rstrip("/")
    production_endpoint = _endpoint(production_destination)
    staging_endpoint = _endpoint(staging_destination)

    lines = ["edge"]
    if edge is None:
        lines.append(f"  lambda: {edge_name(env_name)}  absent")
    else:
        lines.append(f"  lambda: {edge_name(env_name)}  {_present(edge.get('State'))}")
        lines.append(f"  lambda arn: {_present(edge.get('FunctionArn'))}")
    if http_api is None:
        lines.append(f"  http api: {edge_name(env_name)}  absent")
        lines.append("  callback: absent")
    else:
        lines.append(f"  http api: {edge_name(env_name)}  present")
        lines.append(f"  http api id: {_present(http_api.get('ApiId'))}")
    if callback_base:
        lines.append(f"  callback: {callback_base}/{{portfolio}}/{{org}}/{{channel}}")
        lines.append(
            "  challenge: To verify the callback, the producer sends a GET to the callback URL. The edge Lambda replies."
        )
    lines.append("")
    lines.append("routing")
    if staged:
        names = ", ".join(staged)
        lines.append(f"  Routing {names} to staging. The rest goes to production.")
        lines.append(f"  staging: {staging_endpoint}")
    else:
        lines.append("  Everything goes to production.")
    lines.append(f"  production: {production_endpoint}")
    if staged != excluded:
        lines.append("  drift: staging list and production exclusion differ")
    return "\n".join(lines)


def format_webhook_status(events: Any, env_name: str) -> str:
    staging_rule = staging_rule_name(env_name)
    production_rule = production_rule_name(env_name)
    staged = _staging_portfolios(events, env_name)
    excluded = _production_excluded(events, env_name)
    destination = staging_destination_name(env_name)
    endpoint = "(not created)"
    try:
        described = events.describe_api_destination(Name=destination)
        endpoint = str(described.get("InvocationEndpoint") or endpoint)
    except Exception as exc:
        if not _missing(exc):
            raise
    lines = [
        "webhook",
        f"  staging rule: {staging_rule}",
        f"  destination: {endpoint}",
        "  portfolios: " + (", ".join(staged) if staged else "(none)"),
        f"  production rule: {production_rule}",
        "  production excludes: " + (", ".join(excluded) if excluded else "(none)"),
    ]
    if staged != excluded:
        lines.append("  drift: staging list and production exclusion differ")
    return "\n".join(lines)


def stage_portfolio(
    events: Any,
    *,
    env_name: str,
    portfolio: str,
    staging_base_url: str,
    role_arn: str,
) -> str:
    portfolio = check_portfolio(portfolio)
    role = (role_arn or "").strip()
    if not role:
        raise RengloOpsError("ROLE_ARN is missing from platform-vars")
    endpoint = ingress_url(staging_base_url)
    _production_excluded(events, env_name)
    current = _staging_portfolios(events, env_name)
    already = portfolio in current
    portfolios = _sorted([*current, portfolio])
    destination_arn = _ensure_destination(events, env_name=env_name, endpoint=endpoint)
    _put_pattern(
        events,
        staging_rule_name(env_name),
        staging_pattern(portfolios),
        description="Webhook events pulled out to the staging API",
    )
    _put_staging_target(
        events,
        env_name=env_name,
        destination_arn=destination_arn,
        role_arn=role,
    )
    production = _describe_rule(events, production_rule_name(env_name)) or {}
    _put_pattern(
        events,
        production_rule_name(env_name),
        production_pattern(portfolios),
        description=str(production.get("Description") or ""),
    )
    lead = f"{portfolio} is already on staging" if already else f"staging {portfolio}"
    return f"{lead}\n{format_webhook_status(events, env_name)}"


def unstage_portfolio(events: Any, *, env_name: str, portfolio: str) -> str:
    portfolio = check_portfolio(portfolio)
    current = _staging_portfolios(events, env_name)
    if portfolio not in current:
        raise RengloOpsError(f"{portfolio} is not on the staging rule")
    _production_excluded(events, env_name)
    remaining = [item for item in current if item != portfolio]
    production = _describe_rule(events, production_rule_name(env_name)) or {}
    _put_pattern(
        events,
        production_rule_name(env_name),
        production_pattern(remaining),
        description=str(production.get("Description") or ""),
    )
    if remaining:
        _put_pattern(
            events,
            staging_rule_name(env_name),
            staging_pattern(remaining),
            description="Webhook events pulled out to the staging API",
        )
    else:
        _delete_rule(events, staging_rule_name(env_name))
    return f"production {portfolio}\n{format_webhook_status(events, env_name)}"
