"""A plugin class's cache: named by its attribute; its plugin from the run, or bound."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.runtime.composition import PluginEntry, compose
from untaped.sdk import Cache, ConfigError, Plugin
from untaped.sdk._run import Run, run_scope
from untaped.sdk.storage.store import Store


class DemoSettings(BaseModel):
    pass


class Demo(Plugin):
    """Demo."""

    settings = DemoSettings
    motd = Cache(label="message of the day", max_age=timedelta(hours=1))


@pytest.fixture
def run(tmp_path: Path) -> Iterator[Run]:
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")) as active:
        yield active


@pytest.mark.usefixtures("run")
def test_a_plugin_classs_cache_takes_its_attribute_and_plugin() -> None:
    assert Demo.motd.qualified == "demo.motd"
    assert Demo.motd.setting == "demo.caches.motd.max_age_seconds"


def test_a_bound_cache_is_itself_and_says_so() -> None:
    twin = Cache(label="message of the day", max_age=timedelta(hours=1))
    assert twin != Demo.motd
    assert len({twin, Demo.motd}) == 2
    assert repr(twin) == "Cache(label='message of the day')"
    assert Cache("demo", "motd", label="x", max_age=timedelta(0)) == Cache(
        "demo", "motd", label="x", max_age=timedelta(0)
    )


def test_a_bound_cache_is_checked_without_its_name() -> None:
    with pytest.raises(ConfigError, match=r"cache Cache created in .*: max_age must be whole"):
        Cache(label="x", max_age=timedelta(milliseconds=5))
    with pytest.raises(ConfigError, match="store goes with the plugin and name"):
        Cache(
            label="x", max_age=timedelta(0), store=Store("demo", "index", base=lambda: Path("i.db"))
        )


def test_a_cache_bound_where_it_is_declared_needs_no_plugin_class() -> None:
    bound = Cache(label="x", max_age=timedelta(hours=1))._bound_to("acme", "motd")
    assert (bound.plugin, bound.name, bound.qualified) == ("acme", "motd", "acme.motd")


def test_composition_takes_a_bound_cache_held_as_it_is_bound() -> None:
    bound = Cache(label="x", max_age=timedelta(hours=1))._bound_to("demo", "motd")
    holder = type(
        "Bound", (Plugin,), {"__doc__": "Bound.", "settings": DemoSettings, "motd": bound}
    )
    declaration = compose([PluginEntry("demo", f"{__name__}:Bound", holder)])
    assert declaration.quarantined == ()


@pytest.mark.parametrize(("plugin", "attribute"), [("demo", "other"), ("acme", "motd")])
def test_composition_refuses_a_holder_a_bound_cache_disagrees_with(
    plugin: str, attribute: str
) -> None:
    bound = Cache(label="x", max_age=timedelta(hours=1))._bound_to(plugin, attribute)
    holder = type(
        "Bound", (Plugin,), {"__doc__": "Bound.", "settings": DemoSettings, "motd": bound}
    )
    declaration = compose([PluginEntry("demo", f"{__name__}:Bound", holder)])
    [quarantined] = declaration.quarantined
    assert [p.rule for p in quarantined.problems] == ["block-name"]
    assert (
        f"is bound to {plugin}.{attribute} where it is declared" in quarantined.problems[0].detail
    )
