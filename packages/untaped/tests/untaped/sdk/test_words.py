"""Behavioural tests for the shared message helpers in ``untaped.sdk.words``."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import Command
from untaped.sdk._run import NoRunError
from untaped.sdk.words import (
    command_line,
    elapsed_text,
    hint,
    not_found,
    plural,
    q,
    run_hint,
    shown_path,
    split_profile,
    summary,
)


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


def test_run_hint_is_the_body_a_hint_line_prints() -> None:
    assert run_hint("auth set awx") == "run `untaped auth set awx`"
    assert run_hint("untaped doctor") == "run `untaped doctor`"


def test_summary_drops_zero_counts() -> None:
    assert summary("sync", {"cloned": 2, "unchanged": 0, "failed": 1}) == (
        "sync: 2 cloned, 1 failed"
    )
    assert summary("sync", {"cloned": 0}) == "sync: nothing to do"


@pytest.mark.parametrize(
    ("argv", "profile"),
    [
        (("--profile", "p", "skills", "update"), "p"),
        (("--profile=p", "skills", "update"), "p"),
        (("skills", "update"), None),
    ],
)
def test_split_profile_reads_past_either_spelling(
    argv: tuple[str, ...], profile: str | None
) -> None:
    assert split_profile(argv) == (profile, ["skills", "update"])


def test_shown_path_writes_home_as_a_tilde() -> None:
    assert shown_path(Path.home()) == "~"
    assert shown_path(Path.home() / "x") == "~/x"
    assert shown_path("/elsewhere") == "/elsewhere"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (5, "5.0s"),
        (42.13497729599476, "42.1s"),
        (59.96, "1m00s"),
        (102.0, "1m42s"),
        (3725.0, "1h02m"),
    ],
)
def test_a_run_time_reads_to_the_tenth_then_in_minutes_then_hours(
    seconds: float, expected: str
) -> None:
    assert elapsed_text(seconds) == expected


class _Named(Command):
    """A command a hint names by its class."""

    def run(self) -> None:
        return None


def test_a_command_is_a_string_or_spelled_never_a_bare_list() -> None:
    with pytest.raises(TypeError, match=r"Command\.spelled"):
        command_line(["auth", "login"])  # type: ignore[arg-type]


def test_a_class_is_spelled_only_inside_the_run_that_mounts_it() -> None:
    with pytest.raises(NoRunError):
        hint(_Named.spelled("x"))
