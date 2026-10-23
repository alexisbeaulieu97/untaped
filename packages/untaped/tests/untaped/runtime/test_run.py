"""The runtime builds a run from the declaration and today's settings, sink and terminal."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import Declaration
from untaped.runtime.run import build_run, composition_of
from untaped.sdk import Command, Plugin, plugin_of, settings_of
from untaped.sdk.run import run_scope

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


def _composed() -> tuple[Declaration, dict[str, type[Plugin]]]:
    plugins: dict[str, type[Plugin]] = {"hello": Hello}
    return compose([PluginEntry("hello", f"{__name__}:Hello", Hello)]), plugins


def test_the_composition_maps_settings_models_and_packages_to_plugins() -> None:
    declaration, plugins = _composed()
    composition = composition_of(declaration, plugins)
    assert composition.owners == {HelloSettings: "hello"}
    assert composition.packages == {PACKAGE: "hello"}


def test_a_built_run_reads_settings_from_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HELLO__GREETING", "hi")
    monkeypatch.chdir(tmp_path)
    declaration, plugins = _composed()
    run = build_run(declaration, plugins, home=tmp_path)
    with run_scope(run):
        assert settings_of(HelloSettings).greeting == "hi"
        assert settings_of(HelloSettings) is settings_of(HelloSettings)
        assert plugin_of(HelloSettings).dir == tmp_path / "plugins" / "hello"
        assert Version().ctx.plugin.name == "hello"
        assert Version().ctx.project is None
