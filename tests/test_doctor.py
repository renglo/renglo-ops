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


def test_doctor_cli_exit_code(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "renglo doctor" in out
    assert "[fail]" in out
