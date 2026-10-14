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


def test_a_rule_named_with_its_key_allows_it() -> None:
    lines = ["x  # untaped: allow lint.error-case, print"]
    assert allowed(lines, 1, "error-case") and allowed(lines, 1, "print")


def test_a_keyed_name_allows_only_its_own_rule() -> None:
    assert not allowed(["x  # untaped: allow lint.error-case"], 1, "print")
