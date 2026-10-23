"""The run: what a block reaches per invocation, and the handles built from it."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from untaped.sdk import (
    Command,
    InstalledPlugin,
    Service,
    System,
    UntapedError,
    attribution,
    plugin_of,
    settings_of,
)
from untaped.sdk.run import (
    Clock,
    Composition,
    NoRunError,
    Run,
    context_for,
    current_run,
    run_scope,
)

PACKAGE = __name__.partition(".")[0]


class HelloSettings(BaseModel):
    greeting: str = Field("hello", description="What say prints.")


class LintSettings(BaseModel):
    strict: bool = False


class Say(Command[HelloSettings]):
    """Say hello."""

    def run(self) -> str:
        return f"{self.settings.greeting} from {self.ctx.plugin.name}"


class Version(Command):
    """Print the version."""

    def run(self) -> str:
        return "1"


class HelloError(UntapedError):
    system = Service(HelloSettings)


class Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, text: object, **_: object) -> None:
        self.lines.append(str(text))


def _run(tmp_path: Path, sections: dict[str, BaseModel] | None = None) -> Run:
    picked = sections or {}

    def picks(section: str, model: type[BaseModel]) -> BaseModel:
        return picked.get(section) or model()

    return Run(
        home=tmp_path,
        composition=Composition(
            owners={HelloSettings: "hello", LintSettings: "lint"}, packages={PACKAGE: "hello"}
        ),
        picks=picks,
        diag=Recorder(),  # type: ignore[arg-type]
        terminal=lambda: pytest.fail("no terminal in this test"),
    )


@pytest.fixture
def run(tmp_path: Path) -> Iterator[Run]:
    with run_scope(_run(tmp_path, {"hello": HelloSettings(greeting="hi")})) as active:
        yield active


def test_outside_a_run_the_handles_say_so() -> None:
    with pytest.raises(NoRunError, match="no untaped run is active"):
        current_run()
    with pytest.raises(NoRunError):
        settings_of(HelloSettings)


def test_run_scope_restores_the_run_before(tmp_path: Path) -> None:
    outer, inner = _run(tmp_path), _run(tmp_path / "inner")
    with run_scope(outer):
        with run_scope(inner):
            assert current_run() is inner
        assert current_run() is outer


def test_a_command_reads_its_settings_and_its_plugin(run: Run) -> None:
    assert Say().run() == "hi from hello"


def test_a_command_without_settings_says_so(run: Run) -> None:
    with pytest.raises(TypeError, match="Version reads no settings"):
        _ = Version().settings


def test_settings_of_reads_another_plugins_section(run: Run) -> None:
    assert settings_of(LintSettings) == LintSettings()


def test_a_model_no_plugin_declared_is_a_lookup_error(run: Run) -> None:
    class Stray(BaseModel):
        pass

    with pytest.raises(LookupError, match="Stray is the settings model of no composed plugin"):
        settings_of(Stray)


def test_plugin_of_is_the_plugin_named_by_its_model(run: Run, tmp_path: Path) -> None:
    lint = plugin_of(LintSettings)
    assert lint.name == "lint"
    assert lint.dir == tmp_path / "plugins" / "lint"
    assert lint.committed("lint.yml", root=tmp_path) == tmp_path / ".untaped" / "lint.yml"
    assert lint.committed("lint/house.py") == Path(".untaped/lint/house.py")
    with pytest.raises(ValueError, match="not plugin lint's"):
        lint.committed("awx.yml")


def test_the_context_is_the_run_seen_by_the_commands_plugin(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ctx = context_for(Say)
    assert ctx.plugin == plugin_of(HelloSettings)
    assert ctx.project is None
    ctx.diag.info("hello")
    assert run.diag.lines == ["hello"]  # type: ignore[attr-defined]


def test_a_module_no_plugin_owns_is_a_lookup_error(run: Run) -> None:
    with pytest.raises(LookupError, match="no composed plugin owns module elsewhere"):
        run.composition.owner_of_module("elsewhere.commands")
    stray = type("Stray", (Version,), {"__module__": "elsewhere.commands"})
    with pytest.raises(LookupError, match="Stray belongs to no composed plugin"):
        _ = stray().ctx


def test_a_subclass_reads_its_bases_settings(run: Run) -> None:
    class Shout(Say):
        """Shout hello."""

    assert Shout().settings == HelloSettings(greeting="hi")


def test_a_run_that_picks_the_wrong_model_is_a_bug(tmp_path: Path) -> None:
    broken = _run(tmp_path, {"hello": LintSettings()})
    with run_scope(broken), pytest.raises(TypeError, match="picked LintSettings"):
        settings_of(HelloSettings)


def test_a_service_names_its_plugin_in_the_run(run: Run) -> None:
    assert HelloError.system == "hello"
    assert HelloError("boom").system == "hello"
    assert HelloError("boom", system=System.LOCAL).system == "local"
    assert UntapedError("boom", system=Service(LintSettings)).system == "lint"
    assert repr(HelloError.__dict__["system"]) == "Service(HelloSettings)"


def test_an_error_keeps_its_service_after_the_run(tmp_path: Path) -> None:
    with run_scope(_run(tmp_path)):
        error = HelloError("boom")
    assert error.system == "hello"
    assert attribution(error)["system"] == "hello"


def test_a_service_never_fails_a_read() -> None:
    class Stray(BaseModel):
        pass

    assert hasattr(HelloError, "system")
    assert HelloError.system == "Service(HelloSettings)"
    assert HelloError("boom").system == "Service(HelloSettings)"
    assert Service(Stray).name().endswith(".Stray)")


def test_system_is_a_closed_vocabulary_of_strings() -> None:
    assert [str(member) for member in System] == ["untaped", "local", "git", "http"]
    assert UntapedError("x").system == "untaped"


def test_the_clock_tells_wall_and_monotonic_time() -> None:
    clock = Clock()
    assert clock.now().tzinfo is not None
    before = clock.monotonic()
    clock.sleep(0)
    assert clock.monotonic() >= before


def test_ctx_plugins_is_the_installs_rows(tmp_path: Path) -> None:
    row = InstalledPlugin(name="hello", status="ready", distribution="untaped-hello", version="1.0")
    base = _run(tmp_path)
    composition = replace(base.composition, installed=lambda: (row,))
    with run_scope(replace(base, composition=composition)):
        assert Version().ctx.plugins == (row,)
    with run_scope(base):
        assert Version().ctx.plugins == ()
