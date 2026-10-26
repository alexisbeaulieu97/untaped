"""A block takes its name from the plugin class attribute holding it, its plugin from the run."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import Plugin
from untaped.sdk._block import Binding, Block, OwnedBlock, UnboundBlockError
from untaped.sdk._run import NoRunError, run_scope


class Thing(Block):
    block_kind = "thing"


class Settings(BaseModel):
    pass


def test_a_block_records_the_module_that_created_it() -> None:
    assert Thing().module == __name__


def test_an_unbound_block_names_itself_and_its_module() -> None:
    thing = Thing()
    with pytest.raises(UnboundBlockError, match=f"Thing created in {__name__}"):
        _ = thing.binding


def test_a_module_constant_binds_when_the_plugin_class_holds_it() -> None:
    THING = Thing()

    class Demo(Plugin):
        """Demo."""

        settings = Settings
        thing = THING

    assert THING.binding == Binding(Demo, "thing")


def test_a_class_that_is_not_a_plugin_binds_nothing() -> None:
    thing = Thing()

    class Other:
        held = thing

    assert thing.bindings == ()


def test_every_binding_is_kept_for_composition_to_judge() -> None:
    thing = Thing()

    class One(Plugin):
        """One."""

        settings = Settings
        a = thing
        b = thing

    assert [binding.attribute for binding in thing.bindings] == ["a", "b"]


def test_blocks_hash_by_identity() -> None:
    first, second = Thing(), Thing()
    assert first != second
    assert len({first, second}) == 2


class Owned(OwnedBlock):
    block_kind = "owned"
    name_style = "kebab"


def test_an_owned_block_is_named_by_its_attribute_and_the_composed_plugin(
    tmp_path: Path,
) -> None:
    class Demo(Plugin):
        """Demo."""

        settings = Settings
        repo_store = Owned()

    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")):
        assert (Demo.repo_store.plugin, Demo.repo_store.name) == ("demo", "repo-store")
    with (
        run_scope(run_owning(tmp_path, "elsewhere", "demo")),
        pytest.raises(LookupError, match=r"Demo\.repo_store belongs to no composed plugin"),
    ):
        _ = Demo.repo_store.plugin


def test_an_owned_block_reads_its_plugin_in_a_run() -> None:
    class Demo(Plugin):
        """Demo."""

        settings = Settings
        owned = Owned()

    assert Demo.owned.name == "owned"
    with pytest.raises(NoRunError):
        _ = Demo.owned.plugin
    with pytest.raises(UnboundBlockError):
        _ = Owned().name


def test_the_11x_spelling_names_the_block_itself() -> None:
    owned = Owned("demo", "store")
    assert (owned.plugin, owned.name, owned.spelled) == ("demo", "store", True)
    assert not Owned().spelled
    with pytest.raises(TypeError, match="its plugin and its name together"):
        Owned("demo")
