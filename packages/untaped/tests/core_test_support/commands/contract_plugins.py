"""A toy owner (``rack``) and provider (``bin``) installed as real packages for a test.

``rack`` declares ``ItemSource`` in ``untaped_rack.api`` with a conformance
check in ``untaped_rack.testing``; ``bin`` fills it with its own ``Box``
records through the bridge. The ``contract_plugins_site`` fixture
(``tests/untaped/core/conftest.py``) writes both on ``sys.path``.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from textwrap import dedent

from untaped.core.commands.plugin_new import _range
from untaped.runtime.registry import PluginCandidate

PACKAGES = ("untaped_rack", "untaped_bin")

RACK = {
    "untaped_rack/__init__.py": '"""An owner: where items come from."""\n',
    "untaped_rack/plugin.py": '''
        """The ``rack`` plugin class."""

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Plugin
        from untaped_rack.api import ItemSource


        class RackSettings(BaseModel):
            """Nothing to configure."""

            model_config = ConfigDict(frozen=True)


        class Rack(Plugin):
            """Where items come from."""

            settings = RackSettings
            offers = (ItemSource,)
        ''',
    "untaped_rack/errors.py": '"""Errors."""\n',
    "untaped_rack/api.py": '''
        """What a provider may import from rack."""

        from untaped_rack._contract import Item, ItemSource

        __all__ = ["Item", "ItemSource"]
        ''',
    "untaped_rack/_contract.py": '''
        """The rack contract, defined outside the api module that exports it."""

        from abc import abstractmethod
        from datetime import date

        from untaped.sdk.contracts import Contract, Issued, Record


        class Item(Issued, kind="rack.item"):
            name: str


        class ItemSource[T: Record = Item](Contract):
            """Where items come from."""

            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self, since: date | None = None) -> list[Item]:
                """Every item on the rack."""

            def named(self, name: str, *, limit: int = 10) -> list[Item]:
                """The items called ``name``."""
                raise NotImplementedError
        ''',
    "untaped_rack/testing.py": '''
        """The rack's conformance checks."""


        def conformance(provider):
            if getattr(type(provider), "nonconforming", False):
                raise AssertionError("items must be sorted")
        ''',
}

BIN = {
    "untaped_bin/__init__.py": '"""A provider for rack."""\n',
    "untaped_bin/settings.py": '''
        """The bin settings."""

        from pydantic import BaseModel, ConfigDict


        class BinSettings(BaseModel):
            """Where the boxes are."""

            model_config = ConfigDict(frozen=True)

            bins: list[str] = []
        ''',
    "untaped_bin/plugin.py": '''
        """The ``bin`` plugin class."""

        from untaped.sdk import Plugin
        from untaped_bin.providers.rack import BinSource
        from untaped_bin.settings import BinSettings


        class Bin(Plugin):
            """A provider for rack."""

            settings = BinSettings
            fills = (BinSource,)
        ''',
    "untaped_bin/errors.py": '"""Errors."""\n',
    "untaped_bin/providers/__init__.py": "",
    "untaped_bin/providers/rack.py": '''
        """Bins as rack items."""

        from typing import ClassVar

        from untaped.sdk import key
        from untaped.sdk.contracts import NotReady, Provider, Record
        from untaped_bin.settings import BinSettings
        from untaped_rack.api import Item, ItemSource


        class Box(Record, kind="bin.box"):
            id: int
            label: str


        class BinSource(ItemSource[Box], Provider[BinSettings]):
            boxes: ClassVar[list[Box]] = [Box(id=1, label="tools"), Box(id=2, label="toys")]
            nonconforming: ClassVar[bool] = False
            broken: ClassVar[bool] = False
            waiting: ClassVar[bool] = False

            def ready(self) -> NotReady | None:
                waiting = type(self).waiting
                return NotReady("no bins yet", key(BinSettings).bins) if waiting else None

            def to_item(self, item: Box) -> Item:
                return Item(name=item.label)

            def items(self) -> list[Item]:
                if type(self).broken:
                    raise RuntimeError("the bin is locked")
                return [self.to_item(box) for box in type(self).boxes]

            def named(self, name: str, *, limit: int = 10) -> list[Item]:
                return [item for item in self.items() if item.name == name][:limit]
        ''',
}

#: The running core's own range, so a fixture plugin is never quarantined after a major bump.
CORE_RANGE = f"untaped{_range(version('untaped'))}"


def write(site: Path) -> None:
    """Write both packages under ``site``."""
    for name, body in {**RACK, **BIN}.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")


def candidates(
    *, rack: bool = True, bin_requires: tuple[str, ...] | None = None
) -> list[PluginCandidate]:
    """Candidates for ``bin`` and, unless ``rack=False``, ``rack``, as discovery reads them."""
    requires = bin_requires or (CORE_RANGE, "untaped-rack>=1,<2")
    found = [
        PluginCandidate(
            distribution="untaped-bin",
            name="bin",
            target="untaped_bin.plugin:Bin",
            distribution_version="1.0",
            requires_dist=requires,
        )
    ]
    if rack:
        found.append(
            PluginCandidate(
                distribution="untaped-rack",
                name="rack",
                target="untaped_rack.plugin:Rack",
                distribution_version="1.4",
                requires_dist=(CORE_RANGE,),
            )
        )
    return found
