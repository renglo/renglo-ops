from pathlib import Path
from unittest.mock import patch

import yaml

from renglo.cli import main
from renglo.doctor import Status, doctor_exit_code, run_doctor


def test_doctor_fails_without_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    checks = run_doctor(tmp_path)
    tenant_checks = [c for c in checks if c.label == "renglo.yaml loads"]
    assert tenant_checks and tenant_checks[0].status == Status.FAIL
    assert doctor_exit_code(checks) == 1


def test_doctor_ok_minimal_bom(tmp_path: Path, monkeypatch) -> None:
    bom = tmp_path / "acme-bom"
    bom.mkdir()
    (bom / ".git").mkdir()
    (bom / "renglo.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "acme1",
                "github": {"repo": "acmeco/acme-bom"},
                "email": {"from": "noreply@acme.com", "identity": "email"},
                "platform": "0.1.0",
                "accounts": {
                    "staging": {"id": "123456789012", "region": "us-east-1", "enabled": True},
                    "production": {"id": "", "region": "us-east-1", "enabled": False},
                },
                "registries": [],
                "placement": {"hub": [], "peers": {}},
                "packages": {},
                "release": {"bom": "0.1.0", "console": "0.1.0"},
                "env": {},
                "defaults": {"architecture": "x86_64", "cognito_token_hours": 24},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bom / "bom").mkdir()
    (bom / "bom" / "v0.1.0.json").write_text("{}", encoding="utf-8")
    (bom / "console_bom").mkdir()
    (bom / "console_bom" / "v0.1.0.json").write_text("{}", encoding="utf-8")
    local = tmp_path / ".renglo"
    local.mkdir()
    (local / "local.yaml").write_text(
        "tenant: acme-bom\nprofile: test-profile\nregion: us-east-1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(bom)
    with patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")):
        checks = run_doctor(bom)
    load = next(c for c in checks if c.label == "renglo.yaml loads")
    assert load.status == Status.OK
    p1 = next(c for c in checks if c.label == "Project 1 (greenfield)")
    assert p1.status in (Status.OK, Status.WARN)


class _Cfn:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    def describe_stacks(self, StackName: str):
        status = self.statuses.get(StackName)
        if status is None:
            raise RuntimeError("Stack with id does not exist")
        return {"Stacks": [{"StackStatus": status}]}


def _tenant_with_peer(bom: Path, peers: dict) -> None:
    bom.mkdir()
    (bom / ".git").mkdir()
    (bom / "renglo.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "acme1",
                "github": {"repo": "acmeco/acme-bom"},
                "email": {"from": "noreply@acme.com", "identity": "email"},
                "platform": "0.1.0",
                "accounts": {
                    "staging": {"id": "123456789012", "region": "us-east-1", "enabled": True},
                    "production": {"id": "", "region": "us-east-1", "enabled": False},
                },
                "registries": [],
                "placement": {"hub": [], "peers": peers},
                "packages": {},
                "release": {"bom": "0.1.0", "console": "0.1.0"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bom / "bom").mkdir()
    (bom / "bom" / "v0.1.0.json").write_text("{}", encoding="utf-8")
    (bom / "console_bom").mkdir()
    (bom / "console_bom" / "v0.1.0.json").write_text("{}", encoding="utf-8")


def _aws_on_path(monkeypatch) -> None:
    import shutil

    real = shutil.which

    def which(cmd: str):
        if cmd == "aws":
            return "/usr/bin/aws"
        return real(cmd)

    monkeypatch.setattr("renglo.doctor.shutil.which", which)


def test_doctor_fails_when_declared_peer_is_absent(tmp_path: Path, monkeypatch) -> None:
    bom = tmp_path / "acme-bom"
    _tenant_with_peer(
        bom,
        {
            "lab": {"compute": "fargate", "extensions": ["data"]},
            "ultra": {"compute": "fargate", "extensions": ["ultrahost"]},
        },
    )
    monkeypatch.chdir(bom)
    _aws_on_path(monkeypatch)
    monkeypatch.setenv("AWS_PROFILE", "test-profile")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    cfn = _Cfn({"acme1-peer-lab": "CREATE_COMPLETE"})
    with (
        patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")),
        patch("renglo.doctor._cloudformation_client", return_value=cfn),
    ):
        checks = run_doctor(bom)
    lab = next(c for c in checks if c.label == "Peer lab")
    ultra = next(c for c in checks if c.label == "Peer ultra")
    assert lab.status == Status.OK
    assert "CREATE_COMPLETE" in lab.detail
    assert ultra.status == Status.FAIL
    assert "not deployed" in ultra.detail
    assert doctor_exit_code(checks) == 1


def test_doctor_skips_peer_health_without_credentials(tmp_path: Path, monkeypatch) -> None:
    bom = tmp_path / "acme-bom"
    _tenant_with_peer(bom, {"ultra": {"compute": "fargate", "extensions": ["ultrahost"]}})
    monkeypatch.chdir(bom)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with patch("renglo.doctor._aws_identity", return_value=(False, "", "no creds")):
        checks = run_doctor(bom)
    peer = next(c for c in checks if c.group == "Peers")
    assert peer.status == Status.SKIP
    assert "ultra" in peer.detail


def test_doctor_cli_exit_code(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "renglo doctor" in out
    assert "[fail]" in out
