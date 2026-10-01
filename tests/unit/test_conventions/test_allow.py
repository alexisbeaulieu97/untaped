"""The inline ``# untaped: allow <rule>`` marker."""

from __future__ import annotations

from untaped.conventions.allow import allowed

LINES = [
    "x = 1",
    "data = sys.stdin.read()  # untaped: allow sys-stdin",
    "print(x)  # untaped: allow sys-stdin, print",
    "y = 2  # untaped: allow print",
]


def test_marker_on_the_line_allows_that_rule() -> None:
    assert allowed(LINES, 2, "sys-stdin")


def test_comma_list() -> None:
    assert allowed(LINES, 3, "print") and allowed(LINES, 3, "sys-stdin")


def test_wrong_rule_does_not_allow() -> None:
    assert not allowed(LINES, 4, "sys-stdin")


def test_wrong_line_does_not_allow() -> None:
    assert not allowed(LINES, 1, "sys-stdin")


def test_a_rule_name_prefix_does_not_allow() -> None:
    assert not allowed(["x  # untaped: allow sys-stdin-extra"], 1, "sys-stdin")


def test_a_line_past_the_end_does_not_allow() -> None:
    assert not allowed(LINES, 5, "print")
