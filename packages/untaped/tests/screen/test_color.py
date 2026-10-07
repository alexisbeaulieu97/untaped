"""One colour decision, from the environment, for both Rich and prompt_toolkit (Rich's half)."""

from __future__ import annotations

import pytest

from untaped.screen.color import ColorMode, detect_color, ptk_depth_name, rich_system

CASES: list[tuple[dict[str, str], ColorMode, str, str]] = [
    ({"COLORTERM": "truecolor"}, ColorMode("truecolor"), "truecolor", "DEPTH_24_BIT"),
    ({"COLORTERM": "24bit", "TERM": "xterm"}, ColorMode("truecolor"), "truecolor", "DEPTH_24_BIT"),
    ({"COLORTERM": "TrueColor "}, ColorMode("truecolor"), "truecolor", "DEPTH_24_BIT"),
    ({"TERM": "xterm-256color"}, ColorMode("256"), "256", "DEPTH_8_BIT"),
    ({"TERM": "screen-256color"}, ColorMode("256"), "256", "DEPTH_8_BIT"),
    ({"TERM": "xterm-kitty"}, ColorMode("256"), "256", "DEPTH_8_BIT"),
    ({"TERM": "xterm"}, ColorMode("standard"), "standard", "DEPTH_4_BIT"),
    ({"TERM": "linux"}, ColorMode("standard"), "standard", "DEPTH_4_BIT"),
    ({}, ColorMode("standard"), "standard", "DEPTH_4_BIT"),
    (
        {"NO_COLOR": "1", "COLORTERM": "truecolor"},
        ColorMode("standard", no_color=True),
        "standard",
        "DEPTH_1_BIT",
    ),
    ({"NO_COLOR": "", "TERM": "xterm-256color"}, ColorMode("256"), "256", "DEPTH_8_BIT"),
]


@pytest.mark.parametrize(("environ", "mode", "rich", "depth"), CASES)
def test_the_environment_decides_one_mode_and_both_libraries_follow(
    environ: dict[str, str], mode: ColorMode, rich: str, depth: str
) -> None:
    decided = detect_color(environ)
    assert decided == mode
    assert rich_system(decided) == rich
    assert ptk_depth_name(decided) == depth


def test_no_color_is_the_only_mode_with_a_one_bit_depth() -> None:
    depths = {ptk_depth_name(detect_color(environ)) for environ, *_ in CASES}
    assert "DEPTH_1_BIT" in depths
    assert ptk_depth_name(ColorMode("truecolor")) != "DEPTH_1_BIT"
