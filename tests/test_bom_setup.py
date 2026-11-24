import json
from pathlib import Path

import yaml

from renglo_ops.model.tenant import RegistryRef, Tenant, dump_tenant, load_tenant
from renglo_ops.release.bom_setup import (
    checked_out_platform_version,
    ensure_github_ids,
    insert_github_ids,
    is_release_candidate,
    latest_published,
    prepare_tenant,
    registry_for,
    setup_bom,
)


def _tenant(**overrides) -> Tenant:
    data = {
        "name": "acme1",
        "namespace": "acme",
        "github_repo": "acmeco/acme-bom",
        "email_from": "noreply@acme.com",
        "email_identity": "email",
    }
    data.update(overrides)
    return Tenant(**data)


def test_prepare_tenant_fills_the_platform_set() -> None:
    tenant = _tenant()
    notes = prepare_tenant(tenant)
    assert tenant.release_bom == ""
    assert tenant.release_console == ""
    assert tenant.platform == ""
    assert tenant.placement_hub == ["data", "schd"]
    assert tenant.packages["acme-wl"] == {"python": "acme-wl", "npm": "@acme/wl"}
    assert tenant.packages["data"]["python"] == "renglo-data"
    assert tenant.registries[0].domain == "acme"
    assert tenant.registries[1].domain == "renglo"
    assert tenant.registries[1].account == "339713094352"
    assert "BEDROCK_MODELS" in tenant.env


def test_prepare_tenant_keeps_an_existing_catalog() -> None:
    tenant = _tenant(
        platform="0.1.16",
        release_bom="0.1.16",
        release_console="0.1.16",
        registries=[RegistryRef(domain="acme", scopes=["@acme"])],
        packages={"skbrk-wl": {"python": "ultrahost-wl", "npm": "@ultrahost/wl"}},
        placement_hub=["data"],
        env={"OPENAI_MODELS": "gpt-4o-mini"},
    )
    notes = prepare_tenant(tenant, platform="0.9.9")
    assert notes == []
    assert tenant.platform == "0.1.16"
    assert tenant.packages["skbrk-wl"]["python"] == "ultrahost-wl"
    assert tenant.placement_hub == ["data"]


def test_ensure_github_ids_records_them_before_deploy(tmp_path: Path) -> None:
    tenant = _tenant()
    path = tmp_path / "renglo.yaml"
    path.write_text(dump_tenant(tenant), encoding="utf-8")

    preview = ensure_github_ids(path, lookup=lambda _repo: ("11", "22"), dry_run=True)
    assert preview == ["would set github.owner_id to 11", "would set github.repo_id to 22"]
    assert "owner_id" not in path.read_text(encoding="utf-8")

    notes = ensure_github_ids(path, lookup=lambda _repo: ("11", "22"))
    assert notes == ["github.owner_id set to 11", "github.repo_id set to 22"]
    assert "owner_id: '11'" in path.read_text(encoding="utf-8")
    assert ensure_github_ids(path, lookup=lambda _repo: ("99", "88")) == []


def test_insert_github_ids_keeps_the_rest_of_the_file() -> None:
    text = "name: acme1\ngithub:\n  repo: acmeco/acme-bom\nemail:\n  from: a@b.c\n"
    updated = insert_github_ids(text, "11", "22")
    assert "repo: acmeco/acme-bom\n  owner_id: '11'\n  repo_id: '22'\nemail:" in updated
    assert insert_github_ids(updated, "11", "22") == updated


def _published() -> dict[str, str]:
    return {
        "renglo-ops": "0.3.2",
        "renglo-lib": "1.4.0",
        "renglo-api": "1.4.1",
        "acme-wl": "0.2.0",
        "@acme/wl": "0.2.0",
        "@renglo/console": "1.8.0",
        "renglo-data": "2.0.1",
        "@renglo/data": "2.0.1",
        "renglo-schd": "1.1.0",
        "@renglo/schd": "1.1.0",
    }


def test_registry_follows_domain_and_scope() -> None:
    tenant = _tenant()
    prepare_tenant(tenant)
    assert registry_for(tenant, "renglo-lib", "python").domain == "renglo"
    assert registry_for(tenant, "renglo-ops", "python").domain == "renglo"
    assert registry_for(tenant, "acme-wl", "python").domain == "acme"
    assert registry_for(tenant, "@renglo/data", "npm").domain == "renglo"
    assert registry_for(tenant, "@acme/wl", "npm").domain == "acme"


