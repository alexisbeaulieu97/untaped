"""``record`` lines: a declaration's kinds, shaped by who writes them, and core's diagnostics."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel

from untaped.core.surface import removed
from untaped.core.surface._lines import Line
from untaped.core.surface._records import record_lines
from untaped.runtime.bootstrap import compose_root, kept_composition
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Key, Output, Plugin, Record, Stdin, experimental


class Place(BaseModel):
    """Where a shelf stands."""

    city: str
    floor: int = 0


class Shelf(BaseModel):
    """A shelf it prints."""

    name: Annotated[str, Key]
    books: int = 0
    state: Literal["open", "shut"] = "open"
    place: Place | None = None


class Item(BaseModel):
    """What it reads on stdin: any item."""

    name: Annotated[str, Key]


class Book(Item):
    """An item it prints, read through its base."""

    pages: int = 0


class Beta(BaseModel):
    """What only an experimental command prints."""

    name: Annotated[str, Key]


class Shelves(Command):
    """List shelves."""

    output: Output[Shelf]

    def run(self) -> Iterator[Shelf]:
        yield Shelf(name="a")


class Books(Command):
    """List books."""

    output: Output[Book]

    def run(self) -> Iterator[Book]:
        yield Book(name="a")


class Take(Command):
    """Take the items piped in."""

    stdin: Stdin[Item]

    def run(self) -> str:
        return str(len(self.stdin.rows()))


class Count(Command):
    """Count the rows piped in, of any kind."""

    stdin: Stdin[Record]

    def run(self) -> str:
        return str(len(self.stdin.rows()))


class Bet(Command):
    """List what is in beta."""

    stability = experimental
    output: Output[Beta]

    def run(self) -> Iterator[Beta]:
        yield Beta(name="a")


class Shop(Plugin):
    """Sell books."""

    settings = BaseModel
    commands = (Shelves, Books, Take, Count, Bet)


def _lines(target: type[Plugin] = Shop) -> list[Line]:
    candidate = PluginCandidate(distribution="untaped-shop", name="shop", target=target)
    with kept_composition(quiet=True):
        return record_lines(compose_root(candidates=[candidate]), {})


def _texts(target: type[Plugin] = Shop) -> set[str]:
    return {line.text for line in _lines(target)}


def test_a_printed_kind_has_a_bare_owner_and_typed_fields_without_defaults() -> None:
    texts = _texts()
    assert {
        "record shop.shelf",
        "record shop.shelf.name string",
        "record shop.shelf.books integer",
        "record shop.shelf.state string",
        "record shop.shelf.state choice=open",
        "record shop.shelf.state choice=shut",
    } <= texts
    assert not any(text.startswith("record shop.shelf(") for text in texts)
    assert not any("default=" in text for text in texts if text.startswith("record shop.shelf"))


def test_a_kind_read_through_its_base_also_carries_its_required_fields_and_defaults() -> None:
    assert {
        "record shop.book",
        "record shop.book(name)",
        "record shop.book.pages integer",
        "record shop.book.pages default=0",
    } <= _texts()


def test_a_command_reading_any_kind_promises_no_fields() -> None:
    class Counting(Plugin):
        """Count rows."""

        settings = BaseModel
        commands = (Shelves, Count)

    assert "record shop.shelf(name)" not in _texts(Counting)


def test_a_nested_model_gets_lines_of_its_own() -> None:
    texts = _texts()
    assert {
        "record shop.shelf.place Place?",
        "record shop.Place",
        "record shop.Place.city string",
        "record shop.Place.floor integer",
    } <= texts


def test_a_kind_only_an_experimental_command_reaches_is_experimental() -> None:
    marks = {line.text: line.mark for line in _lines()}
    assert marks["record shop.beta.name string"] == "experimental"
    assert marks["record shop.shelf.name string"] is None


def test_adding_a_reader_only_adds_lines_and_a_new_required_field_then_fails() -> None:
    class Printing(Plugin):
        """Print books."""

        settings = BaseModel
        commands = (Books,)

    printed = _lines(Printing)
    read = _lines()
    assert removed(printed, read) == []
    without_pages = [line for line in read if "pages" not in line.text]
    assert removed(without_pages, read) == []
    required = [
        Line("record shop.book(name, pages)", None)
        if line.text == "record shop.book(name)"
        else line
        for line in read
    ]
    assert [line.text for line in removed(read, required)] == ["record shop.book(name)"]


def test_core_s_diagnostic_kinds_have_lines_with_their_level() -> None:
    texts = _texts()
    assert {
        "record untaped.failure",
        "record untaped.failure.level choice=error",
        "record untaped.failure.message string",
        "record untaped.progress.level choice=progress",
    } <= texts


def test_an_owner_keeps_only_its_kinds() -> None:
    candidate = PluginCandidate(distribution="untaped-shop", name="shop", target=Shop)
    with kept_composition(quiet=True):
        lines = record_lines(compose_root(candidates=[candidate]), {}, owner="shop")
    texts = {line.text for line in lines}
    assert "record shop.shelf" in texts
    assert not any(text.startswith("record untaped.") for text in texts)
