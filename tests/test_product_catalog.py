from pathlib import Path

import yaml

from renglo_ops.model.product_catalog import find_product_catalog, sync_tenant_packages
from renglo_ops.model.tenant import load_tenant

_TENANT = """\
name: acme1
github:
  repo: acmeco/acme-bom
email:
  from: noreply@acme.com
  identity: email
accounts:
  staging:
    id: '123456789012'
    region: us-east-1
    enabled: true
placement:
  hub:
    - renglo-data
  peers: {}
packages:
  renglo-lib:
    python: renglo-lib
  apollo-wl:
    python: apollo-wl
    npm: '@apollo/wl'
  data:
    python: renglo-data
    npm: '@renglo/data'
release:
  bom: 0.1.0
  console: 0.1.0
"""

_CATALOG = """\
packages:
  data:
    python: renglo-data
    npm: '@renglo/data'
  gmail:
    python: renglo-gmail
    npm: '@renglo/gmail'
"""


def _workspace(tmp_path: Path, *, catalog: bool) -> Path:
    if catalog:
        wl = tmp_path / "dev" / "apollo-wl"
        wl.mkdir(parents=True)
        (wl / "product.yaml").write_text(_CATALOG, encoding="utf-8")
    bom = tmp_path / "ops" / "acme-bom"
    bom.mkdir(parents=True)
    (bom / "renglo.yaml").write_text(_TENANT, encoding="utf-8")
    local = tmp_path / ".renglo"
    local.mkdir()
    (local / "local.yaml").write_text("tenant: ops/acme-bom\n", encoding="utf-8")
    return bom / "renglo.yaml"


def test_sync_adds_catalog_packages(tmp_path: Path) -> None:
    path = _workspace(tmp_path, catalog=True)
    result = sync_tenant_packages(load_tenant(path), start=tmp_path)
    assert result.added == ["gmail"]
    assert result.updated == []
    assert result.wrote
    assert result.unplaced == ["gmail"]
    text = path.read_text(encoding="utf-8")
    assert "id: '123456789012'" in text
    assert "renglo-lib:" in text
    assert "apollo-wl:" in text
    assert "python: renglo-gmail" in text
    assert "npm: '@renglo/gmail'" in text
    again = sync_tenant_packages(load_tenant(path), start=tmp_path)
    assert again.added == []
    assert again.wrote is False


def test_sync_missing_catalog_is_optional(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RENGLO_PRODUCT_CATALOG", raising=False)
    path = _workspace(tmp_path, catalog=False)
    before = path.read_text(encoding="utf-8")
    result = sync_tenant_packages(load_tenant(path), start=tmp_path)
    assert result.catalog is None
    assert result.wrote is False
    assert "not found" in result.format()
    assert path.read_text(encoding="utf-8") == before
    assert find_product_catalog(tmp_path, load_tenant(path)) is None


def test_sync_missing_override_is_optional(tmp_path: Path, monkeypatch) -> None:
    path = _workspace(tmp_path, catalog=True)
    monkeypatch.setenv("RENGLO_PRODUCT_CATALOG", str(tmp_path / "missing" / "product.yaml"))
    result = sync_tenant_packages(load_tenant(path), start=tmp_path)
    assert result.catalog is None
    assert result.wrote is False


def test_sync_updates_coordinates(tmp_path: Path) -> None:
    path = _workspace(tmp_path, catalog=True)
    catalog = tmp_path / "dev" / "apollo-wl" / "product.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {"packages": {"data": {"python": "renglo-data", "npm": "@renglo/data-next"}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = sync_tenant_packages(load_tenant(path), start=tmp_path)
    assert result.updated == ["data"]
    assert "npm: '@renglo/data-next'" in path.read_text(encoding="utf-8")
    assert "renglo-lib:" in path.read_text(encoding="utf-8")
