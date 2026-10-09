"""A toy owner (``shelf``) and its providers (``library``, ``shop``, ``kiosk``)."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from untaped import bootstrap
from untaped.contracts import Configured, Contract, Issued, Record, bridge, cached
from untaped.contracts._registry import reset
from untaped.plugins.registry import CompositionResult, PluginSpec
from untaped.sdk import experimental
from untaped.settings import get_settings
from untaped.testing import provider_candidate


class Book(Issued, kind="shelf.book"):
    title: str
    pages: int = 0


@experimental
class BookSource[T: Record = Book](Contract):
    """Where books come from."""

    @bridge
    def to_book(self, item: T) -> Book:
        raise NotImplementedError

    @cached(ttl=timedelta(hours=1))
    @abstractmethod
    def books(self) -> list[Book]: ...

    def lookup(self, title: str) -> list[Book]:
        raise NotImplementedError


class Volume(Record, kind="library.volume"):
    id: int
    name: str


class SpecialVolume(Volume, kind="library.special_volume"):
    signed: bool = False


class LibrarySettings(BaseModel):
    catalog: str
    volumes: list[Volume] = []


class Library(BookSource[Volume], Configured[LibrarySettings]):
    calls: ClassVar[int] = 0
    error: ClassVar[Exception | None] = None

    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name, pages=item.id)

    def books(self) -> list[Book]:
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return [self.to_book(volume) for volume in self.settings.volumes]


class Shop(BookSource):
    rows: ClassVar[list[object]] = []
    error: ClassVar[Exception | None] = None

    def books(self) -> list[Book]:
        if type(self).error is not None:
            raise type(self).error
        return list(type(self).rows)  # type: ignore[arg-type]  # tests plant bad rows too

    def lookup(self, title: str) -> list[Book]:
        return [book for book in self.books() if book.title == title]


class Kiosk(BookSource):
    rows: ClassVar[list[Book]] = []

    def books(self) -> list[Book]:
        return list(type(self).rows)


def shelf_spec() -> PluginSpec:
    return PluginSpec(name="shelf", contracts=lambda: (BookSource,))


def library_spec(*providers: Contract) -> PluginSpec:
    offered: Sequence[Contract] = providers or (Library(),)
    return PluginSpec(name="library", settings=LibrarySettings, provides={"shelf": lambda: offered})


def shop_spec() -> PluginSpec:
    return PluginSpec(name="shop", provides={"shelf": lambda: (Shop(),)})


def kiosk_spec() -> PluginSpec:
    return PluginSpec(name="kiosk", provides={"shelf": lambda: (Kiosk(),)})


def compose(*specs: PluginSpec) -> CompositionResult:
    reset()
    return bootstrap.compose_root(candidates=[provider_candidate(spec) for spec in specs])


def write_config(text: str) -> None:
    import os

    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()
    reset()


LIBRARY_CONFIG = """\
profiles:
  default:
    library:
      catalog: https://library.example
      volumes:
        - {id: 1, name: Dune}
        - {id: 2, name: Emma}
"""
