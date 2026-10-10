"""A toy owner (``rack``) and provider (``bin``) installed as real packages for a test.

``rack`` declares ``ItemSource`` in ``untaped_rack.api`` with a conformance
check in ``untaped_rack.testing``; ``bin`` fills it with its own ``Box``
records through the bridge. The ``contract_plugins`` fixture (conftest)
writes both on ``sys.path``.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from untaped.plugins.registry import PluginCandidate

PACKAGES = ("untaped_rack", "untaped_bin")

RACK = {
    "untaped_rack/__init__.py": '''
        """An owner: where items come from."""

        from untaped.sdk import PluginSpec


        def _contracts():
            from untaped_rack.api import ItemSource

            return (ItemSource,)


        SPEC = PluginSpec(name="rack", contracts=_contracts)
        ''',
    "untaped_rack/errors.py": '"""Errors."""\n',
    "untaped_rack/api.py": '''
        """The rack contract."""

        from abc import abstractmethod

        from untaped.contracts import Contract, Issued, Record, bridge


        class Item(Issued, kind="rack.item"):
            name: str


        class ItemSource[T: Record = Item](Contract):
            """Where items come from."""

            @bridge
            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self) -> list[Item]:
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
    "untaped_bin/__init__.py": '''
        """A provider for rack."""

        from untaped.sdk import PluginSpec


        def _rack():
            from untaped_bin.adapters.rack import BinSource

            return (BinSource(),)


        SPEC = PluginSpec(name="bin", provides={"rack": _rack})
        ''',
    "untaped_bin/errors.py": '"""Errors."""\n',
    "untaped_bin/adapters/__init__.py": "",
    "untaped_bin/adapters/rack.py": '''
        """Bins as rack items."""

        from typing import ClassVar

        from untaped.contracts import Record
        from untaped_rack.api import Item, ItemSource


        class Box(Record, kind="bin.box"):
            id: int
            label: str


        class BinSource(ItemSource[Box]):
            boxes: ClassVar[list[Box]] = [Box(id=1, label="tools"), Box(id=2, label="toys")]
            nonconforming: ClassVar[bool] = False
            broken: ClassVar[bool] = False

            def to_item(self, item: Box) -> Item:
                return Item(name=item.label)

            def items(self) -> list[Item]:
                if type(self).broken:
                    raise RuntimeError("the bin is locked")
                return [self.to_item(box) for box in type(self).boxes]
        ''',
}

CORE_RANGE = "untaped>=10,<11"


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
    requires = bin_requires or (CORE_RANGE, "untaped-rack>=1,<2; extra == 'rack'")
    found = [
        PluginCandidate(
            distribution="untaped-bin",
            name="bin",
            target="untaped_bin:SPEC",
            distribution_version="1.0",
            requires_dist=requires,
        )
    ]
    if rack:
        found.append(
            PluginCandidate(
                distribution="untaped-rack",
                name="rack",
                target="untaped_rack:SPEC",
                distribution_version="1.4",
                requires_dist=(CORE_RANGE,),
            )
        )
    return found
