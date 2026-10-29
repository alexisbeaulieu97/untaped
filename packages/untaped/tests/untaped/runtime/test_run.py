"""The runtime builds a run from the declaration and today's settings, sink and terminal."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from untaped.runtime import bootstrap
from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import Declaration
from untaped.runtime.profile_resolver import write_target
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.run import build_run, child_run, composition_of
from untaped.runtime.settings import get_settings, register_profile_settings
from untaped.sdk import Command, Plugin, plugin_of, settings_of
from untaped.sdk._run import NoRunError, Rendering, SettingsOverlay, current_run, run_scope

PACKAGE = __name__.partition(".")[0]


class HelloSettings(BaseModel):
    greeting: str = Field("hello", description="What say prints.")


class Version(Command):
    """Print the version."""

    def run(self) -> str:
        return "1"


class Hello(Plugin):
    """Say hello."""

    settings = HelloSettings
    commands = (Version,)


def _composed() -> tuple[Declaration, dict[str, type[BaseModel]]]:
    """The declaration of ``Hello``, and its settings model by plugin name."""
    return compose([PluginEntry("hello", f"{__name__}:Hello", Hello)]), {"hello": HelloSettings}


def test_the_composition_maps_settings_models_and_packages_to_plugins() -> None:
    declaration, settings = _composed()
    composition = composition_of(declaration, settings)
    assert composition.owners == {HelloSettings: "hello"}
    assert composition.packages == {PACKAGE: "hello"}


def test_a_built_run_reads_settings_from_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HELLO__GREETING", "hi")
    monkeypatch.chdir(tmp_path)
    declaration, settings = _composed()
    run = build_run(declaration, settings, home=tmp_path)
    with run_scope(run):
        assert settings_of(HelloSettings).greeting == "hi"
        assert settings_of(HelloSettings) is settings_of(HelloSettings)
        assert plugin_of(HelloSettings).dir == tmp_path / "plugins" / "hello"
        assert Version().ctx.plugin.name == "hello"
        assert Version().ctx.project is None
        assert Version().ctx.terminal is Version().ctx.terminal


@pytest.mark.usefixtures("fresh_composition")
def test_the_installs_rows_are_read_once(tmp_path: Path) -> None:
    ghost = PluginCandidate(distribution="untaped-ghost", name="ghost", target="ghost:Plugin")
    bootstrap.compose_root(candidates=[ghost])
    declaration, settings = _composed()
    with run_scope(build_run(declaration, settings, home=tmp_path)):
        rows = Version().ctx.plugins
        assert [row.name for row in rows] == ["ghost", "untaped"]
        assert Version().ctx.plugins is rows


@pytest.mark.usefixtures("fresh_composition")
def test_each_run_reads_the_installs_rows_afresh(tmp_path: Path) -> None:
    declaration, settings = _composed()
    for name in ("ghost", "spook"):
        candidate = PluginCandidate(
            distribution=f"untaped-{name}", name=name, target=f"{name}:Plugin"
        )
        bootstrap.compose_root(candidates=[candidate])
        with run_scope(build_run(declaration, settings, home=tmp_path)):
            assert [row.name for row in Version().ctx.plugins] == [name, "untaped"]


def test_a_child_run_shares_the_parents_state_and_reads_under_its_own_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    (tmp_path / "config.yml").write_text(
        "format_version: 2\nprofiles:\n  work:\n    hello:\n      greeting: hi\n",
        encoding="utf-8",
    )
    declaration, settings = _composed()
    run = build_run(declaration, settings, home=tmp_path).render_with(Rendering(format="json"))
    with run_scope(run):
        assert settings_of(HelloSettings).greeting == "hello"  # the parent's, now read
        with child_run(profiles="work") as child:
            assert current_run() is child
            shared = ("composition", "rendering", "diag", "terminal", "clock", "notes")
            assert all(getattr(child, name) is getattr(run, name) for name in shared)
            assert child.home == run.home
            assert settings_of(HelloSettings).greeting == "hi"
            assert write_target() == "work"
            with child_run() as grandchild:
                assert grandchild.profiles == ("work",)
        assert current_run() is run
        assert settings_of(HelloSettings).greeting == "hello"
        assert write_target() == "default"


def test_a_run_reads_a_section_again_once_its_pick_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    (tmp_path / "config.yml").write_text(
        "format_version: 2\nprofiles:\n  work:\n    hello:\n      greeting: hi\n",
        encoding="utf-8",
    )
    declaration, settings = _composed()
    run = build_run(declaration, settings, home=tmp_path)
    with run_scope(run):
        assert settings_of(HelloSettings).greeting == "hello"
        run.set_profiles(("work",))  # a late --profile, as the 11.x seam's dispatch takes one
        assert settings_of(HelloSettings).greeting == "hi"
        assert write_target() == "work"


def test_a_child_runs_overlay_reaches_its_sections_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    declaration, settings = _composed()
    with run_scope(build_run(declaration, settings, home=tmp_path)):
        overlay = SettingsOverlay("default", "hello", {"greeting": "typed"})
        with child_run(overlay=overlay):
            assert settings_of(HelloSettings).greeting == "typed"
        assert settings_of(HelloSettings).greeting == "hello"


def test_a_child_run_needs_a_run() -> None:
    with pytest.raises(NoRunError), child_run(profiles="work"):
        pass


def test_a_child_run_refuses_an_empty_pick(tmp_path: Path) -> None:
    declaration, settings = _composed()
    with (
        run_scope(build_run(declaration, settings, home=tmp_path)),
        pytest.raises(ValueError, match="names a profile"),
        child_run(profiles=()),
    ):
        pass


@pytest.fixture
def picked_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_composition: None
) -> Iterator[None]:
    """A run over a home whose base and ``work`` and ``lab`` profiles greet differently."""
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    (tmp_path / "config.yml").write_text(
        "format_version: 2\nhello: {greeting: base}\n"
        "profiles:\n  work: {hello: {greeting: work}}\n  lab: {hello: {greeting: lab}}\n",
        encoding="utf-8",
    )
    register_profile_settings("hello", HelloSettings)
    declaration, settings = _composed()
    with run_scope(build_run(declaration, settings, home=tmp_path)):
        yield


def _read() -> tuple[str, str, str]:
    """What the current run reads: its section, the aggregate settings and its write target."""
    return (
        settings_of(HelloSettings).greeting,
        get_settings().hello.greeting,  # type: ignore[attr-defined]
        write_target(),
    )


@pytest.mark.usefixtures("picked_run")
def test_a_thread_started_in_a_child_run_reads_its_pick_after_the_block() -> None:
    go = threading.Event()
    seen: list[tuple[str, str, str]] = []

    def later() -> None:
        assert go.wait(timeout=10)
        seen.append(_read())

    with child_run(profiles="work"):
        thread = threading.Thread(target=contextvars.copy_context().run, args=(later,), daemon=True)
        thread.start()
    try:
        assert _read() == ("base", "base", "default")  # the parent fills its own entry
    finally:
        go.set()
        thread.join(timeout=10)
    assert seen == [("work", "work", "work")]


@pytest.mark.usefixtures("picked_run")
def test_async_tasks_in_separate_child_runs_each_read_their_own_pick() -> None:
    async def task(name: str) -> list[tuple[str, str, str]]:
        with child_run(profiles=name):
            first = _read()
            await asyncio.sleep(0)  # the other task enters its child and reads
            return [first, _read()]

    async def both() -> list[list[tuple[str, str, str]]]:
        return list(await asyncio.gather(task("work"), task("lab")))

    work, lab = asyncio.run(both())
    assert work == [("work", "work", "work")] * 2
    assert lab == [("lab", "lab", "lab")] * 2


@pytest.mark.usefixtures("picked_run")
def test_the_main_thread_reads_its_own_pick_while_a_worker_is_in_a_child_run() -> None:
    inside, read = threading.Event(), threading.Event()
    seen: list[tuple[str, str, str]] = []

    def worker() -> None:
        with child_run(profiles="work"):
            try:
                seen.append(_read())
            finally:
                inside.set()
            assert read.wait(timeout=10)
            seen.append(_read())

    thread = threading.Thread(target=contextvars.copy_context().run, args=(worker,), daemon=True)
    thread.start()
    try:
        assert inside.wait(timeout=10)
        main = _read()
    finally:
        read.set()
        thread.join(timeout=10)
    assert main == ("base", "base", "default")
    assert seen == [("work", "work", "work")] * 2
