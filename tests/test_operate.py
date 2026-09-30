from pathlib import Path

import yaml

from renglo.cli import main
from renglo.help import render
from renglo.operate import (
    extension_rows,
    format_extension_tree,
    format_peer_list,
    parse_stacks,
    refuse_destroy_a_while_b_exists,
)
from renglo_ops.model.errors import RengloOpsError
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
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    def describe_stacks(self, StackName: str):
        status = self.statuses.get(StackName)
        if status is None:
            raise RuntimeError("Stack with id does not exist")
        return {"Stacks": [{"StackStatus": status}]}


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
    assert "renglo email sender-status" in text
    assert "renglo help operate" in render("")
