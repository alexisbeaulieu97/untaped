"""A plugin class's cache: named by its attribute, its plugin read from the run."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import Cache, ConfigError, Plugin
from untaped.sdk.run import Composition, Run, run_scope
from untaped.sdk.storage.store import Store


class DemoSettings(BaseModel):
    pass


class Demo(Plugin):
    """Demo."""

    settings = DemoSettings
    motd = Cache(label="message of the day", max_age=timedelta(hours=1))


@pytest.fixture
def run(tmp_path: Path) -> Iterator[Run]:
    active = Run(
        home=tmp_path,
        composition=Composition(packages={__name__.partition(".")[0]: "demo"}),
        picks=lambda _section, model: model(),
        diag=None,  # type: ignore[arg-type]
        terminal=lambda: pytest.fail("no terminal in this test"),
    )
    with run_scope(active):
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
