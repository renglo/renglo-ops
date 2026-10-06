import json

from renglo.help import render
from renglo.webhook import (
    check_portfolio,
    format_webhook_report,
    format_webhook_status,
    stage_portfolio,
    unstage_portfolio,
)
from renglo_ops.model.errors import RengloOpsError

ENV = "apollo1"
PROD = "apollo1-renglo-webhook"
STAGING = "apollo1-renglo-webhook-staging"
PORTFOLIO = "b3853d35c1cf"
OTHER = "46f2c445ed63"
BASE = "https://staging.example.com"
ROLE = "arn:aws:iam::858045071584:role/apollo1_tt_role"


class _Missing(Exception):
    def __init__(self) -> None:
        super().__init__("missing")
        self.response = {"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}}


class _Events:
    def __init__(self) -> None:
        self.rules = {
            PROD: {
                "Name": PROD,
                "State": "ENABLED",
                "Description": "production ingress",
                "EventPattern": json.dumps(
                    {"source": ["custom.renglo.webhook"], "detail-type": ["WebhookReceived"]}
                ),
            }
        }
        self.targets: dict[str, list] = {}
        self.destinations: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def describe_rule(self, Name):
        rule = self.rules.get(Name)
        if rule is None:
            raise _Missing()
        return dict(rule)

    def put_rule(self, **kwargs):
        pattern = json.loads(kwargs["EventPattern"])
        self.calls.append(("put_rule", kwargs["Name"], pattern))
        self.rules[kwargs["Name"]] = {
            "Name": kwargs["Name"],
            "State": kwargs["State"],
            "Description": kwargs.get("Description") or "",
            "EventPattern": kwargs["EventPattern"],
        }

    def put_targets(self, Rule, Targets):
        self.calls.append(("put_targets", Rule, Targets[0]["RoleArn"]))
        self.targets[Rule] = Targets

    def list_targets_by_rule(self, Rule):
        if Rule not in self.rules and Rule not in self.targets:
            raise _Missing()
        return {"Targets": list(self.targets.get(Rule, []))}

    def remove_targets(self, Rule, Ids):
        self.calls.append(("remove_targets", Rule))
        self.targets[Rule] = [item for item in self.targets.get(Rule, []) if item["Id"] not in Ids]

    def delete_rule(self, Name):
        self.calls.append(("delete_rule", Name))
        self.rules.pop(Name, None)
        self.targets.pop(Name, None)

    def describe_connection(self, Name):
        return {
            "ConnectionArn": "arn:aws:events:connection/test/abc",
            "ConnectionState": "AUTHORIZED",
        }

    def describe_api_destination(self, Name):
        found = self.destinations.get(Name)
        if found is None:
            raise _Missing()
        return dict(found)

    def create_api_destination(self, **kwargs):
        self.calls.append(("create_api_destination", kwargs["InvocationEndpoint"]))
        arn = f"arn:aws:events:api-destination/{kwargs['Name']}/id"
        self.destinations[kwargs["Name"]] = {
            "ApiDestinationArn": arn,
            "InvocationEndpoint": kwargs["InvocationEndpoint"],
        }
        return {"ApiDestinationArn": arn}

    def update_api_destination(self, **kwargs):
        self.destinations[kwargs["Name"]]["InvocationEndpoint"] = kwargs["InvocationEndpoint"]


def _stage(events: _Events, portfolio: str = PORTFOLIO) -> str:
    return stage_portfolio(
        events,
        env_name=ENV,
        portfolio=portfolio,
        staging_base_url=BASE,
        role_arn=ROLE,
    )


def test_check_portfolio_rejects_a_path() -> None:
    try:
        check_portfolio(f"{PORTFOLIO}/_all")
    except RengloOpsError as exc:
        assert "portfolio id only" in str(exc)
        return
    raise AssertionError("expected refusal")


def test_status_when_nothing_is_staged() -> None:
    text = format_webhook_status(_Events(), ENV)
    assert "portfolios: (none)" in text
    assert "production excludes: (none)" in text
    assert "destination: (not created)" in text
    assert "drift:" not in text


def test_stage_writes_staging_rule_before_production_exclusion() -> None:
    events = _Events()
    text = _stage(events)
    puts = [call for call in events.calls if call[0] == "put_rule"]
    assert puts[0][1] == STAGING
    assert puts[0][2]["detail"]["portfolio"] == [PORTFOLIO]
    assert puts[1][1] == PROD
    assert puts[1][2]["detail"]["portfolio"] == [{"anything-but": [PORTFOLIO]}]
    assert events.calls[0][0] == "create_api_destination"
    assert events.calls[0][1] == "https://staging.example.com/_schd/ingress"
    assert "staging b3853d35c1cf" in text
    assert "drift:" not in text


def test_stage_keeps_an_existing_portfolio() -> None:
    events = _Events()
    _stage(events, OTHER)
    text = _stage(events, PORTFOLIO)
    staged = json.loads(events.rules[STAGING]["EventPattern"])["detail"]["portfolio"]
    assert staged == sorted([OTHER, PORTFOLIO])
    assert "already on staging" not in text
    again = _stage(events, PORTFOLIO)
    assert "already on staging" in again


def test_unstage_restores_production_before_deleting_the_rule() -> None:
    events = _Events()
    _stage(events)
    events.calls.clear()
    text = unstage_portfolio(events, env_name=ENV, portfolio=PORTFOLIO)
    assert events.calls[0][0] == "put_rule"
    assert events.calls[0][1] == PROD
    assert "detail" not in events.calls[0][2]
    assert ("delete_rule", STAGING) in events.calls
    assert events.calls.index(("put_rule", PROD, events.calls[0][2])) < events.calls.index(
        ("delete_rule", STAGING)
    )
    assert STAGING not in events.rules
    assert "production b3853d35c1cf" in text
    assert "portfolios: (none)" in text


def test_unstage_one_portfolio_leaves_the_other() -> None:
    events = _Events()
    _stage(events, OTHER)
    _stage(events, PORTFOLIO)
    unstage_portfolio(events, env_name=ENV, portfolio=PORTFOLIO)
    staged = json.loads(events.rules[STAGING]["EventPattern"])["detail"]["portfolio"]
    assert staged == [OTHER]
    assert ("delete_rule", STAGING) not in events.calls


def test_unstage_unknown_portfolio() -> None:
    try:
        unstage_portfolio(_Events(), env_name=ENV, portfolio=PORTFOLIO)
    except RengloOpsError as exc:
        assert "not on the staging rule" in str(exc)
        return
    raise AssertionError("expected refusal")


class _Lambda:
    def __init__(self, *, found: bool = True) -> None:
        self.found = found

    def get_function_configuration(self, FunctionName):
        if not self.found:
            raise _Missing()
        return {
            "FunctionArn": f"arn:aws:lambda:us-east-1:858045071584:function:{FunctionName}",
            "State": "Active",
        }


class _Api:
    def __init__(self, *, found: bool = True) -> None:
        self.found = found

    def get_apis(self, NextToken=""):
        if not self.found:
            return {"Items": []}
        return {
            "Items": [
                {
                    "Name": "apollo1-webhook-edge",
                    "ApiId": "wpca9b1i37",
                    "ApiEndpoint": "https://wpca9b1i37.execute-api.us-east-1.amazonaws.com",
                }
            ]
        }


def _report(events: _Events, *, edge: bool = True) -> str:
    events.rules[PROD]["Arn"] = "arn:aws:events:us-east-1:858045071584:rule/apollo1-renglo-webhook"
    events.destinations["apollo1-renglo-process"] = {
        "ApiDestinationArn": "arn:aws:events:us-east-1:858045071584:api-destination/apollo1-renglo-process/dest",
        "ApiDestinationState": "ACTIVE",
        "InvocationEndpoint": "https://prod.example.com/production/_schd/ingress",
    }
    return format_webhook_report(
        events,
        ENV,
        lambda_client=_Lambda(found=edge),
        apigw=_Api(found=edge),
    )


def test_report_shows_a_generic_callback_and_staging_route() -> None:
    events = _Events()
    _stage(events)
    text = _report(events)
    assert "lambda: apollo1-webhook-edge  Active" in text
    assert "http api id: wpca9b1i37" in text
    assert "callback: https://wpca9b1i37.execute-api.us-east-1.amazonaws.com/{portfolio}/{org}/{channel}" in text
    assert "GET to the callback URL" in text
    assert "whatsapp" not in text
    assert f"Routing {PORTFOLIO} to staging. The rest goes to production." in text
    assert "staging: https://staging.example.com/_schd/ingress" in text
    assert "production: https://prod.example.com/production/_schd/ingress" in text


def test_report_says_everything_goes_to_production() -> None:
    text = _report(_Events())
    assert "Everything goes to production." in text
    assert "Routing " not in text
    assert "production: https://prod.example.com/production/_schd/ingress" in text
    assert "staging:" not in text


def test_report_marks_a_missing_edge() -> None:
    text = _report(_Events(), edge=False)
    assert "lambda: apollo1-webhook-edge  absent" in text
    assert "http api: apollo1-webhook-edge  absent" in text
    assert "callback: absent" in text
    assert "Everything goes to production." in text


def test_help_lists_webhook_commands() -> None:
    text = render("webhook")
    assert "renglo webhook status" in text
    assert "renglo webhook stage PORTFOLIO" in text
    assert "renglo webhook unstage PORTFOLIO" in text
