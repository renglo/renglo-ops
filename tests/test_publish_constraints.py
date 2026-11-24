import json
from pathlib import Path

import pytest

from renglo_ops.model.errors import RengloOpsError
from renglo_ops.release.compile_console_lock import registries_from_renglo_yaml
from renglo_ops.release.publish_constraints import (
    _scrub,
    _write_python_lock,
    console_lock_from_published,
    merge_lock_pins,
    peer_constraints_dest,
    python_lock_text,
)


def test_merge_keeps_the_bom_pin_over_the_resolved_one() -> None:
    pins = merge_lock_pins(
        {"renglo-lib": "1.4.0"},
        [("cognitojwt", "1.4.1"), ("renglo-lib", "9.9.9")],
    )
    assert ("cognitojwt", "1.4.1") in pins
    assert ("renglo-lib", "1.4.0") in pins
    assert ("renglo-lib", "9.9.9") not in pins


def test_python_lock_text_uses_the_published_closure(tmp_path: Path) -> None:
    bom = tmp_path / "bom" / "v0.0.1.json"
    bom.parent.mkdir()
    bom.write_text(json.dumps({"python": {"renglo-lib": "1.4.0"}}), encoding="utf-8")

    def resolve(requirements, extra_args, secrets):
        assert requirements == ["renglo-lib==1.4.0", "wheel"]
        assert extra_args == ["--extra-index-url", "https://example.test/simple"]
        assert secrets == []
        return "cognitojwt==1.4.1\nrenglo-lib==9.9.9\nwheel==0.45.1\n"

    text = python_lock_text(
        bom,
        extra_args=["--extra-index-url", "https://example.test/simple"],
        secrets=[],
        also=["wheel"],
        resolve=resolve,
    )
    assert "cognitojwt==1.4.1" in text
    assert "renglo-lib==1.4.0" in text
    assert "renglo-lib==9.9.9" not in text
    assert "wheel==0.45.1" in text
    assert "Written when the BOM is generated." in text


def test_python_lock_refuses_to_store_a_registry_token(tmp_path: Path) -> None:
    bom = tmp_path / "bom" / "v0.0.1.json"
    bom.parent.mkdir()
    bom.write_text(json.dumps({"python": {"renglo-lib": "1.4.0"}}), encoding="utf-8")

    def resolve(requirements, extra_args, secrets):
        return "cognitojwt==SECRETVALUE\n"

    with pytest.raises(RengloOpsError, match="registry token"):
        _write_python_lock(
            bom,
            extra_args=[],
            secrets=["SECRETVALUE"],
            resolve=resolve,
        )
    assert not (tmp_path / "constraints" / "v0.0.1.txt").exists()


def test_scrub_removes_the_token_and_its_quoted_form() -> None:
    assert _scrub("aws:SECRETVALUE@host and aws:SECRETVALUE", ["SECRETVALUE"]) == "aws:***@host and aws:***"


def test_peer_lock_is_not_written_beside_peers_bom(tmp_path: Path) -> None:
    dest = peer_constraints_dest(tmp_path, "lab", tmp_path / "peers_bom" / "lab" / "v0.0.1.json")
    assert dest == tmp_path / "constraints" / "peers" / "lab" / "v0.0.1.txt"


def test_renglo_yaml_scopes_use_the_staging_account() -> None:
    text = """
accounts:
  staging:
    id: '339713094352'
    region: us-east-1
  production:
    id: '111'
    region: eu-west-1
registries:
- domain: skbrk
  npm: npm-store
  scopes:
  - '@skbrk'
- domain: renglo
  npm: npm-store
  account: '339713094352'
  scopes:
  - '@renglo'
placement:
  hub: []
"""
    found = registries_from_renglo_yaml(text)
    assert found[0]["domain"] == "skbrk"
    assert found[0]["domain_owner"] == "339713094352"
    assert found[0]["region"] == "us-east-1"
    assert found[0]["npm_scopes"] == ["@skbrk"]
    assert found[1]["npm_scopes"] == ["@renglo"]
    assert found[1]["domain_owner"] == "339713094352"


def test_console_lock_requires_the_host_package(tmp_path: Path) -> None:
    bom = tmp_path / "console_bom" / "v0.0.1.json"
    bom.parent.mkdir()
    bom.write_text(json.dumps({"npm": {"@acme/wl": "0.2.0"}}), encoding="utf-8")
    with pytest.raises(RengloOpsError, match="@renglo/console"):
        console_lock_from_published(bom, npmrc_text="", secrets=[])
