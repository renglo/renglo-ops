from pathlib import Path

from renglo_ops.cdk.shared.config_builder import (
    build_deploy_input_vars,
    build_launcher_vars,
    llm_platform_vars,
)
from renglo_ops.localdev_render import _render_env_config
from renglo_ops.platform_contract import (
    demanded_keys,
    env_config_values,
    extension_pins,
    keys_for_pin,
    pin_meets,
    requirement_sentence,
)


def test_pin_below_0_1_8_demands_nothing() -> None:
    assert pin_meets("0.1.7", "0.1.8") is False
    assert pin_meets("0.1.8", "0.1.8") is True
    assert pin_meets("0.1.9", "0.1.8") is True
    assert keys_for_pin("0.1.7") == ()
    assert "DYNAMODB_TIMESERIES_TABLE" in keys_for_pin("0.1.8")
    assert "BEDROCK_MODEL" in keys_for_pin("0.1.8")
    assert "BEDROCK_CONVERSE_MODEL" in keys_for_pin("0.1.9")


def test_highest_pin_wins(tmp_path: Path) -> None:
    repo = tmp_path / "extensions" / "ultrahost"
    (repo / "package").mkdir(parents=True)
    (repo / "ui").mkdir()
    (repo / "package" / "pyproject.toml").write_text(
        '[tool.renglo]\nplatform = "0.1.7"\n',
        encoding="utf-8",
    )
    (repo / "ui" / "package.json").write_text(
        '{"renglo": {"platform": "0.1.9"}}',
        encoding="utf-8",
    )
    demand = demanded_keys(extension_pins(tmp_path, ["ultrahost", "missing"]))
    assert demand["DYNAMODB_TIMESERIES_TABLE"] == ("ultrahost",)
    assert extension_pins(tmp_path, ["missing"])[0].checked_out is False


def test_llm_platform_vars_keep_model_ids_only() -> None:
    selected = llm_platform_vars(
        {
            "BEDROCK_MODEL": " anthropic.claude ",
            "BEDROCK_CONVERSE_MODEL": "  ",
            "OPENAI_API_KEY": "sk-test",
            "APP_FE_BASE_URL": "https://console.example.com",
        }
    )
    assert selected == {"BEDROCK_MODEL": "anthropic.claude"}


def test_launcher_publishes_timeseries_and_llm_ids() -> None:
    common = dict(
        env_name="acme1",
        aws_region="us-east-1",
        aws_account="123456789012",
        data_bucket="bucket",
        cognito_user_pool_id="pool",
        cognito_app_client_id="client",
        tenant_role_arn="arn:role",
        compute_outputs={},
        ecs_network={},
        extension_vars={},
        platform_env={"BEDROCK_MODEL": "anthropic.claude", "OPENAI_API_KEY": "sk-test"},
    )
    launcher = build_launcher_vars(
        stage="production",
        cognito_domain="domain",
        backend_ecr_repo_name="repo",
        codedeploy_app_name="app",
        amplify_app_id="amp",
        amplify_default_domain="example.amplifyapp.com",
        amplify_console_url="https://production.example.amplifyapp.com",
        stage_app={"rest_url": "https://api.example/", "ws_connections": "", "ws_url": ""},
        **common,
    )
    deploy = build_deploy_input_vars(
        production_app={"ws_connections": "", "ws_url": ""},
        **common,
    )
    assert launcher["DYNAMODB_TIMESERIES_TABLE"] == "acme1_timeseries"
    assert launcher["BEDROCK_MODEL"] == "anthropic.claude"
    assert "OPENAI_API_KEY" not in launcher
    assert "BEDROCK_CONVERSE_MODEL" not in launcher
    assert deploy["DYNAMODB_TIMESERIES_TABLE"] == "acme1_timeseries"
    assert deploy["BEDROCK_MODEL"] == "anthropic.claude"


def test_requirement_sentence_names_the_deploy() -> None:
    derived = requirement_sentence(
        "DYNAMODB_TIMESERIES_TABLE",
        ("ultrahost",),
        ["production", "staging"],
        operator_value="",
    )
    assert "platform-vars/production and platform-vars/staging" in derived
    assert "renglo stack deploy --stack b" in derived
    unset = requirement_sentence(
        "BEDROCK_MODEL",
        ("ultrahost",),
        ["production"],
        operator_value="",
    )
    assert "set env.BEDROCK_MODEL in renglo.yaml" in unset
    ready = requirement_sentence(
        "BEDROCK_MODEL",
        ("ultrahost",),
        ["production"],
        operator_value="anthropic.claude",
    )
    assert "set env.BEDROCK_MODEL" not in ready
    assert ready.endswith("run renglo stack deploy --stack b")


def test_env_config_values_skip_comments() -> None:
    text = "\n".join(
        [
            "# DYNAMODB_TIMESERIES_TABLE = 'commented'",
            "DYNAMODB_TIMESERIES_TABLE = ''",
            'BEDROCK_MODEL = "anthropic.claude"',
        ]
    )
    values = env_config_values(text)
    assert values["DYNAMODB_TIMESERIES_TABLE"] == ""
    assert values["BEDROCK_MODEL"] == "anthropic.claude"


def test_local_render_writes_llm_ids() -> None:
    text = _render_env_config(
        {"BEDROCK_MODEL": "anthropic.claude", "BEDROCK_CONVERSE_MODEL": ""},
        invite_fe_base_url="http://127.0.0.1:5174",
        wss_backend_url="http://127.0.0.1:8080/send_to_client",
        secret_key="s",
        csrf_session_key="c",
    )
    assert "BEDROCK_MODEL = 'anthropic.claude'" in text
    assert "BEDROCK_CONVERSE_MODEL = ''" in text
