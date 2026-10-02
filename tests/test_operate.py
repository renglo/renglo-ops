import json
from pathlib import Path

import yaml

from renglo.cli import main
from renglo.help import render
from renglo.operate import (
    extension_rows,
    format_extension_tree,
    format_peer_list,
    format_registry_check,
    format_registry_show,
    format_status_urls,
    package_coordinates,
    parse_stacks,
    refuse_destroy_a_while_b_exists,
)
from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.registry import Registry
from renglo_ops.model.tenant import load_tenant


def _tenant(tmp_path: Path):
    path = tmp_path / "renglo.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "acme1",
                "github": {"repo": "acmeco/acme-bom"},
                "email": {"from": "noreply@acme.com", "identity": "email"},
                "accounts": {
                    "staging": {"id": "123456789012", "region": "us-east-1", "enabled": True},
                },
                "packages": {
                    "renglo-lib": {"python": "renglo-lib"},
                    "data": {"python": "renglo-data"},
                    "billing": {"python": "acme-billing"},
                },
                "placement": {
                    "hub": ["renglo-data"],
                    "peers": {
                        "lab": {
                            "compute": "fargate",
                            "extensions": ["billing"],
                            "peers_bom": "0.1.0",
                        }
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_tenant(path)


def test_parse_stacks() -> None:
    assert parse_stacks("") == ["a", "b"]
    assert parse_stacks("b") == ["b"]
    assert parse_stacks("b,a") == ["a", "b"]
    try:
        parse_stacks("", default_both=False)
    except RengloOpsError:
        return
    raise AssertionError("expected refusal")


def test_extension_and_peer_views(tmp_path: Path) -> None:
    tenant = _tenant(tmp_path)
    rows = dict((handle, where) for handle, where, _package in extension_rows(tenant))
    assert rows["data"] == "hub"
    assert rows["billing"] == "peer:lab"
    assert "renglo-lib" not in rows
    text = format_extension_tree(tenant)
    assert "renglo-data" in text
    assert "lab" in format_peer_list(tenant)


class _Cfn:
    def __init__(
        self,
        statuses: dict[str, str],
        *,
        outputs: dict[str, list[dict[str, str]]] | None = None,
    ) -> None:
        self.statuses = statuses
        self.outputs = outputs or {}

    def describe_stacks(self, StackName: str):
        status = self.statuses.get(StackName)
        if status is None:
            raise RuntimeError("Stack with id does not exist")
        stack: dict = {"StackStatus": status}
        if StackName in self.outputs:
            stack["Outputs"] = self.outputs[StackName]
        return {"Stacks": [stack]}


def test_format_registry_show_absent() -> None:
    registry = Registry(name="apollo", github_org="acmeco", path=Path("/tmp/registry.yaml"))
    text = format_registry_show(
        registry,
        _Cfn({}),
        account_id="111122223333",
        region="us-east-1",
        profile="volatour",
    )
    assert "ABSENT" in text
    assert "same AWS profile" in text


def test_format_registry_show_outputs() -> None:
    registry = Registry(name="apollo", github_org="acmeco", path=Path("/tmp/registry.yaml"))
    cfn = _Cfn(
        {"apollo-publisher": "CREATE_COMPLETE"},
        outputs={
            "apollo-publisher": [
                {"OutputKey": "OidcPublishRoleArn", "OutputValue": "arn:aws:iam::1:role/x"},
                {"OutputKey": "PublisherName", "OutputValue": "apollo"},
            ]
        },
    )
    text = format_registry_show(
        registry,
        cfn,
        account_id="858045071584",
        region="us-east-1",
        profile="volatour",
    )
    assert "AWS_PUBLISH_ROLE_ARN: arn:aws:iam::1:role/x" in text
    assert "PUBLISHER_NAME: apollo" in text


class _Versions:
    def __init__(self, pages: list[dict], error: Exception | None = None) -> None:
        self.pages = pages
        self.error = error
        self.calls: list[dict] = []

    def list_package_versions(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        index = 0
        token = kwargs.get("nextToken") or ""
        if token:
            index = int(token)
        return self.pages[index]


class _AwsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


def _registry() -> Registry:
    return Registry(
        name="apollo",
        github_org="acmeco",
        python="python-store",
        npm="npm-store",
        path=Path("/tmp/registry.yaml"),
    )


def test_package_coordinates() -> None:
    assert package_coordinates("Apollo_TourbotLink", "") == ("pypi", "", "apollo-tourbotlink")
    assert package_coordinates("@apollo/tourbotlink", "") == ("npm", "apollo", "tourbotlink")
    assert package_coordinates("tourbotlink", "npm") == ("npm", "", "tourbotlink")
    try:
        package_coordinates("@apollo", "")
    except RengloOpsError:
        return
    raise AssertionError("expected refusal")


def test_format_registry_check_published() -> None:
    client = _Versions(
        [
            {"versions": [{"version": "0.1.0", "status": "Published"}], "nextToken": ""},
        ]
    )
    text, published = format_registry_check(
        _registry(),
        client,
        package="Apollo_TourbotLink",
        version="v0.1.0",
        fmt="",
        account_id="111122223333",
        region="us-east-1",
    )
    assert published
    assert "result: published" in text
    assert "package: apollo-tourbotlink" in text
    assert "version: 0.1.0" in text
    assert client.calls[0]["format"] == "pypi"
    assert client.calls[0]["repository"] == "python-store"
    assert client.calls[0]["domain"] == "apollo"


def test_format_registry_check_npm_and_absent() -> None:
    missing = _AwsError("ResourceNotFoundException", "Package '@apollo/ui' was not found")
    client = _Versions([], error=missing)
    text, published = format_registry_check(
        _registry(),
        client,
        package="@apollo/ui",
        version="1.2.3",
        fmt="",
        account_id="111122223333",
        region="us-east-1",
    )
    assert not published
    assert "result: absent" in text
    assert client.calls[0]["namespace"] == "apollo"
    assert client.calls[0]["format"] == "npm"
    assert client.calls[0]["repository"] == "npm-store"


def test_format_registry_check_not_published() -> None:
    client = _Versions(
        [{"versions": [{"version": "0.1.0", "status": "Unfinished"}]}]
    )
    text, published = format_registry_check(
        _registry(),
        client,
        package="apollo-tourbotlink",
        version="0.1.0",
        fmt="python",
        account_id="111122223333",
        region="us-east-1",
    )
    assert not published
    assert "result: not published" in text
    assert "status: Unfinished" in text


def test_format_registry_check_other_resource_error() -> None:
    client = _Versions([], error=_AwsError("ResourceNotFoundException", "Domain not found"))
    try:
        format_registry_check(
            _registry(),
            client,
            package="apollo-tourbotlink",
            version="0.1.0",
            fmt="python",
            account_id="111122223333",
            region="us-east-1",
        )
    except RengloOpsError as exc:
        assert "Domain not found" in str(exc)
    else:
        raise AssertionError("expected refusal")


def test_registry_check_refuses_default_profile(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text("name: apollo\ngithub_org: acme\n", encoding="utf-8")
    try:
        main(
            [
                "--profile",
                "default",
                "registry",
                "check",
                "apollo-tourbotlink",
                "0.1.0",
                "--registry",
                str(registry),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected refusal")
    assert "named AWS profile" in capsys.readouterr().err


def test_refuse_destroy_a_while_b_exists() -> None:
    cfn = _Cfn({"acme1-stack-b": "UPDATE_COMPLETE"})
    try:
        refuse_destroy_a_while_b_exists(cfn, "acme1", ["a"])
    except RengloOpsError as exc:
        assert "stack B" in str(exc)
    else:
        raise AssertionError("expected refusal")
    refuse_destroy_a_while_b_exists(cfn, "acme1", ["a", "b"])


def test_cli_reads_placement_without_aws(tmp_path: Path, monkeypatch, capsys) -> None:
    _tenant(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["extension", "tree"]) == 0
    assert "billing" in capsys.readouterr().out
    assert main(["peer", "list"]) == 0
    assert "fargate" in capsys.readouterr().out
    assert main(["peer", "show", "lab"]) == 0
    assert "acme1-peer-lab" in capsys.readouterr().out
    try:
        main(["extension", "show", "missing"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected refusal")


def test_help_operate() -> None:
    text = render("operate")
    assert "renglo catalog sync" in text
    assert "renglo email sender-status" in text
    assert "renglo help operate" in render("")
    assert "catalog sync" in render("")


class _Ssm:
    def __init__(self, params: dict[str, str]) -> None:
        self.params = params

    def get_parameter(self, Name: str):
        raw = self.params.get(Name)
        if raw is None:
            raise _AwsError("ParameterNotFound", Name)
        return {"Parameter": {"Value": raw}}


def test_format_status_urls() -> None:
    staging = json.dumps(
        {"VARS": {"BASE_URL": "https://api/staging", "FE_BASE_URL": "https://console/staging/"}}
    )
    production = json.dumps(
        {"VARS": {"BASE_URL": "https://api/production", "FE_BASE_URL": "https://console/production/"}}
    )
    ssm = _Ssm(
        {
            "/acme1/bootstrap/platform-vars/staging": staging,
            "/acme1/bootstrap/platform-vars/production": production,
        }
    )
    text = format_status_urls(ssm, "acme1")
    assert "urls staging:" in text
    assert "api: https://api/staging" in text
    assert "console: https://console/staging/" in text
    assert "urls production:" in text
    assert "api: https://api/production" in text
    assert "console: https://console/production/" in text
    assert "urls development:" in text
    assert "api: http://127.0.0.1:5001" in text
    assert "console: http://127.0.0.1:5174/" in text

    no_profile = format_status_urls(None, "acme1")
    assert "urls staging:" in no_profile
    assert "urls production:" in no_profile
    assert "profile required" in no_profile
    assert "urls development:" in no_profile
