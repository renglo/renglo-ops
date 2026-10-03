from pathlib import Path

import yaml

from renglo.cli import main
from renglo.help import PATHS, render
from renglo_ops.image import materialize
from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.legacy import customer_config_dict, deploy_targets_dict
from renglo_ops.model.local import is_tool_checkout, refuse_tool_output
from renglo_ops.model.tenant import (
    dump_tenant,
    find_tenant_file,
    load_tenant,
    tenant_from_dict,
    tenant_to_dict,
)
from renglo_ops.release.catalog import catalog_data


def test_tenant_projects_legacy_catalog_shapes() -> None:
    tenant = tenant_from_dict(
        {
            "name": "apollo1",
            "github": {
                "repo": "teamamericaai/apollo-bom",
                "owner_id": "327216445",
                "repo_id": "1392981742",
            },
            "email": {"from": "apollo_noreply@teamamericany.com", "identity": "email"},
            "platform": "0.1.0",
            "accounts": {
                "staging": {"id": "858045071584", "region": "us-east-1", "enabled": True},
                "production": {"id": "858045071584", "region": "us-east-1", "enabled": False},
            },
            "registries": [
                {
                    "domain": "apollo",
                    "python": "python-store",
                    "npm": "npm-store",
                    "scopes": ["@apollo"],
                },
                {
                    "domain": "renglo",
                    "python": "python-store",
                    "npm": "npm-store",
                    "account": "339713094352",
                    "scopes": ["@renglo"],
                },
            ],
            "placement": {"hub": ["renglo-data"], "peers": {}},
            "packages": {"renglo-lib": {"python": "renglo-lib"}},
            "release": {"bom": "0.1.0", "console": "0.1.0"},
            "env": {"APP_FE_BASE_URL": "https://console.example.com"},
        }
    )
    assert tenant.github_repo == "teamamericaai/apollo-bom"
    assert tenant.foreign_domain_owners() == ["339713094352"]
    assert customer_config_dict(tenant)["package_registry"]["domain_owners"] == ["339713094352"]
    assert tenant.env["APP_FE_BASE_URL"] == "https://console.example.com"
    projected = deploy_targets_dict(tenant)
    assert projected["tenants"]["apollo1"]["aws_account"] == "858045071584"
    assert projected["registries"][1]["domain_owner"] == "339713094352"