def test_setup_writes_pins_and_ids(tmp_path: Path) -> None:
    tenant = _tenant(platform="0.1.16", release_bom="0.1.0", release_console="0.1.0")
    prepare_tenant(tenant)
    path = tmp_path / "renglo.yaml"
    path.write_text(dump_tenant(tenant), encoding="utf-8")

    notes = setup_bom(tmp_path, lookup=lambda _repo: ("11", "22"), versions=_published())
    written = load_tenant(path)
    platform = checked_out_platform_version()
    assert written.github_owner_id == "11"
    assert written.github_repo_id == "22"
    assert written.platform == platform
    assert written.release_bom == "0.0.1"
    assert written.release_console == "0.0.1"
    assert any("github.owner_id" in note for note in notes)
    assert f"platform: {platform}" in path.read_text(encoding="utf-8")

    hub = json.loads((tmp_path / "bom" / "v0.0.1.json").read_text(encoding="utf-8"))
    console = json.loads((tmp_path / "console_bom" / "v0.0.1.json").read_text(encoding="utf-8"))
    assert hub["version"] == "v0.0.1"
    assert hub["platform"] == platform
    assert console["platform"] == platform
    assert hub["python"]["renglo-lib"] == "1.4.0"
    assert hub["python"]["renglo-api"] == "1.4.1"
    assert hub["python"]["acme-wl"] == "0.2.0"
    assert hub["python"]["renglo-data"] == "2.0.1"
    assert hub["python"]["renglo-schd"] == "1.1.0"
    assert console["npm"]["@renglo/console"] == "1.8.0"
    assert console["npm"]["@acme/wl"] == "0.2.0"
    assert console["npm"]["@renglo/data"] == "2.0.1"
    assert console["npm"]["@renglo/schd"] == "1.1.0"
    assert "owner_id" in path.read_text(encoding="utf-8")
    yaml.safe_load(path.read_text(encoding="utf-8"))


def test_release_candidate_versions() -> None:
    assert is_release_candidate("0.0.20rc2")
    assert is_release_candidate("0.0.25-rc.2")
    assert is_release_candidate("1.0.0-RC.1")
    assert not is_release_candidate("0.0.9")
    assert not is_release_candidate("0.1.16")
    assert not is_release_candidate("1.0.0a1")


class _Pages:
    def __init__(self, pages: list[list[str]]) -> None:
        self.pages = pages

    def list_package_versions(self, **kwargs):
        index = int(kwargs.get("nextToken") or "0")
        page = {"versions": [{"version": item} for item in self.pages[index]]}
        if index + 1 < len(self.pages):
            page["nextToken"] = str(index + 1)
        return page


def test_latest_published_skips_release_candidates() -> None:
    registry = RegistryRef(domain="renglo", account="339713094352")
    client = _Pages([["0.0.20rc2", "0.0.19rc1"], ["0.0.18", "0.0.17rc1"]])
    found = latest_published(client, registry, "renglo-lib", "python", account_id="1")
    assert found == "0.0.18"
    assert latest_published(_Pages([["1.2.0", "1.2.0rc1"]]), registry, "renglo-lib", "python", account_id="1") == "1.2.0"
    assert latest_published(_Pages([["0.0.20rc2"]]), registry, "renglo-lib", "python", account_id="1") == ""


def test_first_bom_replaces_a_platform_numbered_manifest(tmp_path: Path) -> None:
    platform = checked_out_platform_version()
    tenant = _tenant(platform=platform, release_bom=platform, release_console=platform)
    prepare_tenant(tenant)
    path = tmp_path / "renglo.yaml"
    path.write_text(dump_tenant(tenant), encoding="utf-8")
    (tmp_path / "bom").mkdir()
    (tmp_path / "bom" / f"v{platform}.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "console_bom").mkdir()
    (tmp_path / "console_bom" / f"v{platform}.json").write_text("{}\n", encoding="utf-8")

    setup_bom(tmp_path, lookup=lambda _repo: ("11", "22"), versions=_published())

    assert not (tmp_path / "bom" / f"v{platform}.json").exists()
    hub = json.loads((tmp_path / "bom" / "v0.0.1.json").read_text(encoding="utf-8"))
    assert hub["platform"] == platform
    assert "bom: 0.0.1" in path.read_text(encoding="utf-8")
    assert f"bom: {platform}" not in path.read_text(encoding="utf-8")


def test_an_existing_manifest_keeps_its_release(tmp_path: Path) -> None:
    platform = checked_out_platform_version()
    tenant = _tenant(platform=platform, release_bom="0.0.3", release_console="0.0.3")
    prepare_tenant(tenant)
    (tmp_path / "renglo.yaml").write_text(dump_tenant(tenant), encoding="utf-8")
    (tmp_path / "bom").mkdir()
    (tmp_path / "bom" / "v0.0.3.json").write_text("{}\n", encoding="utf-8")

    setup_bom(tmp_path, lookup=lambda _repo: ("11", "22"), versions=_published())

    written = load_tenant(tmp_path / "renglo.yaml")
    assert written.release_bom == "0.0.3"
    hub = json.loads((tmp_path / "bom" / "v0.0.3.json").read_text(encoding="utf-8"))
    assert hub["platform"] == platform
    assert not (tmp_path / "bom" / "v0.0.1.json").exists()
