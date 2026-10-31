from renglo_ops.localdev_render import _render_env_development
from renglo_ops.model.placement import (
    placement_hub_missing_packages,
    resolve_package_handle,
    wl_npm_package,
)


def _packages() -> dict:
    return {
        "breakdown": {"python": "skbrk-breakdown", "npm": "@skbrk/breakdown"},
        "data": {"python": "renglo-data", "npm": "@renglo/data"},
    }


def test_resolve_package_handle_accepts_slot_or_dist() -> None:
    packages = _packages()
    assert resolve_package_handle(packages, "breakdown") == "breakdown"
    assert resolve_package_handle(packages, "skbrk-breakdown") == "breakdown"
    assert resolve_package_handle(packages, "@skbrk/breakdown") == "breakdown"
    assert resolve_package_handle(packages, "renglo-data") == "data"


def test_placement_hub_missing_packages() -> None:
    packages = _packages()
    assert not placement_hub_missing_packages(packages, ["breakdown", "renglo-data"])
    assert placement_hub_missing_packages(packages, ["missing-slot"]) == ["missing-slot"]


def test_wl_npm_package_is_the_scoped_wl_name() -> None:
    packages = {
        **_packages(),
        "liga-wl": {"python": "liga-wl", "npm": "@liga/wl"},
    }
    assert wl_npm_package(packages) == "@liga/wl"
    assert wl_npm_package(_packages()) == ""
    rendered = _render_env_development({}, api_local_url="http://127.0.0.1:5001", ws_local_url="ws://127.0.0.1:8080", wl_npm="@liga/wl")
    assert 'VITE_WL_PACKAGE="@liga/wl"' in rendered
