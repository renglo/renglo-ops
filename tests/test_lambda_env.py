import sys
from pathlib import Path

from renglo_ops.release.lambda_env import env_payload_size, filter_lambda_env

_RELEASE = Path(__file__).resolve().parents[1] / "lib" / "renglo_ops" / "release"
if str(_RELEASE) not in sys.path:
    sys.path.insert(0, str(_RELEASE))

from fetch_deploy_input import _load_platform_env_overlay  # noqa: E402

_CORS = (
    Path(__file__).resolve().parents[1]
    / "lib"
    / "renglo_ops"
    / "bom_ci"
    / "templates"
    / ".github"
    / "scripts"
)
if str(_CORS) not in sys.path:
    sys.path.insert(0, str(_CORS))

from apply_cors_allowed import load_cors_allowed  # noqa: E402


def test_filter_drops_tenant_file_keys() -> None:
    source = {
        "WL_NAME": "arbitium0813",
        "DYNAMODB_ENTITY_TABLE": "arbitium0813_entities",
        "packages": '{"renglo-lib": {"python": "renglo-lib"}}',
        "placement": '{"hub": ["renglo-data"]}',
        "registries": "[]",
        "accounts": "{}",
        "github": "{}",
        "email": "{}",
        "release": "{}",
        "staging": "{}",
        "name": "arbitium0813",
        "platform": "0.1.5",
    }
    kept = filter_lambda_env(source)
    assert set(kept) == {"WL_NAME", "DYNAMODB_ENTITY_TABLE"}
    assert env_payload_size(kept) < env_payload_size(source)


def test_tenant_file_overlay_is_empty_without_env_block(tmp_path: Path) -> None:
    path = tmp_path / "renglo.yaml"
    path.write_text(
        "name: arbitium0813\n"
        "github:\n"
        "  repo: Arbitium/arbitium-bom\n"
        "packages:\n"
        "  renglo-lib:\n"
        "    python: renglo-lib\n",
        encoding="utf-8",
    )
    assert _load_platform_env_overlay(path) == {}


def test_tenant_file_overlay_uses_env_block(tmp_path: Path) -> None:
    path = tmp_path / "renglo.yaml"
    path.write_text(
        "name: arbitium0813\n"
        "github:\n"
        "  repo: Arbitium/arbitium-bom\n"
        "env:\n"
        "  OPENAI_API_KEY: sk-test\n",
        encoding="utf-8",
    )
    assert _load_platform_env_overlay(path) == {"OPENAI_API_KEY": "sk-test"}


def test_cors_allowed_list(tmp_path: Path) -> None:
    path = tmp_path / "allowed.yml"
    path.write_text(
        "origins:\n"
        "  - https://lab.arbitium.com/\n"
        "  - https://staging.example.amplifyapp.com\n",
        encoding="utf-8",
    )
    assert load_cors_allowed(path) == (
        '["https://lab.arbitium.com", "https://staging.example.amplifyapp.com"]'
    )


def test_cors_allowed_star_opens_every_origin(tmp_path: Path) -> None:
    path = tmp_path / "allowed.yml"
    path.write_text('origins:\n  - "*"\n', encoding="utf-8")
    assert load_cors_allowed(path) == '["*"]'


def test_cors_allowed_rejects_a_path(tmp_path: Path) -> None:
    path = tmp_path / "allowed.yml"
    path.write_text("origins:\n  - https://lab.arbitium.com/app\n", encoding="utf-8")
    try:
        load_cors_allowed(path)
    except ValueError as exc:
        assert "no path" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_flat_overlay_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "platform_env.yml"
    path.write_text("OPENAI_API_KEY: sk-test\n", encoding="utf-8")
    assert _load_platform_env_overlay(path) == {"OPENAI_API_KEY": "sk-test"}
