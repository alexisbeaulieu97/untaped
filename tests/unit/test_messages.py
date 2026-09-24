"""Behavioural tests for the shared message helpers in ``untaped.messages``."""

from __future__ import annotations

from pathlib import Path

from untaped.messages import hint, not_found, plural, q, summary


def test_plural_picks_the_noun_form_from_the_count() -> None:
    assert plural(0, "repo") == "0 repos"
    assert plural(1, "repo") == "1 repo"
    assert plural(2, "repo") == "2 repos"
    assert plural(2, "index", "indexes") == "2 indexes"
    assert plural(1, "index", "indexes") == "1 index"


def test_q_quotes_the_string_form_never_a_repr() -> None:
    assert q("prod") == "'prod'"
    assert q(Path("/tmp/x")) == "'/tmp/x'"
    assert q(3) == "'3'"
    assert q("it's") == '"it\'s"'  # embedded quotes stay unambiguous


def test_not_found_lists_known_names_when_given() -> None:
    assert not_found("profile", "prod") == "profile not found: 'prod'"
    assert (
        not_found("profile", "prod", known=["default", "stage"])
        == "profile not found: 'prod'; known: default, stage"
    )
    assert not_found("profile", "prod", known=[]) == "profile not found: 'prod'; known: none"


def test_hint_names_the_full_command_once() -> None:
    assert hint("config set awx.token --prompt") == (
        "hint: run `untaped config set awx.token --prompt`"
    )
    assert hint("untaped doctor") == "hint: run `untaped doctor`"


def test_summary_drops_zero_counts() -> None:
    assert summary("sync", {"cloned": 2, "unchanged": 0, "failed": 1}) == (
        "sync: 2 cloned, 1 failed"
    )
    assert summary("sync", {"cloned": 0}) == "sync: nothing to do"