def test_renglo_yaml_projection_stays_in_the_checkout(tmp_path: Path) -> None:
    from renglo_ops.release.__main__ import _project

    src = tmp_path / "renglo.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "name": "apollo1",
                "github": {"repo": "teamamericaai/apollo-bom"},
                "email": {"from": "a@b.c", "identity": "email"},
                "accounts": {
                    "staging": {"id": "858045071584", "region": "us-east-1", "enabled": True},
                },
                "placement": {
                    "hub": ["renglo-data"],
                    "peers": {
                        "tourbot": {
                            "compute": "lambda_only",
                            "extensions": ["tourbotlink"],
                            "peers_bom": "0.1.1",
                        }
                    },
                },
                "release": {"bom": "0.1.1", "console": "0.1.1"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    projected, made = _project(str(src))
    try:
        path = Path(projected)
        assert made
        assert path.parent == tmp_path
        assert path.name != "renglo.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["peers"]["tourbot"]["peers_bom"] == "0.1.1"
    finally:
        Path(projected).unlink(missing_ok=True)


def test_renglo_yaml_round_trip(tmp_path: Path) -> None:
    tenant = tenant_from_dict(
        {
            "name": "apollo1",
            "github": {"repo": "teamamericaai/apollo-bom"},
            "email": {"from": "a@b.c", "identity": "email"},
            "accounts": {
                "staging": {"id": "858045071584", "region": "us-east-1", "enabled": True},
                "production": {"enabled": False},
            },
            "placement": {"hub": ["renglo-data"], "peers": {}},
            "release": {"bom": "0.1.0", "console": "0.1.0"},
        }
    )
    path = tmp_path / "renglo.yaml"
    path.write_text(dump_tenant(tenant), encoding="utf-8")
    loaded = load_tenant(path)
    assert tenant_to_dict(loaded)["github"]["repo"] == "teamamericaai/apollo-bom"
    data = catalog_data(tmp_path)
    assert data is not None
    assert data["hub"]["python"] == ["renglo-data"]


def test_help_paths_are_filters() -> None:
    greenfield = render("greenfield")
    assert "renglo doctor" in greenfield
    assert "renglo registry deploy" not in greenfield
    assert "registry" in PATHS
    assert "renglo registry deploy" in render("registry")
    assert "renglo registry show" in render("registry")
    assert "renglo registry check PACKAGE VERSION" in render("registry")


def test_refuse_writing_into_the_tool() -> None:
    root = Path(__file__).resolve().parents[1]
    assert is_tool_checkout(root)
    try:
        refuse_tool_output(root / "lib" / "out.txt")
    except RengloOpsError as exc:
        assert "renglo-ops" in str(exc)
    else:
        raise AssertionError("expected refusal")


def test_materialize_image(tmp_path: Path) -> None:
    written = materialize(tmp_path)
    names = {path.name for path in written}
    assert "Dockerfile" in names
    assert (tmp_path / "scripts" / "install_backend_packages.py").is_file()


def test_stack_deploy_dry_run_prints_cdk(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "console").mkdir()
    (tmp_path / "dev").mkdir()
    (tmp_path / "renglo.yaml").write_text(
        """
name: apollo1
github:
  repo: teamamericaai/apollo-bom
email:
  from: a@b.c
  identity: email
accounts:
  staging:
    id: "858045071584"
    region: us-east-1
    enabled: true
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["stack", "deploy", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "renglo_ops.cdk.hub" in out
    assert "cdk deploy" in out
    assert "--app" in out


def test_config_init_writes_both_files(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "console").mkdir()
    (tmp_path / "dev").mkdir()
    (tmp_path / "ops").mkdir()
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "init",
            "--no-input",
            "--name",
            "acme1",
            "--github-repo",
            "acmeco/acme-bom",
            "--email-from",
            "noreply@acme.com",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
        ]
    )
    assert code == 0
    capsys.readouterr()
    tenant = load_tenant(tmp_path / "ops" / "acme-bom" / "renglo.yaml")
    assert tenant.name == "acme1"
    assert tenant.github_repo == "acmeco/acme-bom"
    assert tenant.accounts["staging"].id == "123456789012"
    assert tenant.accounts["staging"].region == "us-west-2"
    assert tenant.accounts["production"].enabled is False
    local = yaml.safe_load((tmp_path / ".renglo" / "local.yaml").read_text())
    assert local["tenant"] == "ops/acme-bom"
    assert local["region"] == "us-west-2"
    assert ".renglo/" in (tmp_path / ".gitignore").read_text()
    assert find_tenant_file(tmp_path / "console") == tmp_path / "ops" / "acme-bom" / "renglo.yaml"


def test_config_init_refuses_to_overwrite(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    flags = [
        "config",
        "init",
        "--no-input",
        "--bom",
        str(tmp_path / "acme-bom"),
        "--name",
        "acme1",
        "--github-repo",
        "acmeco/acme-bom",
        "--email-from",
        "noreply@acme.com",
    ]
    assert main(flags) == 0
    capsys.readouterr()
    try:
        main(flags)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected refusal")
    assert "already describes an environment" in capsys.readouterr().err
    assert main([*flags, "--force"]) == 0


def test_config_init_replaces_the_template(tmp_path: Path, monkeypatch, capsys) -> None:
    bom = tmp_path / "acme-bom"
    bom.mkdir()
    (bom / "renglo.yaml").write_text(
        "name: CHANGE_ME\ngithub:\n  repo: CHANGE_ME_ORG/CHANGE_ME-bom\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "init",
            "--no-input",
            "--name",
            "acme1",
            "--github-repo",
            "acmeco/acme-bom",
            "--email-from",
            "noreply@acme.com",
        ]
    )
    assert code == 0
    capsys.readouterr()
    assert load_tenant(bom / "renglo.yaml").name == "acme1"


def test_invalid_identity_rejected() -> None:
    try:
        tenant_from_dict(
            {
                "name": "apollo1",
                "github": {"repo": "org/apollo-bom"},
                "email": {"from": "a@b.c", "identity": "carrier"},
            }
        )
    except RengloOpsError:
        return
    raise AssertionError("expected rejection")


