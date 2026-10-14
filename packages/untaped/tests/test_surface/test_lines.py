"""The line grammar: ``parse(render(lines)) == lines``, the header and the marks."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.surface import FORMAT, Line, parse, render

_COMMITTED = Path(__file__).resolve().parents[4] / "tests" / "repo" / "surface.txt"


def test_the_committed_file_round_trips() -> None:
    text = _COMMITTED.read_text(encoding="utf-8")
    assert render(parse(text)) == text


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [Line("cli untaped")],
        [
            Line("cli untaped x --format alias=-f", "deprecated"),
            Line("cfg a.b string default=unset"),
        ],
        [Line('sdk m.f(name=) default="a b"', "experimental"), Line("state a.b array")],
        [Line("contract o.M.name string(pattern=^[^/\\s]+$)")],
    ],
)
def test_parse_reads_what_render_writes(lines: list[Line]) -> None:
    assert parse(render(lines)) == sorted(lines)


def test_render_sorts_and_dedupes_under_the_header() -> None:
    text = render([Line("cli untaped b"), Line("cli untaped a"), Line("cli untaped b")])
    assert text == f"# untaped surface {FORMAT}\ncli untaped a\ncli untaped b\n"


def test_the_mark_is_not_the_identity() -> None:
    line = Line("cli untaped x", "deprecated")
    assert str(line) == "cli untaped x  [deprecated]"
    assert line.identity == "cli untaped x"
    assert line.area == "cli"


@pytest.mark.parametrize("text", ["", " cli", "cli x\ncli y", "cli x  [deprecated]"])
def test_a_line_is_one_line_without_a_mark_in_its_text(text: str) -> None:
    with pytest.raises(ValueError, match="surface line"):
        Line(text)


def test_parse_refuses_a_file_without_the_header() -> None:
    with pytest.raises(ValueError, match="not a surface file"):
        parse("cli untaped\n")


def test_parse_refuses_a_newer_format() -> None:
    with pytest.raises(ValueError, match="newer than this generator"):
        parse(f"# untaped surface {FORMAT + 1}\ncli untaped\n")
