from renglo_ops.model.placement import (
    placement_hub_missing_packages,
    resolve_package_handle,
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
