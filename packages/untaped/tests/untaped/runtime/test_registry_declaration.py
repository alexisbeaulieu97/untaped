"""Core's declaration cache: a second start reads the 12.0 plugin classes' declaration, unimported.

Each test writes a small plugin package under ``tmp_path`` and names it by an
entry point's target, as an installed plugin is, so the test can tell from
``sys.modules`` whether a composition imported it.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from core_test_support.commands.support import make_plugin
from untaped.runtime.bootstrap import build_root_app, composed_with
from untaped.runtime.registry import (
    CompositionResult,
    PluginCandidate,
    named_plugin_dir,
    section_settings,
)
from untaped.sdk._home import home
from untaped.sdk.testing import CliInvoker

PLUGIN = '''
from datetime import timedelta

from pydantic import BaseModel, Field

from untaped.sdk import Cache, Command, Plugin

from {package}.settings import ToySettings

MOTD = Cache(label="toy motd", max_age=timedelta(hours=2))


class Greet(Command[ToySettings]):
    """Print the greeting."""

    def run(self) -> str:
        return self.settings.greeting


class Toy(Plugin):
    """A toy plugin, composed from its target."""

    settings = ToySettings
    commands = (Greet,)
    motd = MOTD
'''

SETTINGS = """
from pydantic import BaseModel, Field


class ToySettings(BaseModel):
    greeting: str = Field("hello from toy", description="What greet prints.")
"""

BROKEN = '''
from untaped.sdk import Command, Plugin

from {package}.settings import ToySettings


class Silent(Command[ToySettings]):
    def run(self) -> None:
        return None


class Toy(Plugin):
    """A plugin whose command has no docstring."""

    settings = ToySettings
    commands = (Silent,)
'''


@pytest.fixture
def toys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_composition: None
) -> Iterator[Path]:
    """A folder on ``sys.path`` for the toy packages; each is unloaded after the test."""
    root = tmp_path / "toys"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    before = set(sys.modules)
    yield root
    for name in set(sys.modules) - before:
        if name.startswith("toyplug"):
            del sys.modules[name]


def _package(root: Path, package: str, plugin: str = PLUGIN) -> PluginCandidate:
    folder = root / package
    folder.mkdir()
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (folder / "plugin.py").write_text(
        textwrap.dedent(plugin.format(package=package)), encoding="utf-8"
    )
    return PluginCandidate(
        distribution=f"untaped-{package}", name="toy", target=f"{package}.plugin:Toy"
    )


def compose_root(*, candidates: list[PluginCandidate]) -> CompositionResult:
    """One start's composition, its settings registrations undone afterwards."""
    with composed_with(candidates) as result:
        return result


def _unload(package: str) -> None:
    for name in [each for each in sys.modules if each.startswith(package)]:
        del sys.modules[name]


def _declarations() -> list[Path]:
    folder = named_plugin_dir("untaped") / "cached" / "declaration"
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def test_cores_declaration_is_bound_where_it_is_declared() -> None:
    from untaped.core.plugin import Core
    from untaped.core.settings import DECLARATION

    assert Core.declaration is DECLARATION
    assert (DECLARATION.plugin, DECLARATION.name) == ("untaped", "declaration")
    # read with no run, in the folder it always had
    assert DECLARATION._folder() == home().expanduser() / "plugins/untaped/cached/declaration"


