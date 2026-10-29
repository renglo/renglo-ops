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


def _extension(root: Path, handle: str, pin: str) -> None:
    package = root / "extensions" / handle / "package"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text(
        f'[project]\nname = "{handle}"\nversion = "0.0.1"\n\n[tool.renglo]\nplatform = "{pin}"\n',
        encoding="utf-8",
    )


def _placed_tenant(root: Path, hub: list[str], env: dict | None = None) -> Path:
    bom = root / "acme-bom"
    bom.mkdir()
    (bom / ".git").mkdir()
    (bom / "renglo.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "acme1",
                "github": {"repo": "acmeco/acme-bom"},
                "email": {"from": "noreply@acme.com", "identity": "email"},
                "platform": "0.1.9",
                "accounts": {
                    "staging": {"id": "123456789012", "region": "us-east-1", "enabled": True},
                    "production": {"id": "", "region": "us-east-1", "enabled": False},
                },
                "registries": [],
                "placement": {"hub": hub, "peers": {}},
                "packages": {handle: {"python": handle} for handle in hub},
                "release": {"bom": "0.1.0", "console": "0.1.0"},
                "env": env or {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    local = root / ".renglo"
    local.mkdir()
    (local / "local.yaml").write_text(
        "tenant: acme-bom\nprofile: test-profile\nregion: us-east-1\n",
        encoding="utf-8",
    )
    return bom


def test_doctor_ignores_extensions_below_the_platform_keys(tmp_path: Path, monkeypatch) -> None:
    _extension(tmp_path, "elements", "0.1.7")
    bom = _placed_tenant(tmp_path, ["elements"])
    monkeypatch.chdir(bom)
    _aws_on_path(monkeypatch)
    with patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")):
        checks = run_doctor(bom)
    pins = next(c for c in checks if c.label == "Extension platform pins")
    assert pins.status == Status.OK
    assert "elements" in pins.detail
    assert not any(c.label == "Platform vars" for c in checks)


def test_doctor_warns_when_placed_extension_needs_a_deploy(tmp_path: Path, monkeypatch) -> None:
    _extension(tmp_path, "ultrahost", "0.1.9")
    bom = _placed_tenant(
        tmp_path,
        ["ultrahost"],
        {},
    )
    api = tmp_path / "dev" / "renglo-api"
    api.mkdir(parents=True)
    (api / "env_config.py").write_text("WL_NAME = 'acme1'\n", encoding="utf-8")
    monkeypatch.chdir(bom)
    _aws_on_path(monkeypatch)

    def load_vars(_ssm, _env_name, _stage):
        return {"WL_NAME": "acme1"}

    with (
        patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")),
        patch("renglo.doctor._ssm_client", return_value=object()),
        patch("renglo.doctor._load_platform_vars", side_effect=load_vars),
        patch("renglo.doctor._timeseries_table_state", return_value=("missing", "acme1_timeseries")),
    ):
        checks = run_doctor(bom)
    vars_checks = [c for c in checks if c.label == "Platform vars"]
    detail = " ".join(c.detail for c in vars_checks)
    table = next(c for c in checks if c.label == "Timeseries table")
    assert vars_checks and all(c.status == Status.WARN for c in vars_checks)
    assert "DYNAMODB_TIMESERIES_TABLE" in detail
    assert "renglo stack deploy --stack b" in detail
    assert "BEDROCK_MODELS is missing" in detail
    assert "set env.BEDROCK_MODELS" in detail
    assert "BEDROCK_MODEL is missing" not in detail
    assert "BEDROCK_CONVERSE_MODEL is missing" not in detail
    assert table.status == Status.WARN
    assert "renglo stack deploy --stack a" in table.detail
    assert not any(c.label == "Local API config" for c in checks)
    platform = [c for c in checks if c.group == "Platform"]
    assert doctor_exit_code(platform) == 0


def test_doctor_warns_when_local_config_lags_platform_vars(tmp_path: Path, monkeypatch) -> None:
    _extension(tmp_path, "ultrahost", "0.1.9")
    bom = _placed_tenant(tmp_path, ["ultrahost"])
    api = tmp_path / "dev" / "renglo-api"
    api.mkdir(parents=True)
    (api / "env_config.py").write_text("WL_NAME = 'acme1'\n", encoding="utf-8")
    monkeypatch.chdir(bom)
    _aws_on_path(monkeypatch)

    def load_vars(_ssm, _env_name, _stage):
        return {
            "DYNAMODB_TIMESERIES_TABLE": "acme1_timeseries",
            "BEDROCK_MODELS": "us.anthropic.claude-haiku-5-5",
        }

    with (
        patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")),
        patch("renglo.doctor._ssm_client", return_value=object()),
        patch("renglo.doctor._load_platform_vars", side_effect=load_vars),
        patch("renglo.doctor._timeseries_table_state", return_value=("ok", "acme1_timeseries")),
    ):
        checks = run_doctor(bom)
    local = next(c for c in checks if c.label == "Local API config")
    assert local.status == Status.WARN
    assert "DYNAMODB_TIMESERIES_TABLE" in local.detail
    assert "renglo state local-config --apply" in local.detail
    assert next(c for c in checks if c.label == "Platform vars").status == Status.OK


def test_doctor_accepts_live_platform_vars(tmp_path: Path, monkeypatch) -> None:
    _extension(tmp_path, "ultrahost", "0.1.9")
    bom = _placed_tenant(tmp_path, ["ultrahost"])
    api = tmp_path / "dev" / "renglo-api"
    api.mkdir(parents=True)
    (api / "env_config.py").write_text(
        "\n".join(
            [
                "DYNAMODB_TIMESERIES_TABLE = 'acme1_timeseries'",
                "BEDROCK_MODELS = 'us.anthropic.claude-haiku-5-5'",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(bom)
    _aws_on_path(monkeypatch)

    def load_vars(_ssm, _env_name, _stage):
        return {
            "DYNAMODB_TIMESERIES_TABLE": "acme1_timeseries",
            "BEDROCK_MODELS": "us.anthropic.claude-haiku-5-5",
        }

    with (
        patch("renglo.doctor._aws_identity", return_value=(True, "123456789012", "arn")),
        patch("renglo.doctor._ssm_client", return_value=object()),
        patch("renglo.doctor._load_platform_vars", side_effect=load_vars),
        patch("renglo.doctor._timeseries_table_state", return_value=("ok", "acme1_timeseries")),
    ):
        checks = run_doctor(bom)
    for label in ("Platform vars", "Timeseries table", "Local API config"):
        check = next(c for c in checks if c.label == label)
        assert check.status == Status.OK


def test_doctor_cli_exit_code(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "renglo doctor" in out
    assert "[fail]" in out
