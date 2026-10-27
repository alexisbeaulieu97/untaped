"""The line grammar: ``parse(render(lines)) == lines``, the header and the marks."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.core.surface import (
    FORMAT,
    DeprecatedMark,
    Line,
    Postponement,
    due,
    parse,
    postponements,
    render,
)

_COMMITTED = Path(__file__).resolve().parents[6] / "tests" / "repo" / "surface.txt"


def test_the_committed_file_round_trips() -> None:
    text = _COMMITTED.read_text(encoding="utf-8")
    assert render(parse(text)) == text


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [Line("cli untaped")],
        [
            Line("cli untaped x --format alias=-f", DeprecatedMark()),
            Line("cfg a.b string default=unset"),
        ],
        [Line('sdk m.f(name=) default="a b"', "experimental"), Line("state a.b array")],
        [Line("contract o.M.name string(pattern=^[^/\\s]+$)")],
        [
            Line(
                "cli untaped plugin check", DeprecatedMark(12, "untaped lint, from untaped[lint]")
            ),
            Line("cfg a.old -> a.new", DeprecatedMark(12)),
            Line("sdk m.f()", DeprecatedMark(None, "m.g; or m.h")),
        ],
    ],
)
def test_parse_reads_what_render_writes(lines: list[Line]) -> None:
    assert parse(render(lines)) == sorted(lines)


def test_render_sorts_and_dedupes_under_the_header() -> None:
    text = render([Line("cli untaped b"), Line("cli untaped a"), Line("cli untaped b")])
    assert text == "# untaped surface 1\ncli untaped a\ncli untaped b\n"


def test_render_writes_the_lowest_format_its_marks_need() -> None:
    undated = render([Line("cli untaped a", DeprecatedMark(None, "untaped b"))])
    dated = render([Line("cli untaped a", DeprecatedMark(12))])
    assert undated == "# untaped surface 1\ncli untaped a  [deprecated]\n"
    assert dated == f"# untaped surface {FORMAT}\ncli untaped a  [deprecated, removed in 12.0]\n"


def test_an_experimental_mark_reads_back_and_an_older_format_still_parses() -> None:
    lines = [Line("cli untaped docs", "experimental"), Line("cli untaped x", DeprecatedMark(12))]
    assert parse(render(lines)) == lines
    assert parse("# untaped surface 1\ncli untaped a  [deprecated]\n") == [
        Line("cli untaped a", DeprecatedMark())
    ]


@pytest.mark.parametrize(
    ("mark", "text"),
    [
        (DeprecatedMark(), "deprecated"),
        (DeprecatedMark(12), "deprecated, removed in 12.0"),
        (DeprecatedMark(None, "x"), "deprecated; use x"),
        (DeprecatedMark(13, "a [b]; c"), "deprecated, removed in 13.0; use a [b]; c"),
    ],
)
def test_a_deprecated_mark_spells_its_date_and_replacement(mark: DeprecatedMark, text: str) -> None:
    line = Line("cli untaped x", mark)
    assert str(line) == f"cli untaped x  [{text}]"
    dated = Line("cli untaped y", DeprecatedMark(12))
    assert parse(render([line, dated])) == [line, dated]


@pytest.mark.parametrize("use", ["", " x", "a\nb", "a  [b"])
def test_a_replacement_is_one_line_without_the_separator(use: str) -> None:
    with pytest.raises(ValueError, match="replacement"):
        DeprecatedMark(12, use)


@pytest.mark.parametrize(
    "mark", ["deprecated, removed in 12", "deprecated, removed in 0.0", "stable", "deprecated;"]
)
def test_parse_refuses_an_unknown_mark(mark: str) -> None:
    with pytest.raises(ValueError, match="mark"):
        parse(f"# untaped surface {FORMAT}\ncli untaped x  [{mark}]\n")


def test_postponements_pair_lines_both_files_date() -> None:
    baseline = [
        Line("cli untaped a", DeprecatedMark(12)),
        Line("cli untaped b", DeprecatedMark(12)),
        Line("cli untaped c", DeprecatedMark()),
        Line("cli untaped d", DeprecatedMark(12)),
    ]
    current = [
        Line("cli untaped a", DeprecatedMark(13, "x")),
        Line("cli untaped b", DeprecatedMark(12)),
        Line("cli untaped c", DeprecatedMark(13)),
        Line("cli untaped e", DeprecatedMark(13)),
    ]
    assert postponements(baseline, current) == [Postponement(current[0], was=12, now=13)]


def test_due_is_every_line_dated_at_or_below_the_major() -> None:
    lines = [
        Line("cli untaped a", DeprecatedMark(12)),
        Line("cli untaped b", DeprecatedMark(13)),
        Line("cli untaped c", DeprecatedMark()),
        Line("cli untaped d", "experimental"),
        Line("cli untaped e", DeprecatedMark(11)),
    ]
    assert due(lines, 12) == [lines[0], lines[4]]


def test_the_mark_is_not_the_identity() -> None:
    line = Line("cli untaped x", DeprecatedMark())
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
