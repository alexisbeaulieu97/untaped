"""Blocks learn their name from the plugin class attribute holding them."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from untaped.sdk import Plugin
from untaped.sdk._block import Binding, Block, UnboundBlockError


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
