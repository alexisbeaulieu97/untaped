"""A toy owner (``shelf``) and its providers (``library``, ``shop``, ``kiosk``)."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from untaped.runtime import bootstrap
from untaped.runtime.contracts.registry import BoundProvider, offers, reset
from untaped.runtime.registry import CompositionResult, PluginCandidate, PluginSpec
from untaped.runtime.run import build_run, entered
from untaped.runtime.settings import get_settings
from untaped.sdk import Plugin, experimental
from untaped.sdk._home import config_path
from untaped.sdk._run import Run, run_or_none
from untaped.sdk.contracts import Contract, Issued, Provider, Record, cached
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.testing import plugin_candidate


class Book(Issued, kind="shelf.book"):
    title: str
    pages: int = 0


@experimental
class BookSource[T: Record = Book](Contract):
    """Where books come from."""

    def to_book(self, item: T) -> Book:
        raise NotImplementedError

    @cached(max_age=timedelta(hours=1), label="books")
    @abstractmethod
    def books(self) -> list[Book]: ...

    def lookup(self, title: str) -> list[Book]:
        raise NotImplementedError

    @cached(max_age=timedelta(hours=1), label="books")
    def by_author(self, author: str, limit: int = 10) -> list[Book]:
        raise NotImplementedError

    def first(self) -> Book:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


BOOKS = contract_of(BookSource).methods["books"].cache
BY_AUTHOR = contract_of(BookSource).methods["by_author"].cache


class Volume(Record, kind="library.volume"):
    id: int
    name: str


class SpecialVolume(Volume, kind="library.special_volume"):
    signed: bool = False


class ShelfSettings(BaseModel):
    """What the shelf reads: nothing."""


class NoSettings(BaseModel):
    """What a toy plugin reads: nothing."""


class LibrarySettings(BaseModel):
    catalog: str
    volumes: list[Volume] = []


class Library(BookSource[Volume], Provider[LibrarySettings]):
    calls: ClassVar[int] = 0
    error: ClassVar[Exception | None] = None

    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name, pages=item.id)

    def books(self) -> list[Book]:
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return [self.to_book(volume) for volume in self.settings.volumes]


class Shop(BookSource, Provider):
    rows: ClassVar[list[object]] = []
    error: ClassVar[Exception | None] = None
    asked: ClassVar[list[tuple[str, int]]] = []
    total: ClassVar[object] = 0

    def books(self) -> list[Book]:
        if type(self).error is not None:
            raise type(self).error
        return list(type(self).rows)  # type: ignore[arg-type]  # tests plant bad rows too

    def lookup(self, title: str) -> list[Book]:
        return [book for book in self.books() if book.title == title]

    def by_author(self, author: str, limit: int = 10) -> list[Book]:
        type(self).asked.append((author, limit))
        return [Book(title=author)]

    def first(self) -> Book:
        return type(self).rows[0]  # type: ignore[return-value]  # tests plant bad rows too

    def count(self) -> int:
        return type(self).total  # type: ignore[return-value]  # tests plant bad values too


class Kiosk(BookSource, Provider):
    rows: ClassVar[list[Book]] = []

    def books(self) -> list[Book]:
        return list(type(self).rows)


def plugin(
    name: str,
    *,
    settings: type[BaseModel] = NoSettings,
    offers: Sequence[type] = (),
    fills: Sequence[type] = (),
) -> PluginCandidate:
    """A plugin class named ``name`` holding ``offers`` and ``fills``, as a candidate."""
    cls = type(
        name.title().replace("-", ""),
        (Plugin,),
        {
            "__doc__": f"The {name} plugin.",
            "__module__": __name__,
            "settings": settings,
            "offers": tuple(offers),
            "fills": tuple(fills),
        },
    )
    return PluginCandidate(distribution=f"untaped-{name}", name=name, target=cls)


def shelf_plugin() -> PluginCandidate:
    return plugin("shelf", settings=ShelfSettings, offers=(BookSource,))


def library_plugin(*providers: type) -> PluginCandidate:
    return plugin("library", settings=LibrarySettings, fills=providers or (Library,))


def shop_plugin() -> PluginCandidate:
    return plugin("shop", fills=(Shop,))


def kiosk_plugin() -> PluginCandidate:
    return plugin("kiosk", fills=(Kiosk,))


def bound(name: str) -> Contract:
    """The ``BookSource`` provider instance plugin ``name`` answered with."""
    [found] = [
        each.instance
        for each in offers(contract_of(BookSource))  # type: ignore[arg-type]  # a contract
        if isinstance(each, BoundProvider) and each.plugin == name
    ]
    return found


@dataclass
class _Held:
    """The run :func:`compose` keeps current until :func:`fresh_providers` ends."""

    stack: ExitStack
    run: Run | None = None


_HELD: list[_Held] = []


def candidates(*plugins: PluginCandidate | PluginSpec) -> list[PluginCandidate]:
    """``plugins`` as candidates; a ``PluginSpec`` tests the 11.x path and goes with it."""
    return [
        each if isinstance(each, PluginCandidate) else plugin_candidate(each) for each in plugins
    ]


def compose(*plugins: PluginCandidate | PluginSpec) -> CompositionResult:
    """Compose ``plugins``; under :func:`fresh_providers`, also enter a run of it until the end.

    A provider reads its settings and context from the run, as it would
    inside a command, so a test that gathers composes under the fixture.
    A run the test entered itself (``home_run``, ``invocation()``) stays
    current instead.
    """
    reset()
    result = bootstrap.compose_root(candidates=candidates(*plugins))
    if _HELD and run_or_none() in (None, (held := _HELD[-1]).run):
        held.stack.close()
        held.run = build_run(result.mount.declaration, result.mount.settings)
        held.stack.enter_context(entered(held.run))
    return result


def write_config(text: str) -> None:

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()
    reset()


LIBRARY_CONFIG = """\
format_version: 2
library:
  catalog: https://library.example
  volumes:
    - {id: 1, name: Dune}
    - {id: 2, name: Emma}
"""


@contextmanager
def fresh_providers() -> Iterator[None]:
    """Fresh provider counters and an empty contract registry, before and after."""
    Library.calls = 0
    Library.error = None
    Shop.rows = []
    Shop.error = None
    Shop.asked = []
    Shop.total = 0
    Kiosk.rows = []
    reset()
    with ExitStack() as stack:
        _HELD.append(_Held(stack))
        try:
            yield
        finally:
            _HELD.pop()
    reset()
