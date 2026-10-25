"""One run per invocation: started first by the root, with every root option applied."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from untaped.core.surface import generate
from untaped.runtime import bootstrap
from untaped.runtime import run as runtime_run
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.runtime.settings import get_settings
from untaped.sdk import Command, Plugin
from untaped.sdk._cli import create_app, echo
from untaped.sdk.run import Run, current_run

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
BROKEN = PluginCandidate(distribution="broken-dist", name="broken", target="no_such_mod:SPEC")


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[Run]:
    """Every run started or built during the test, in order."""
    seen.clear()
    runs: list[Run] = []

    def counting(make: Callable[..., Run]) -> Callable[..., Run]:
        def wrapped(*args: Any, **kwargs: Any) -> Run:
            runs.append(make(*args, **kwargs))
            return runs[-1]

        return wrapped

    monkeypatch.setattr(runtime_run, "start_run", counting(runtime_run.start_run))
    monkeypatch.setattr(runtime_run, "build_run", counting(runtime_run.build_run))
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
    assert seen == started  # the one run picked it from the start


@pytest.mark.usefixtures("_isolated_config")
def test_the_root_warns_once_about_a_quarantined_plugin_and_plugin_check_never(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_root(["plugin", "check", "chime"], CHIME, BROKEN)

    assert capsys.readouterr().err.count("quarantined") == 1


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


@pytest.mark.parametrize("argv", [["chime", "ring"], ["plugin", "list"]])
def test_a_home_that_does_not_resolve_is_reported_through_the_run_s_sink(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "relative/home")

    assert _run_root(argv, CHIME) == 4
    assert "error: UNTAPED_HOME must be an absolute path" in capsys.readouterr().err
