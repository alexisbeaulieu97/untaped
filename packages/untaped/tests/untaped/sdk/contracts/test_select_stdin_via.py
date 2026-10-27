"""``Stdin(via=Contract.bridge)``: a provider's own rows read as the owner's model (design §6)."""

from __future__ import annotations

import json
from abc import abstractmethod
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import target_of
from untaped.sdk import Command, Output, Plugin, Positional, Stdin, experimental, key
from untaped.sdk.contracts import Contract, Issued, NotReady, Provider, Record, gather
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


class BigCard(Card):
    """A card printed by its own command: a model that subclasses the owner's."""

    size: int = 0


class Big(Command[Desk]):
    """Print a big card."""

    output: Output[BigCard]

    def run(self) -> BigCard:
        return BigCard(title="Atlas", size=3)


class Every(Command[Desk]):
    """Print every card every provider keeps."""

    output: Output[Card]

    def run(self) -> Iterator[Card]:
        for ok in gather(Cards.cards)().ok:
            yield from ok.value


class Counter(Plugin):
    """Show cards."""

    settings = Desk
    commands = (Show, Big, Every)
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
        with ThreadPoolExecutor(1) as pool:  # a plain thread: it doesn't see the run
            return list(pool.map(self.to_card, [Loan(shelf_mark="A1", name="Dune")]))


#: Loan's kind: its class name under the plugin that fills with it, the kit's ``lend``.
LOAN = "lend.loan"


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


def test_a_row_stdin_r_takes_reads_without_the_bridge(install: Installer) -> None:
    counter = _counter(install)
    piped = counter.cli("counter big -f pipe").stdout
    ran = counter.cli("counter show --stdin -f json", input=piped)
    assert ran.exit == 0, ran.stderr
    assert [row["title"] for row in json.loads(ran.stdout)] == ["Atlas"]


def test_a_bridge_called_off_the_run_s_thread_still_names_its_kind(install: Installer) -> None:
    ran = _counter(install).cli("counter every -f json")
    assert ran.exit == 0, ran.stderr
    [row] = json.loads(ran.stdout)
    assert (row["source"]["plugin"], row["source"]["kind"]) == ("lend", LOAN)


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


def _bad_via(field: Stdin[Card]) -> list[tuple[str, str]]:
    class Bad(Command[Desk]):
        """Read cards."""

        stdin: Stdin[Card] = field
        output: Output[Card]

        def run(self) -> Iterator[Card]:
            yield from ()

    class Owner(Plugin):
        """Own cards."""

        settings = Desk
        commands = (Bad,)
        offers = (Cards,)

    declaration = compose([PluginEntry(name="owner", target=target_of(Owner), plugin=Owner)])
    [refused] = declaration.quarantined
    return [(problem.rule, problem.detail) for problem in refused.problems]


class Other(Issued):
    name: str


@experimental
class Others[T: Record = Other](Contract):
    """Other things, whose bridge returns no card."""

    def to_other(self, item: T) -> Other:
        """A provider's record as another thing."""
        raise NotImplementedError


def test_via_must_be_a_bridge_returning_what_stdin_accepts() -> None:
    [(rule, detail)] = _bad_via(Stdin(via=Cards.cards))
    assert rule == "field-type"
    assert "takes a contract's bridge method" in detail
    [(rule, detail)] = _bad_via(Stdin(via=Others.to_other))
    assert rule == "field-type"
    assert detail.endswith("reads Other rows, which it doesn't accept")


def test_a_provider_record_whose_kind_is_taken_leaves_the_plugin_composed() -> None:
    def own() -> type[Record]:
        class Loan(Record):  # the kind lender.loan, which the printed Loan holds
            note: str

        return Loan

    taken = own()

    class Mine(Command[Lending]):
        """Print a loan."""

        output: Output[Loan]

        def run(self) -> Loan:
            return Loan(shelf_mark="A1", name="Dune")

    class Clash(Cards[taken], Provider[Lending]):  # type: ignore[valid-type]
        def to_card(self, item: Record) -> Card:
            return Card(title="x")

        def cards(self) -> list[Card]:
            return []

    class Lender(Plugin):
        """Lend and print loans."""

        settings = Lending
        commands = (Mine,)
        fills = (Clash,)

    declaration = compose([PluginEntry(name="lender", target=target_of(Lender), plugin=Lender)])
    assert [plugin.name for plugin in declaration.plugins] == ["lender"]
    assert [(kind.kind, kind.target) for kind in declaration.kinds] == [
        ("lender.loan", target_of(Loan))
    ]
