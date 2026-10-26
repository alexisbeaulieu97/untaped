"""One run per invocation: started first by the root, with every root option applied."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from untaped.core.surface import generate
from untaped.runtime import bootstrap
from untaped.runtime.declaration import Declaration
from untaped.runtime.profile_resolver import flag_pick
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.runtime.root_options import RootLine
from untaped.runtime.settings import get_settings
from untaped.sdk import Command, Plugin
from untaped.sdk._cli import create_app, echo
from untaped.sdk._run import Run, current_run
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("fresh_composition")

#: The run each command below saw, in the order they ran.
seen: list[Run] = []


class ChimeSettings(BaseModel):
    greeting: str = Field("hello", description="What ring prints.")


class Ring(Command[ChimeSettings]):
    """Print the greeting the run picked."""

    def run(self) -> str:
        seen.append(current_run())
        return self.settings.greeting


class Chime(Plugin):
    """Ring a greeting (a 12.0 plugin)."""

    settings = ChimeSettings
    commands = (Ring,)


class Nest(Command[ChimeSettings]):
    """Call the root again from inside the command, then note the run once it returns."""

    def run(self) -> None:
        seen.append(current_run())
        with bootstrap.kept_composition(), pytest.raises(SystemExit):
            bootstrap.run_root(["chime", "ring"], candidates=[NESTED])
        seen.append(current_run())


class Nesting(Plugin):
    """Ring, and ring again from inside a command."""

    settings = ChimeSettings
    commands = (Ring, Nest)


def _old_app() -> App:
    app = create_app(name="old", help="An 11.x plugin.")

    @app.command(name="who")
    def who() -> None:
        """Note the run."""
        seen.append(current_run())
        echo("old")

    return app


CHIME = PluginCandidate(distribution="untaped-chime", name="chime", target=Chime)
OLD = PluginCandidate(
    distribution="untaped-old", name="old", target=PluginSpec(name="old", app_factory=_old_app)
)
NESTED = PluginCandidate(distribution="untaped-chime", name="chime", target=Nesting)
BROKEN = PluginCandidate(distribution="broken-dist", name="broken", target="no_such_mod:SPEC")


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[Run]:
    """Every run made during the test, however it was made, in order."""
    seen.clear()
    runs: list[Run] = []
    make = Run.__init__

    def counting(self: Run, *args: Any, **kwargs: Any) -> None:
        make(self, *args, **kwargs)
        runs.append(self)

    monkeypatch.setattr(Run, "__init__", counting)
    return runs


def _run_root(argv: list[str], *candidates: PluginCandidate) -> int | str | None:
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=candidates)
    return exit_info.value.code


@pytest.mark.usefixtures("_isolated_config")
@pytest.mark.parametrize("argv", [["chime", "ring"], ["old", "who"]], ids=["declared", "11.x"])
def test_an_invocation_starts_one_run_and_its_command_runs_under_it(
    argv: list[str], started: list[Run], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run_root(argv, CHIME, OLD) in (0, None)

    assert len(started) == 1
    assert seen == started
    assert started[0].composition.owner_of_settings(ChimeSettings) == "chime"
    assert capsys.readouterr().out.strip() in ("hello", "old")


def test_a_trailing_profile_is_the_run_s_pick_for_a_declared_command(
    _isolated_config: Path, started: list[Run], capsys: pytest.CaptureFixture[str]
) -> None:
    _isolated_config.write_text(
        "format_version: 2\nprofiles:\n  loud:\n    chime:\n      greeting: HEY\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    assert _run_root(["chime", "ring", "--profile", "loud"], CHIME) in (0, None)

    assert capsys.readouterr().out.strip() == "HEY"
    assert seen == started  # the command ran under the invocation's one run
    # whose pick it is, so it ends with the run
    assert started[0].profiles == ("loud",)
    assert flag_pick() == ()


def test_profiles_repeated_among_a_declared_command_s_options_stack_in_order(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolated_config.write_text(
        "format_version: 2\nprofiles:\n"
        "  a:\n    chime:\n      greeting: A\n"
        "  b:\n    chime:\n      greeting: B\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    argv = ["chime", "ring", "--profile", "a", "-v", "-v", "--profile", "b"]
    assert _run_root(argv, CHIME) in (0, None)

    assert capsys.readouterr().out.strip() == "B"  # b layered over a, as `--profile a,b`


@pytest.mark.usefixtures("_isolated_config")
def test_the_root_reads_the_line_by_the_declaration_kept_from_the_last_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    #: Each pass's declaration, and the line as the pass left it.
    lines: list[tuple[Declaration | None, list[str], bool]] = []
    read = bootstrap.applied_root_options

    def reading(
        run: Run,
        tokens: Sequence[str],
        options: dict[str, Any],
        declaration: Declaration | None = None,
    ) -> RootLine:
        line = read(run, tokens, options, declaration)
        lines.append((declaration, list(line.tokens), line.placed))
        return line

    monkeypatch.setattr(bootstrap, "applied_root_options", reading)

    assert _run_root(["plugin", "list", "-q"]) in (0, None)  # nothing kept yet
    assert _run_root(["plugin", "list", "-q"]) in (0, None)

    (cold, *first), (kept, *second) = lines
    assert cold is None
    assert first == [["plugin", "list", "-q"], False]  # dispatch reads the line, -q with it
    assert kept is not None
    assert second == [["plugin", "list"], True]


@pytest.mark.usefixtures("_isolated_config")
def test_a_root_called_inside_a_command_starts_its_own_run_and_hands_back_the_outer(
    started: list[Run], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run_root(["chime", "nest"], NESTED) in (0, None)

    outer, inner = started
    assert seen == [outer, inner, outer]  # nest, the inner ring, nest again after it
    assert capsys.readouterr().out.strip() == "hello"


@pytest.mark.usefixtures("_isolated_config")
def test_the_root_warns_once_about_a_quarantined_plugin_and_plugin_check_never(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = bootstrap.build_root_app(candidates=[CHIME, BROKEN])
    assert "quarantined" not in capsys.readouterr().err  # composing is not the root's run

    result = CliInvoker().invoke(root.meta, ["plugin", "check", "chime"])

    assert result.stderr.count("quarantined") == 1
    assert "broken" in result.stderr


def test_the_surface_walk_never_warns_about_a_quarantined_plugin(
    capsys: pytest.CaptureFixture[str],
) -> None:

    with pytest.raises(RuntimeError, match="broken"):
        generate([CHIME, BROKEN])

    assert "quarantined" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--version"], ["--help"]])
def test_root_flags_print_when_the_home_does_not_resolve(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "relative/home")

    assert _run_root(argv, CHIME) in (0, None)
    assert capsys.readouterr().out


@pytest.mark.parametrize("argv", [["config", "--help"], ["chime", "--help"]])
def test_a_command_that_never_reads_the_home_runs_when_it_does_not_resolve(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "relative/home")

    assert _run_root(argv, CHIME) in (0, None)
    assert "UNTAPED_HOME" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["chime", "ring"], ["plugin", "list"]])
def test_a_home_that_does_not_resolve_is_reported_through_the_run_s_sink(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "relative/home")

    assert _run_root(argv, CHIME) == 4
    assert "error: UNTAPED_HOME must be an absolute path" in capsys.readouterr().err