def test_a_second_start_composes_the_plugin_without_importing_it(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    first = compose_root(candidates=[toy])
    assert [plugin.name for plugin in first.plugins] == ["untaped", "toy"]
    assert len(_declarations()) == 1

    _unload("toyplug")
    again = compose_root(candidates=[toy])
    assert [plugin.name for plugin in again.plugins] == ["untaped", "toy"]
    assert "toyplug.plugin" not in sys.modules
    toy_plugin = again.plugins[1]
    assert toy_plugin.decl.summary == "A toy plugin, composed from its target."
    # the settings model is read from its own module, the cache's setting from the declaration
    assert "toyplug.settings" in sys.modules
    assert "toyplug.plugin" not in sys.modules
    assert [cache.name for cache in toy_plugin.caches] == [
        "motd"
    ]  # reading a block imports the class
    assert "toyplug.plugin" in sys.modules


KEYED = '''
from untaped.sdk import Command, Credential, CredentialKey, Plugin

from {package}.settings import ToySettings


class ToyServer(CredentialKey):
    """The toy server."""


TOKEN = Credential(key=ToyServer)


class Greet(Command[ToySettings]):
    """Print the greeting."""

    def run(self) -> str:
        return self.settings.greeting


class Toy(Plugin):
    """A toy plugin holding a credential."""

    settings = ToySettings
    commands = (Greet,)
    token = TOKEN
'''


def test_a_second_start_reads_a_plugins_credentials_when_first_asked(toys: Path) -> None:
    """The section knows the credential's name from the declaration, so the class waits."""
    from untaped.runtime.settings import _CREDENTIAL_NAMES
    from untaped.sdk.network.credentials import declared_credentials

    toy = _package(toys, "toyplug", KEYED)
    compose_root(candidates=[toy])
    _unload("toyplug")

    with composed_with([toy]) as again:
        assert "toyplug.plugin" not in sys.modules
        section = section_settings(again.plugins[1])
        assert section is not None
        assert _CREDENTIAL_NAMES[section] == {"token"}
        assert "toyplug.plugin" not in sys.modules
        assert [each.name for each in declared_credentials("toy")] == ["token"]
        assert "toyplug.plugin" in sys.modules


def test_a_replayed_plugin_has_its_setting_ids_and_runs(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    compose_root(candidates=[toy])
    _unload("toyplug")

    root = build_root_app(candidates=[toy])
    assert "toyplug.plugin" not in sys.modules
    invoke = CliInvoker()
    listed = invoke.invoke(root.meta, ["config", "list", "--format", "json"])
    keys = {row["key"]: row for row in json.loads(listed.stdout)}
    assert keys["toy.caches.motd.max_age_seconds"]["value"] == 7200
    assert "toyplug.plugin" not in sys.modules
    result = compose_root(candidates=[toy])
    assert result.plugins[1].block_ids("caches") == ("toy.motd",)

    greeted = invoke.invoke(root.meta, ["toy", "greet"])
    assert (greeted.exit_code, greeted.stdout) == (0, "hello from toy\n")


def test_a_refused_plugin_is_quarantined_again_without_importing_it(toys: Path) -> None:
    toy = _package(toys, "toyplug", BROKEN)
    first = compose_root(candidates=[toy])
    _unload("toyplug")
    again = compose_root(candidates=[toy])
    assert "toyplug.plugin" not in sys.modules
    assert again.quarantine == first.quarantine
    [record] = again.quarantine
    assert record.reason == "bad-plugin-class" and "Silent" in record.detail


@pytest.mark.parametrize("name", ["plugin", "config"])
def test_a_plugin_named_after_a_root_command_stays_refused_without_importing_it(
    toys: Path, name: str
) -> None:
    taken = PluginCandidate(distribution="untaped-toyplug", name=name, target="toyplug.plugin:Toy")
    _package(toys, "toyplug")
    first = compose_root(candidates=[taken])
    _unload("toyplug")
    again = compose_root(candidates=[taken])
    assert "toyplug.plugin" not in sys.modules
    assert again.quarantine == first.quarantine
    [record] = again.quarantine
    assert (record.reason, record.detail) == (
        "duplicate-name",
        f"duplicate plugin name: {name!r} is taken by a root command",
    )


def test_a_plugin_named_after_core_leaves_core_mounted(toys: Path) -> None:
    _package(toys, "toyplug")
    named = PluginCandidate(
        distribution="untaped-toyplug", name="untaped", target="toyplug.plugin:Toy"
    )
    result = compose_root(candidates=[named])
    assert [record.reason for record in result.quarantine] == ["duplicate-name"]
    assert [plugin.name for plugin in result.mount.declaration.plugins] == ["untaped"]
    root = build_root_app(candidates=[named])
    assert CliInvoker().invoke(root.meta, ["plugin", "list"]).exit_code == 0


def test_a_class_refused_before_the_mount_leaves_the_one_holding_its_name(toys: Path) -> None:
    good = _package(toys, "toyplug")
    _package(toys, "toyplug2")
    blank = PluginCandidate(distribution=" ", name="toy", target="toyplug2.plugin:Toy")
    first = compose_root(candidates=[good, blank])
    assert [plugin.name for plugin in first.plugins] == ["untaped", "toy"]
    _unload("toyplug")
    again = compose_root(candidates=[good, blank])
    assert "toyplug.plugin" not in sys.modules  # the kept declaration still has it
    assert [plugin.name for plugin in again.plugins] == ["untaped", "toy"]


def test_a_contested_name_stays_contested_on_a_second_start(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    other = _package(toys, "toyother")  # another distribution's plugin named toy
    first = compose_root(candidates=[toy, other])
    _unload("toyplug")
    again = compose_root(candidates=[toy, other])
    names = [[plugin.name for plugin in each.plugins] for each in (first, again)]
    assert names == [["untaped"], ["untaped"]]
    assert [record.reason for record in again.quarantine] == ["duplicate-name"] * 2
    assert "toyplug.plugin" not in sys.modules


def test_another_set_of_candidates_composes_and_keeps_its_own_declaration(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    compose_root(candidates=[toy])
    other = _package(toys, "toyplugtwo")
    renamed = PluginCandidate(distribution=other.distribution, name="two", target=other.target)
    result = compose_root(candidates=[toy, renamed])
    assert sorted(plugin.name for plugin in result.plugins) == ["toy", "two", "untaped"]
    assert len(_declarations()) == 2


def test_a_home_it_cannot_write_composes_every_time(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    named_plugin_dir("untaped").parent.mkdir(parents=True, exist_ok=True)
    named_plugin_dir("untaped").write_text("not a folder", encoding="utf-8")
    assert [p.name for p in compose_root(candidates=[toy]).plugins] == ["untaped", "toy"]
    _unload("toyplug")
    assert [p.name for p in compose_root(candidates=[toy]).plugins] == ["untaped", "toy"]
    assert "toyplug.plugin" in sys.modules


def test_a_candidate_given_as_an_object_composes_without_the_cache() -> None:
    compose_root(
        candidates=[PluginCandidate(distribution="d", name="given", target=make_plugin("given"))]
    )
    assert _declarations() == []


def test_a_declaration_that_misses_a_plugin_class_is_composed_again(toys: Path) -> None:
    """A target that was no plugin class when the declaration was kept and is one now."""
    folder = toys / "toyplug"
    toy = _package(toys, "toyplug")
    (folder / "plugin.py").write_text(
        "Toy = object()\n",
        encoding="utf-8",
    )
    spec_only = compose_root(candidates=[toy]).mount
    assert spec_only is not None
    assert [plugin.name for plugin in spec_only.declaration.plugins] == ["untaped"]  # core's own
    _unload("toyplug")
    (folder / "plugin.py").write_text(
        textwrap.dedent(PLUGIN.format(package="toyplug")), encoding="utf-8"
    )
    result = compose_root(candidates=[toy])
    assert [plugin.name for plugin in result.mount.declaration.plugins] == ["untaped", "toy"]
    _unload("toyplug")
    compose_root(candidates=[toy])
    assert "toyplug.plugin" not in sys.modules  # the declaration composed afresh was kept


def test_a_kept_plugin_whose_settings_no_longer_import_is_quarantined_as_afresh(
    toys: Path,
) -> None:
    toy = _package(toys, "toyplug")
    compose_root(candidates=[toy])
    _unload("toyplug")
    (toys / "toyplug" / "settings.py").write_text(
        "raise ImportError('a dependency is gone')\n", encoding="utf-8"
    )
    again = compose_root(candidates=[toy])
    assert [plugin.name for plugin in again.plugins] == ["untaped"]
    [record] = again.quarantine
    assert record.reason == "malformed-entry-point" and "a dependency is gone" in record.detail


def test_a_candidate_whose_metadata_changed_is_checked_again(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    compose_root(candidates=[toy])
    _unload("toyplug")
    pinned = PluginCandidate(
        distribution=toy.distribution,
        name=toy.name,
        target=toy.target,
        requires_dist=("untaped<1",),
    )
    again = compose_root(candidates=[pinned])
    assert [plugin.name for plugin in again.plugins] == ["untaped"]
    assert [record.reason for record in again.quarantine] == ["bad-metadata"]
    assert len(_declarations()) == 1  # the same key, composed again


def test_doctor_counts_core_s_own_cache_as_installed(toys: Path) -> None:
    toy = _package(toys, "toyplug")
    root = build_root_app(candidates=[toy])
    assert _declarations()
    doctor = CliInvoker().invoke(root.meta, ["doctor", "--format", "json"])
    [caches] = [row for row in json.loads(doctor.stdout) if row["check"] == "caches"]
    assert "of a plugin not installed" not in caches["detail"]


OWNER_API = '''
from untaped.sdk import experimental
from untaped.sdk.contracts import Contract


@experimental
class Titles(Contract):
    """Every title a plugin holds."""

    def titles(self) -> list[str]:
        raise NotImplementedError
'''

OWNER = '''
from untaped.sdk import Plugin

from {package}.api import Titles
from {package}.settings import ToySettings


class Toy(Plugin):
    """Own the titles."""

    settings = ToySettings
    offers = (Titles,)
'''

FILLER = '''
from untaped.sdk import Plugin
from untaped.sdk.contracts import Provider

from toyplugowner.api import Titles
from {package}.settings import ToySettings


class Shop(Titles, Provider):
    def titles(self) -> list[str]:
        return ["Dune"]


class Toy(Plugin):
    """Fill the titles."""

    settings = ToySettings
    fills = (Shop,)
'''


def test_a_second_start_routes_a_fill_to_its_owner_and_imports_the_owner_class_never(
    toys: Path,
) -> None:
    from untaped.runtime import registry

    owner = _package(toys, "toyplugowner", OWNER)
    (toys / "toyplugowner" / "api.py").write_text(OWNER_API, encoding="utf-8")
    filler = _package(toys, "toyplugfiller", FILLER)
    ordered = [
        PluginCandidate(distribution="untaped-owner", name="owner", target=owner.target),
        PluginCandidate(distribution="untaped-filler", name="filler", target=filler.target),
    ]
    fresh = registry._compose(ordered)
    _unload("toyplugowner")
    _unload("toyplugfiller")
    replayed = registry._compose(ordered, fresh.mount.declaration)
    specs = {each.name: each for each in replayed.plugins}
    assert specs["filler"].fill_owners == ("owner",)
    [titles] = specs["owner"].offers
    assert titles.__module__ == "toyplugowner.api"
    assert "toyplugowner.plugin" not in sys.modules
