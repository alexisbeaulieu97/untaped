"""Dispatch takes the root options the token pass left an 11.x seam command, as before.

Around ``--help``, between command words, and in the parse retry, which
reads the line by the options cyclopts parsed it by.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from cyclopts import App, Parameter

from untaped.runtime import bootstrap
from untaped.runtime.output_policy import is_verbose
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.runtime.root_options import _root_options, applied_root_options
from untaped.runtime.root_options_seam import _take_help_root_options
from untaped.sdk._cli import create_app, echo
from untaped.sdk.marks import show_deprecated


def _strip(tokens: list[str]) -> list[str]:
    with applied_root_options(tokens, _root_options()) as line:
        return _take_help_root_options(line.tokens, _root_options(), line)


def test_help_strips_an_11x_command_s_root_options_around_it() -> None:
    assert _strip(["svc", "--help", "--verbose"]) == ["svc", "--help"]
    assert _strip(["svc", "--profile", "x", "--help"]) == ["svc", "--help"]
    assert _strip(["--help", "svc", "--profile=x"]) == ["--help", "svc"]
    assert _strip(["svc", "--help", "-xv"]) == ["svc", "--help", "-x"]


def test_tokens_after_the_separator_are_never_root_options() -> None:
    tokens = ["svc", "--help", "--", "--verbose", "--deprecated"]

    with applied_root_options(tokens, _root_options()) as line:
        assert _take_help_root_options(line.tokens, _root_options(), line) == tokens
        assert not is_verbose()
        assert not show_deprecated()


def test_without_help_the_tokens_are_left_to_the_normal_dispatch() -> None:
    tokens = ["svc", "own", "--deprecated"]

    assert _strip(tokens) == tokens


def _old_app() -> App:
    app = create_app(name="old", help="An 11.x plugin.")

    @app.command(name="who")
    def who(
        *,
        x: Annotated[bool, Parameter(name=["--x", "-x"])] = False,
        name: Annotated[str | None, Parameter(name=["--name", "-n"])] = None,
        pattern: Annotated[
            str | None, Parameter(name=["--pattern", "-p"], allow_leading_hyphen=True)
        ] = None,
    ) -> None:
        """Print what it was given, and whether the root went verbose."""
        echo(f"x={x} name={name} p={pattern} verbose={is_verbose()}")

    @app.command(name="own")
    def own(
        *, deprecated: Annotated[bool, Parameter(name="--deprecated", help="Own flag.")] = False
    ) -> None:
        """Print whether this command saw its own ``--deprecated``."""
        echo(f"own={deprecated} root={show_deprecated()}")

    jobs = create_app(name="jobs", help="Jobs.")

    @jobs.command(name="list")
    def jobs_list() -> None:
        """List nothing."""

    app.command(jobs)
    return app


OLD = PluginCandidate(
    distribution="untaped-old", name="old", target=PluginSpec(name="old", app_factory=_old_app)
)


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize(
    ("argv", "code", "out", "err"),
    [
        (["old", "who", "-xv"], 0, "x=True name=None p=None verbose=True", ""),
        (["old", "who", "-vx"], 0, "x=True name=None p=None verbose=True", ""),
        (["old", "who", "-vn", "a"], 0, "x=False name=a p=None verbose=True", ""),
        (["old", "who", "-vv"], 0, "x=False name=None p=None verbose=True", ""),
        (["old", "who", "-vq"], 2, "", "--verbose and --quiet cannot be combined"),
        (["old", "who", "-xh"], 0, "Usage: untaped old who", ""),
        # `-n` reads the rest of the token as cyclopts does, so it is no root letter
        (["old", "who", "-vn=5"], 0, "x=False name==5 p=None verbose=True", ""),
        (["old", "--profile"], 2, "", "--profile expects a value"),
        (["old", "jobs", "--profile"], 2, "", "--profile expects a value"),
        (["old", "--verbose=x"], 2, "", "--verbose takes no value"),
        # the retry reads a malformed value as the path does, in line order
        (["old", "who", "-x", "--profile"], 2, "", "--profile expects a value"),
        (["old", "who", "-x", "--verbose=x"], 2, "", "--verbose takes no value"),
        (["old", "who", "-x", "-v", "-q", "--profile"], 2, "", "cannot be combined"),
        (["old", "who", "-v", "-q", "--profile"], 2, "", "cannot be combined"),
        (["old", "who", "-z"], 2, "", "Unknown option: -z"),
        # as before the token pass: the seam command parses it, so the root never sees it
        (["old", "own", "--deprecated"], 0, "own=True root=False", ""),
    ],
)
def test_an_11x_command_reads_root_letters_and_root_option_errors_as_before(
    argv: list[str], code: int, out: str, err: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=[OLD])
    captured = capsys.readouterr()

    assert (exit_info.value.code or 0) == code
    assert out in captured.out
    assert err in captured.err


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize(
    ("argv", "out"),
    [
        # an option's value holds no root letter: the retry takes the next one
        (["old", "who", "-pv", "-xv"], "x=True name=None p=v verbose=True"),
        (["old", "who", "-nv", "-xv"], "x=True name=v p=None verbose=True"),
        (["old", "who", "-nvx", "-xv"], "x=True name=vx p=None verbose=True"),
        (["old", "who", "-p", "-av", "-xv"], "x=True name=None p=-av verbose=True"),
        (["old", "who", "--pattern", "-v", "-xv"], "x=True name=None p=-v verbose=True"),
    ],
)
def test_the_retry_takes_a_root_option_no_option_reads_as_its_value(
    argv: list[str], out: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=[OLD])
    captured = capsys.readouterr()

    assert (exit_info.value.code or 0, captured.out.strip()) == (0, out), captured.err


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize(
    ("argv", "out"),
    [
        (["old", "who", "-v", "-v"], "x=False name=None p=None verbose=True"),
        (["old", "who", "-xv", "-v"], "x=True name=None p=None verbose=True"),
        (["old", "who", "-v", "-xv"], "x=True name=None p=None verbose=True"),
        (["old", "who", "-xvv"], "x=True name=None p=None verbose=True"),
        (
            ["old", "who", "--profile", "a", "--profile", "b", "-x"],
            "x=True name=None p=None verbose=False",
        ),
    ],
)
def test_the_retry_takes_each_repeat_of_a_root_option(
    argv: list[str], out: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=[OLD])
    captured = capsys.readouterr()

    assert (exit_info.value.code or 0, captured.out.strip()) == (0, out), captured.err
