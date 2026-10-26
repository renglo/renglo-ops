from renglo_ops.cdk.shared.config_builder import build_deploy_input_vars, build_launcher_vars


def _launcher(**overrides):
    args = {
        "stage": "staging",
        "env_name": "acme",
        "aws_region": "us-east-1",
        "aws_account": "123456789012",
        "data_bucket": "bucket",
        "cognito_user_pool_id": "pool",
        "cognito_app_client_id": "client",
        "cognito_domain": "auth.example",
        "tenant_role_arn": "arn:aws:iam::123456789012:role/acme_tt_role",
        "backend_ecr_repo_name": "acme-backend",
        "codedeploy_app_name": "acme",
        "amplify_app_id": "app",
        "amplify_default_domain": "example.amplifyapp.com",
        "amplify_console_url": "https://staging.example.amplifyapp.com",
        "stage_app": {
            "fn_name": "acme-backend-staging",
            "rest_url": "https://api.example.com/staging",
            "ws_connections": "https://ws.example.com/staging",
            "ws_url": "wss://ws.example.com/staging",
        },
        "compute_outputs": {},
        "ecs_network": {},
        "extension_vars": {},
    }
    args.update(overrides)
    return build_launcher_vars(**args)


def test_runtime_env_lands_in_platform_vars_without_overriding_infrastructure():
    rendered = _launcher(
        runtime_env={
            "BEDROCK_MODELS": "us.anthropic.claude-sonnet-5-5, us.anthropic.claude-haiku-5-5",
            "BASE_URL": "https://should-not-win.example",
            "lowercase": "no",
            "EMPTY_KEY": "  ",
        }
    )
    assert rendered["BEDROCK_MODELS"] == "us.anthropic.claude-sonnet-5-5, us.anthropic.claude-haiku-5-5"
    assert rendered["BASE_URL"] == "https://api.example.com/staging"
    assert "lowercase" not in rendered
    assert "EMPTY_KEY" not in rendered


def test_runtime_env_lands_in_deploy_input():
    rendered = build_deploy_input_vars(
        env_name="acme",
        aws_region="us-east-1",
        aws_account="123456789012",
        data_bucket="bucket",
        cognito_user_pool_id="pool",
        cognito_app_client_id="client",
        tenant_role_arn="arn:aws:iam::123456789012:role/acme_tt_role",
        production_app={},
        compute_outputs={},
        ecs_network={},
        extension_vars={"KB_ID": "kb"},
        runtime_env={"BEDROCK_MODELS": "us.anthropic.claude-sonnet-5-5", "KB_ID": "other"},
    )
    assert rendered["BEDROCK_MODELS"] == "us.anthropic.claude-sonnet-5-5"
    assert rendered["KB_ID"] == "kb"
