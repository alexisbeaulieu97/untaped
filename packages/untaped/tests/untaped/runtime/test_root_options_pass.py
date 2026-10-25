"""The token pass reads a line by the declared command's shape before the run starts.

It takes a root option wherever it sits among a declared command's own
options and never one the command reads as a value; an 11.x seam command
gets only its leading root options taken here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import pytest
from cyclopts import App, Parameter
from pydantic import BaseModel, Field

from untaped.runtime import bootstrap
from untaped.runtime.declaration import CommandDecl, Declaration, GroupDecl, OptionDecl, PluginDecl
from untaped.runtime.output_policy import is_verbose
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.runtime.root_options import _root_options, applied_root_options
from untaped.sdk import Command, Plugin
from untaped.sdk._cli import create_app, echo
from untaped.sdk.fields import AllowHyphen, Positional, Short
from untaped.sdk.marks import show_deprecated
from untaped.sdk.testing import CliInvoker


def _option(field: str, token: str = "str", **extra: object) -> OptionDecl:
    positional = bool(extra.get("positional"))
    return OptionDecl(
        field=field,
        flag=None if positional else f"--{field}",
        metavar=field.upper(),
        token=token,
        help=f"The {field}.",
        **extra,  # type: ignore[arg-type]
    )


def _command(path: tuple[str, ...], *options: OptionDecl) -> CommandDecl:
    return CommandDecl(
        name=path[-1],
        path=path,
        target=f"hello:{path[-1]}",
        summary=".",
        doc=".",
        options=options,
        returns="nothing",
    )


GREP = _command(
    ("hello", "grep"),
    _option("x", "bool", short="-x"),
    _option("pattern", short="-p", allow_hyphen=True),
    _option("exclude", short="-e"),
    _option("count", "int", short="-n"),
    _option("file", positional=True),
)
RUN = _command(("hello", "run"), _option("cmd", "list[str]", positional=True, allow_hyphen=True))
APPLY = _command(("hello", "apply"), _option("files", "list[str]", positional=True, or_stdin=True))

#: ``hello greet [--names N]... [--loud] [WHO]``, ``hello grep [-x] [-p PATTERN]
#: [-e EXCLUDE] [-n COUNT] [FILE]`` (``-p`` takes a leading ``-``), ``hello run CMD...`` (a command
#: line to run, leading ``-`` allowed), ``hello apply FILES...`` (``-`` reads
#: stdin), ``hello exec [CMD]...`` (a group whose own command takes a command
#: line) and ``hello exec sub``, ``hello jobs [ID]`` (a group with a command of
#: its own) and ``hello jobs list``.
DECLARATION = Declaration(
    plugins=(
        PluginDecl(
            name="hello",
            target="hello:Hello",
            package="hello",
            summary=".",
            doc=".",
            settings="hello:Settings",
            commands=(
                _command(
                    ("hello", "greet"),
                    _option("names", "list[str]"),
                    _option("loud", "bool"),
                    _option("who", positional=True),
                ),
                GREP,
                RUN,
                APPLY,
                GroupDecl(
                    name="exec",
                    path=("hello", "exec"),
                    target="hello:Exec",
                    summary=".",
                    doc=".",
                    commands=(_command(("hello", "exec", "sub")),),
                    runs=_command(
                        ("hello", "exec"),
                        _option("cmd", "list[str]", positional=True, allow_hyphen=True),
                    ),
                ),
                GroupDecl(
                    name="jobs",
                    path=("hello", "jobs"),
                    target="hello:Jobs",
                    summary=".",
                    doc=".",
                    commands=(_command(("hello", "jobs", "list")),),
                    runs=_command(("hello", "jobs"), _option("id", positional=True)),
                ),
            ),
        ),
    )
)


@pytest.fixture
def read() -> Iterator[None]:
    yield
    assert not is_verbose()  # every pass is undone when its block ends


def _line(*tokens: str) -> tuple[list[str], bool, bool]:
    """What the pass leaves of ``tokens``, whether it placed them, and whether ``-v`` is on."""
    with applied_root_options(tokens, _root_options(), DECLARATION) as line:
        return line.tokens, line.placed, is_verbose()


@pytest.mark.usefixtures("read")
@pytest.mark.parametrize(
    "tokens",
    [
        ["-v", "hello", "greet", "--names", "Ada"],
        ["hello", "-v", "greet", "--names", "Ada"],
        ["hello", "greet", "-v", "--names", "Ada"],
        ["hello", "greet", "--names", "Ada", "-v"],
        ["hello", "greet", "--names", "Ada", "--verbose"],
    ],
)
def test_a_root_option_is_taken_wherever_it_sits_among_a_declared_command_s(
    tokens: list[str],
) -> None:
    assert _line(*tokens) == (["hello", "greet", "--names", "Ada"], True, True)


@pytest.mark.usefixtures("read")
def test_a_value_taking_option_reads_its_value_and_a_flag_none() -> None:
    assert _line("hello", "greet", "--loud", "-v", "Ada") == (
        ["hello", "greet", "--loud", "Ada"],
        True,
        True,
    )
    # an option taking a value reads the next token, whatever it looks like:
    # cyclopts refuses `-v` as the value, so the line stays as cyclopts reads it
    assert _line("hello", "greet", "--names", "-v") == (
        ["hello", "greet", "--names", "-v"],
        True,
        False,
    )


@pytest.mark.usefixtures("read")
@pytest.mark.parametrize(
    ("tokens", "verbose"),
    [
        # -x, then -p reading the next token (it takes a leading `-`)
        (["-xp", "-v", "f"], False),
        # -e takes no leading `-`: cyclopts refuses `-v` as its value, so it stays
        (["-xe", "-v", "f"], False),
        # a value attached to the cluster's last letter: the next token is free
        (["-xpfoo", "-v", "f"], True),
        (["-n5", "-v", "f"], True),
        (["-n=5", "-v", "f"], True),
        # no option is spelled `-xn`: the cluster's `-n` reads `=5`, attached
        (["-xn=5", "-v", "f"], True),
        # a cluster of flags only
        (["-xx", "-v", "f"], True),
    ],
)
def test_a_short_cluster_is_read_letter_by_letter_as_cyclopts_reads_it(
    tokens: list[str], verbose: bool
) -> None:
    left = [token for token in tokens if not verbose or token != "-v"]
    assert _line("hello", "grep", *tokens) == (["hello", "grep", *left], True, verbose)


@pytest.mark.usefixtures("read")
@pytest.mark.parametrize(
    ("tokens", "left", "verbose"),
    [
        # as cyclopts reads a cluster once `-v` and `-h` are letters in it
        (["-xv", "f"], ["-x", "f"], True),
        (["-vx", "f"], ["-x", "f"], True),
        (["-xvn5"], ["-x", "-n5"], True),
        (["-xvn", "5"], ["-x", "-n", "5"], True),
        (["-vn=5"], ["-n", "=5"], True),  # `-n` reads `=5`, as in `-xn=5`
        (["-xh"], ["-x", "-h"], False),
        # `-n` reads the rest of the token, `v` included
        (["-nv"], ["-nv"], False),
        # cyclopts refuses the unknown letter: the cluster stays whole for it to
        (["-zv"], ["-zv"], False),
    ],
)
def test_a_root_letter_in_a_short_cluster_is_the_root_s(
    tokens: list[str], left: list[str], verbose: bool
) -> None:
    assert _line("hello", "grep", *tokens) == (["hello", "grep", *left], True, verbose)


@pytest.mark.usefixtures("read")
def test_a_cluster_of_root_letters_is_the_root_s_unless_it_is_a_value() -> None:
    assert _line("hello", "-v", "greet") == (["hello", "greet"], True, True)
    assert _line("-vh") == (["-h"], True, True)
    assert _line("hello", "run", "-vq") == (["hello", "run", "-vq"], True, False)
    with pytest.raises(SystemExit) as exit_info, applied_root_options(["-vq"], _root_options()):
        pass
    assert exit_info.value.code == 2


@pytest.mark.usefixtures("read")
@pytest.mark.parametrize(
    ("tokens", "left", "verbose"),
    [
        (["hello", "exec", "-v"], ["hello", "exec", "-v"], False),
        (["hello", "exec", "-v", "x"], ["hello", "exec", "-v", "x"], False),
        (["hello", "exec", "-q", "ls"], ["hello", "exec", "-q", "ls"], False),
        (["hello", "exec", "--profile", "a"], ["hello", "exec", "--profile", "a"], False),
        (["hello", "exec", "--profile"], ["hello", "exec", "--profile"], False),
        # another command word follows: they are the root's
        (["hello", "exec", "-v", "sub"], ["hello", "exec", "sub"], True),
        (["hello", "-v", "exec", "-v"], ["hello", "exec", "-v"], True),
    ],
)
def test_root_options_between_words_are_the_root_s_only_before_another_word(
    tokens: list[str], left: list[str], verbose: bool
) -> None:
    assert _line(*tokens) == (left, True, verbose)


@pytest.mark.usefixtures("read")
@pytest.mark.parametrize(
    ("tokens", "left"),
    [
        (["hello", "run", "ls", "-v"], ["hello", "run", "ls", "-v"]),
        (["hello", "run", "-v", "ls"], ["hello", "run", "-v", "ls"]),
        (["hello", "apply", "-v"], ["hello", "apply", "-v"]),
    ],
)
def test_an_argument_taking_a_leading_hyphen_keeps_a_root_option_s_spelling(
    tokens: list[str], left: list[str]
) -> None:
    assert _line(*tokens) == (left, True, False)


@pytest.mark.usefixtures("read")
def test_an_argument_that_takes_no_hyphen_leaves_the_root_option_to_the_root() -> None:
    assert _line("hello", "greet", "Ada", "-v") == (["hello", "greet", "Ada"], True, True)
    assert _line("hello", "jobs", "7", "-v") == (["hello", "jobs", "7"], True, True)
    assert _line("hello", "jobs", "-v", "list") == (["hello", "jobs", "list"], True, True)


@pytest.mark.usefixtures("read")
def test_a_separator_ends_the_line_unless_it_only_closed_the_leading_root_options() -> None:
    assert _line("hello", "greet", "--", "-v") == (["hello", "greet", "--", "-v"], True, False)
    assert _line("--profile=x", "--", "hello", "greet", "-v") == (["hello", "greet"], True, True)


@pytest.mark.usefixtures("read")
def test_an_11x_command_gets_only_its_leading_root_options_taken() -> None:
    assert _line("-v", "old", "who", "--quiet") == (["old", "who", "--quiet"], False, True)
    with applied_root_options(["old", "-v"], _root_options()) as line:  # no declaration
        assert (line.tokens, line.placed, is_verbose()) == (["old", "-v"], False, False)


def test_verbose_and_quiet_conflict_only_as_root_options() -> None:
    with applied_root_options(["hello", "run", "-q", "-v"], _root_options(), DECLARATION):
        assert not is_verbose()  # both are the command line's: neither is the root's
    with (
        pytest.raises(SystemExit) as exit_info,
        applied_root_options(["hello", "greet", "-q", "-v"], _root_options(), DECLARATION),
    ):
        pass
    assert exit_info.value.code == 2


class HelloSettings(BaseModel):
    """Nothing to configure."""


class Greet(Command):
    """Print the names, and whether the root went verbose and showed deprecated commands."""

    names: list[str] = Field(default_factory=list, description="Who to greet.")

    def run(self) -> str:
        return f"{','.join(self.names)} verbose={is_verbose()} deprecated={show_deprecated()}"


class Run(Command):
    """Print the command line given."""

    cmd: Positional[list[str], AllowHyphen] = Field(description="The command line.")

    def run(self) -> str:
        return " ".join(self.cmd)


class Grep(Command):
    """Print what it was given, and whether the root went verbose."""

    x: Annotated[bool, Short("-x")] = Field(False, description="A flag.")
    pattern: Annotated[str | None, Short("-p"), AllowHyphen] = Field(None, description="Any.")
    count: Annotated[int | None, Short("-n")] = Field(None, description="How many.")
    file: Positional[str | None] = Field(None, description="The file.")

    def run(self) -> str:
        return f"x={self.x} p={self.pattern} n={self.count} file={self.file} verbose={is_verbose()}"


class Hello(Plugin):
    """Greet (a 12.0 plugin)."""

    settings = HelloSettings
    commands = (Greet, Grep, Run)


HELLO = PluginCandidate(distribution="untaped-hello", name="hello", target=Hello)


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize(
    "argv",
    [
        ["hello", "greet", "--names", "Ada", "-v", "--deprecated"],
        ["--deprecated", "hello", "greet", "-v", "--names", "Ada"],
    ],
)
def test_a_declared_command_runs_with_the_root_options_among_its_own(argv: list[str]) -> None:
    result = CliInvoker().invoke(bootstrap.build_root_app(candidates=[HELLO]).meta, argv)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "Ada verbose=True deprecated=True"


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
def test_a_declared_command_line_to_run_keeps_its_root_option_spellings() -> None:
    root = bootstrap.build_root_app(candidates=[HELLO])

    result = CliInvoker().invoke(root.meta, ["hello", "run", "ls", "-v", "-q"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ls -v -q"


def _outcome(
    argv: list[str],
    *,
    kept: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[object, str, str]:
    """Run ``argv`` from the start, the line read with or without a kept declaration.

    Without one (a first start, changed candidates, candidates given as
    objects), dispatch reads the line by the composition's declaration.
    """
    declaration = bootstrap.compose_root(candidates=[HELLO]).mount.declaration if kept else None
    monkeypatch.setattr(bootstrap, "kept_declaration", lambda _candidates: declaration)
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=[HELLO])
    captured = capsys.readouterr()
    return exit_info.value.code or 0, captured.out.strip(), captured.err.strip()


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize(
    ("argv", "out"),
    [
        (["hello", "grep", "-xp", "-v", "f"], "x=True p=-v n=None file=f verbose=False"),
        (["hello", "grep", "-n5", "-v"], "x=False p=None n=5 file=None verbose=True"),
        (["hello", "grep", "-n=5", "f", "-v"], "x=False p=None n=5 file=f verbose=True"),
        (["hello", "greet", "--names", "Ada", "-v", "-v"], "Ada verbose=True deprecated=False"),
        (
            ["hello", "greet", "-v", "--names", "Ada", "--deprecated"],
            "Ada verbose=True deprecated=True",
        ),
        (["hello", "run", "ls", "-v"], "ls -v"),
        (["hello", "greet", "--help", "--deprecated"], None),
        (["hello", "grep", "-p"], None),
    ],
)
def test_a_line_means_the_same_with_or_without_a_kept_declaration(
    argv: list[str],
    out: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    kept = _outcome(argv, kept=True, monkeypatch=monkeypatch, capsys=capsys)
    cold = _outcome(argv, kept=False, monkeypatch=monkeypatch, capsys=capsys)

    assert cold == kept
    if out is not None:
        assert kept == (0, out, "")


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize("kept", [True, False], ids=["kept", "cold"])
def test_a_value_taking_short_ending_a_cluster_reads_the_next_token_as_cyclopts_does(
    kept: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # -y, then -c reading `-v`: cyclopts refuses it as a value, so the line fails
    argv = ["doctor", "fix", "-yc", "-v", "name"]

    code, _, err = _outcome(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 2
    assert "parameter -c requires an argument" in err


@pytest.mark.usefixtures("fresh_composition", "_isolated_config")
@pytest.mark.parametrize(
    ("argv", "out"),
    [
        (["hello", "grep", "-n5"], "x=False p=None n=5 file=None verbose=False"),
        (["hello", "grep", "-px"], "x=False p=x n=None file=None verbose=False"),
        (["hello", "grep", "-xn5"], "x=True p=None n=5 file=None verbose=False"),
        (["hello", "grep", "-xv"], "x=True p=None n=None file=None verbose=True"),
    ],
)
def test_the_root_driven_directly_reads_the_line_as_run_root_does(
    argv: list[str],
    out: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # the meta parse splits `-n5` into `-n -5`: the pass reads the tokens as given
    result = CliInvoker().invoke(bootstrap.build_root_app(candidates=[HELLO]).meta, argv)
    bootstrap._clear_for_tests()

    assert (result.exit_code, result.stdout.strip()) == (0, out)
    assert _outcome(argv, kept=True, monkeypatch=monkeypatch, capsys=capsys) == (0, out, "")


def _old_app() -> App:
    app = create_app(name="old", help="An 11.x plugin.")

    @app.command(name="who")
    def who(
        *,
        x: Annotated[bool, Parameter(name=["--x", "-x"])] = False,
        name: Annotated[str | None, Parameter(name=["--name", "-n"])] = None,
    ) -> None:
        """Print what it was given, and whether the root went verbose."""
        echo(f"x={x} name={name} verbose={is_verbose()}")

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
        (["old", "who", "-xv"], 0, "x=True name=None verbose=True", ""),
        (["old", "who", "-vx"], 0, "x=True name=None verbose=True", ""),
        (["old", "who", "-vn", "a"], 0, "x=False name=a verbose=True", ""),
        (["old", "who", "-vq"], 2, "", "--verbose and --quiet cannot be combined"),
        (["old", "who", "-xh"], 0, "Usage: untaped old who", ""),
        (["old", "--profile"], 2, "", "--profile expects a value"),
        (["old", "jobs", "--profile"], 2, "", "--profile expects a value"),
        (["old", "--verbose=x"], 2, "", "--verbose takes no value"),
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
