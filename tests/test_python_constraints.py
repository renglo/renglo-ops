from pathlib import Path

from renglo_ops.release.python_constraints import (
    canonical_name,
    constraints_path,
    find_uv,
    locked_download_command,
    missing_bom_pins,
    parse_exact_pins,
    render_constraints,
)


def test_constraints_path_lives_beside_bom() -> None:
    assert constraints_path(Path("bom/v0.1.17.json")) == Path("constraints/v0.1.17.txt")


def test_parse_exact_pins_ignores_comments_and_rejects_ranges() -> None:
    pins = parse_exact_pins(
        "# header\n\ncognitojwt==1.4.1\n  # via flask-cognito\nFlask==3.1.0\n"
    )
    assert pins == [("cognitojwt", "1.4.1"), ("Flask", "3.1.0")]
    try:
        parse_exact_pins("cognitojwt>=1.1.0,<1.6\n")
    except ValueError as exc:
        assert "not an exact pin" in str(exc)
    else:
        raise AssertionError("range was accepted")


def test_missing_bom_pins_catches_a_stale_lock() -> None:
    pins = [("renglo-api", "0.0.19rc2"), ("cognitojwt", "1.4.1")]
    assert missing_bom_pins({"renglo-api": "0.0.19rc2"}, pins) == []
    assert missing_bom_pins({"renglo_api": "0.0.19rc2"}, pins) == []
    assert missing_bom_pins({"renglo-api": "0.0.18"}, pins) == ["renglo-api==0.0.18"]


def test_locked_download_does_not_resolve() -> None:
    cmd = locked_download_command(
        dest=Path("wheels"),
        constraints=Path("constraints/v0.1.17.txt"),
    )
    assert "--no-deps" in cmd
    assert "-r" in cmd
    assert "constraints/v0.1.17.txt" in cmd
    assert "pip" in cmd


def test_find_uv_does_not_use_the_python_module() -> None:
    found = find_uv()
    if found is None:
        return
    assert found.endswith("/uv") or found == "uv"


def test_render_is_sorted_and_names_when_it_is_written() -> None:
    text = render_constraints(
        [("boto3", "1.43.72"), ("cognitojwt", "1.4.1")],
        bom_file=Path("bom/v0.1.17.json"),
        exclude_newer="2026-11-21T00:00:00Z",
    )
    assert "Written when the BOM is generated." in text
    assert "compile_python_constraints" not in text
    assert text.index("boto3==1.43.72") < text.index("cognitojwt==1.4.1")
    assert canonical_name("Renglo_API") == "renglo-api"
