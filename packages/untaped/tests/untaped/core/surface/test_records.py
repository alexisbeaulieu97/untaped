"""``record`` lines: a declaration's kinds, shaped by who writes them, and core's diagnostics."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from untaped.core.surface import SurfaceError, removed
from untaped.core.surface._lines import Line
from untaped.core.surface._records import record_lines
from untaped.runtime.bootstrap import compose_root, kept_composition
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Failure, File, Key, Output, Plugin, Record, Stdin, experimental


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


class Stand(BaseModel):
    """What a stable command reads, holding a kind only an experimental command prints."""

    name: Annotated[str, Key]
    beta: Beta | None = None
    error: Failure | None = None


class Stock(Command):
    """Stock the stands piped in, and print them."""

    stdin: Stdin[Stand]
    output: Output[Stand]

    def run(self) -> Iterator[Stand]:
        yield from self.stdin.rows()


class Restock(Command):
    """Restock from a file."""

    plan: File[Shelf] = Field(description="The shelf to restock.")

    def run(self) -> str:
        return self.plan.load().name


@experimental
class Draft(BaseModel):
    """A model still settling."""

    name: Annotated[str, Key]


class Drafts(Command):
    """List drafts."""

    output: Output[Draft]

    def run(self) -> Iterator[Draft]:
        yield Draft(name="a")


class Main(Command):
    """Read the items piped in and print them back."""

    stability = experimental
    stdin: Stdin[Item]
    output: Output[Item]

    def run(self) -> Iterator[Item]:
        yield from self.stdin.rows()


def _lines(target: type[Plugin] = Shop, *, owner: str | None = None) -> list[Line]:
    candidate = PluginCandidate(distribution="untaped-shop", name="shop", target=target)
    with kept_composition(quiet=True):
        result = compose_root(candidates=[candidate])
        assert not result.quarantine
        return record_lines(result, {}, owner=owner)


def _plugin(*commands: type[Command], runs: type[Command] | None = None) -> type[Plugin]:
    class Built(Plugin):
        """Sell books."""

        settings = BaseModel

    Built.commands = commands
    Built.runs = runs
    return Built


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


def test_an_owner_keeps_only_its_kinds_and_never_relists_core_s() -> None:
    texts = {line.text for line in _lines(_plugin(Stock, Bet), owner="shop")}
    assert {"record shop.stand", "record shop.beta"} <= texts
    assert not any(text.startswith("record untaped.") for text in texts)


def test_a_kind_nested_in_a_read_kind_is_read_and_stable_too() -> None:
    marks = {line.text: line.mark for line in _lines(_plugin(Stock, Bet))}
    assert marks["record shop.beta(name)"] is None
    assert marks["record shop.beta.name string"] is None
    assert "record untaped.Failure" not in marks


def test_a_file_a_command_reads_is_written() -> None:
    assert "record shop.shelf(name)" in _texts(_plugin(Restock))


def test_the_plugin_s_own_command_reaches_its_kinds() -> None:
    marks = {line.text: line.mark for line in _lines(_plugin(runs=Main))}
    assert marks["record shop.item(name)"] == "experimental"


def test_a_stable_command_reaching_an_experimental_model_is_refused() -> None:
    with pytest.raises(SurfaceError, match="@experimental"):
        _lines(_plugin(Drafts))


def test_a_model_core_cannot_import_is_skipped() -> None:
    class Local(BaseModel):
        """Defined where no import reaches."""

        name: str

    class Read(Command):
        """Read local rows."""

        stdin: Stdin[Local]

        def run(self) -> str:
            return ""

    assert "record shop.shelf" in _texts(_plugin(Read, Shelves))


class Box[T](BaseModel):
    """A box of anything."""

    name: Annotated[str, Key]
    held: T | None = None


class Boxes(Command):
    """List boxes of numbers."""

    output: Output[Box[int]]

    def run(self) -> Iterator[Box[int]]:
        yield Box[int](name="a")


def test_a_kind_core_cannot_import_is_skipped() -> None:
    assert "record shop.shelf" in _texts(_plugin(Boxes, Shelves))


def test_a_model_two_deprecations_reach_names_no_replacement() -> None:
    from untaped.core.surface._contracts import _model_mark
    from untaped.core.surface._lines import DeprecatedMark

    login, forget = (
        DeprecatedMark(12, "untaped auth login"),
        DeprecatedMark(12, "untaped auth forget"),
    )
    assert _model_mark({login, forget}, stable_export=False) == DeprecatedMark(12)
    assert _model_mark({login, DeprecatedMark(11, "x")}, stable_export=False) == login
