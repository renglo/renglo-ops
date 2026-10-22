import sys
import zipfile
from pathlib import Path

_RELEASE = Path(__file__).resolve().parents[1] / "lib" / "renglo_ops" / "release"
if str(_RELEASE) not in sys.path:
    sys.path.insert(0, str(_RELEASE))

from prepare_handlers_wheelhouse import runtime_install_specs  # noqa: E402


def _wheel(path: Path, dist_info: str, *, extra: bool) -> None:
    meta = "Metadata-Version: 2.1\nName: pkg\nVersion: 0.0.1\n"
    if extra:
        meta += "Provides-Extra: large-dependencies\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{dist_info}/METADATA", meta)


def test_ecs_image_installs_the_extra_lambda_zip_does_not(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _wheel(
        wheelhouse / "renglo_lib-0.0.13-py3-none-any.whl",
        "renglo_lib-0.0.13.dist-info",
        extra=False,
    )
    _wheel(
        wheelhouse / "arbitium_lab-0.0.13-py3-none-any.whl",
        "arbitium_lab-0.0.13.dist-info",
        extra=True,
    )
    packages = ["renglo-lib", "arbitium-lab"]

    assert runtime_install_specs(packages, wheelhouse, large=False) == [
        "renglo-lib==0.0.13",
        "arbitium-lab==0.0.13",
    ]
    assert runtime_install_specs(packages, wheelhouse, large=True) == [
        "renglo-lib==0.0.13",
        "arbitium-lab[large-dependencies]==0.0.13",
    ]
