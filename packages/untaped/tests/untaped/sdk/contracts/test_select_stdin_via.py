"""``Stdin(via=Contract.bridge)``: a provider's own rows read as the owner's model (design §6)."""

from __future__ import annotations

import json
from abc import abstractmethod
from collections.abc import Callable, Iterator

import pytest
from pydantic import BaseModel, Field

from untaped.sdk import Command, Output, Plugin, Positional, Stdin, experimental, key
from untaped.sdk.contracts import Contract, Issued, NotReady, Provider, Record
from untaped.sdk.testing import Install

Installer = Callable[..., Install]


class Card(Issued):
    """The owner's model."""

    title: str


class Loan(Record):
    """A provider's own record."""

    shelf_mark: str
    name: str


@experimental
class Cards[T: Record = Card](Contract):
    """Where cards come from."""

    def to_card(self, item: T) -> Card:
        """A provider's own record as a card."""
        raise NotImplementedError

    @abstractmethod
    def cards(self) -> list[Card]:
        """Every card."""


class Desk(BaseModel):
    """The owner's settings: none."""


class Show(Command[Desk]):
    """Print each card given or piped."""

    titles: Positional[list[str]] = Field(default_factory=list, description="Titles.")
    stdin: Stdin[Card] = Stdin(via=Cards.to_card)
    output: Output[Card]

    def run(self) -> Iterator[Card]:
        for value in self.stdin.values(self.titles):
            yield Card(title=value) if isinstance(value, str) else value


class Counter(Plugin):
    """Show cards."""

    settings = Desk
    commands = (Show,)
    offers = (Cards,)


class Lending(BaseModel):
    """The provider's settings."""

    branch: str = ""


class Loans(Cards[Loan], Provider[Lending]):
    built = 0

    def __init__(self) -> None:
        type(self).built += 1

    def ready(self) -> NotReady | None:
        if not self.settings.branch:
            return NotReady("no branch", key(Lending).branch)
        return None

    def to_card(self, item: Loan) -> Card:
        return Card(title=f"{item.name} ({self.settings.branch})")

    def cards(self) -> list[Card]:
        return []


#: Loan's kind: its class name under the plugin whose package defines it, this test's owner.
LOAN = "counter.loan"


def _line(kind: str, **record: object) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n"


@pytest.fixture(autouse=True)
def _count() -> None:
    Loans.built = 0


def _counter(install: Installer, branch: str = "north") -> Install:
    return install(Counter, providers={"lend": (Loans,)}, settings=[Lending(branch=branch)])


def test_a_provider_row_is_read_through_its_bridge(install: Installer) -> None:
    piped = _line(LOAN, shelf_mark="A1", name="Dune") + _line("counter.card", title="Emma")
    ran = _counter(install).cli("counter show --stdin -f json", input=piped)
    assert ran.exit == 0, ran.stderr
    rows = json.loads(ran.stdout)
    assert [row["title"] for row in rows] == ["Dune (north)", "Emma"]
    assert rows[0]["source"] == {
        "plugin": "lend",
        "kind": LOAN,
        "record": {"shelf_mark": "A1", "name": "Dune"},
    }
    assert "source" not in rows[1] or rows[1]["source"] is None


def test_nothing_piped_builds_no_provider(install: Installer) -> None:
    ran = _counter(install, branch="").cli("counter show Dune -f json")
    assert ran.exit == 0, ran.stderr
    assert Loans.built == 0


def test_an_unready_provider_whose_row_arrived_exits_4(install: Installer) -> None:
    piped = _line(LOAN, shelf_mark="A1", name="Dune")
    ran = _counter(install, branch="").cli("counter show --stdin", input=piped)
    assert ran.exit == 4
    assert any("no branch (set lend.branch)" in line for line in ran.stderr), ran.stderr


@pytest.mark.parametrize(
    "piped",
    [_line("acme.widget", name="x"), json.dumps({"untaped": "1", "record": {"name": "x"}}) + "\n"],
)
def test_a_kind_no_provider_reads_or_no_kind_exits_2(install: Installer, piped: str) -> None:
    ran = _counter(install).cli("counter show --stdin", input=piped)
    assert ran.exit == 2, ran.stderr


def test_via_must_be_a_bridge_returning_what_stdin_accepts() -> None:
    class Other(Issued):
        name: str

    with pytest.raises(TypeError, match="is not a bridge"):
        Stdin(via=Cards.cards)._check_via((Card,))
    with pytest.raises(TypeError, match=r"reads Card rows, which Stdin\[Other\] doesn't accept"):
        Stdin(via=Cards.to_card)._check_via((Other,))
