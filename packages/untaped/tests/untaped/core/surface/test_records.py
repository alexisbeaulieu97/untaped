"""``record`` lines: a declaration's kinds, shaped by direction, and core's diagnostic kinds."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel

from untaped.core.surface._records import record_lines
from untaped.runtime.bootstrap import compose_root, kept_composition
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Key, Output, Plugin, Stdin


class Shelf(BaseModel):
    """A shelf it prints."""

    name: Annotated[str, Key]
    books: int = 0
    state: Literal["open", "shut"] = "open"


class Order(BaseModel):
    """An order it reads on stdin and prints back."""

    book: Annotated[str, Key]
    copies: int = 1


class Shelves(Command):
    """List shelves."""

    output: Output[Shelf]

    def run(self) -> Iterator[Shelf]:
        yield Shelf(name="a")


class Place(Command):
    """Place the orders piped in."""

    stdin: Stdin[Order]
    output: Output[Order]

    def run(self) -> Iterator[Order]:
        yield from self.stdin.rows()


class Shop(Plugin):
    """Sell books."""

    settings = BaseModel
    commands = (Shelves, Place)


def _texts() -> set[str]:
    candidate = PluginCandidate(distribution="untaped-shop", name="shop", target=Shop)
    with kept_composition(quiet=True):
        result = compose_root(candidates=[candidate])
        return {line.text for line in record_lines(result, {})}


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


def test_a_kind_read_on_stdin_carries_its_required_fields_and_defaults() -> None:
    texts = _texts()
    assert {
        "record shop.order(book)",
        "record shop.order.book string",
        "record shop.order.copies integer default=1",
    } <= texts


def test_core_s_diagnostic_kinds_have_lines() -> None:
    texts = _texts()
    assert {"record untaped.failure", "record untaped.progress"} <= texts
    assert "record untaped.failure.message string" in texts
