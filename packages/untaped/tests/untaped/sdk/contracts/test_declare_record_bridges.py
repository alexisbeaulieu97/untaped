"""Record bridges: a provider issues a second record of its own, routed by its annotation.

The ansible/awx design's N0 (#617), in 12.0's derived roles: a contract method
taking a ``ProviderRecord`` and returning an issued model is a record bridge;
the item bridge still takes ``T``. Scenarios S1 (a provider whose ``T`` is the owner's
model fills a record bridge) and S2 (``Skipped(not-filled)``).
"""

from __future__ import annotations

import json
import threading
from abc import abstractmethod
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Annotated

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.contracts.registry import BoundProvider, Quarantined, offers
from untaped.sdk import (
    Command,
    Issued,
    Key,
    Output,
    Plugin,
    Record,
    Source,
    Stdin,
    UntapedError,
    experimental,
)
from untaped.sdk.contracts import (
    Contract,
    NoProviderReady,
    NotReady,
    Provider,
    ProviderRecord,
    Reason,
    Skipped,
    gather,
)
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.testing import Install, assert_fills, compose_with

if TYPE_CHECKING:
    from decimal import Decimal as Hidden  # a name a fill's annotation can't reach at run time

Installer = Callable[..., Install]


class Card(Issued):
    """The owner's model."""

    title: Annotated[str, Key]


class Pick(Issued):
    """Which cards to take: the owner's selector, issued by a record bridge."""

    match: list[str] = Field(default_factory=list)


@experimental
class Cards[T: Record = Card](Contract):
    """Where cards come from."""

    def to_card(self, item: T) -> Card:
        """A provider's own record as a card."""
        raise NotImplementedError

    def to_pick(self, item: ProviderRecord) -> Pick:
        """A provider's own selector as a pick."""
        raise NotImplementedError

    @abstractmethod
    def cards(self) -> list[Card]:
        """Every card."""

    def picked(self, pick: Pick) -> list[Card]:
        """The cards a pick selects."""
        raise NotImplementedError


class Desk(BaseModel):
    """The owner's settings: none."""


class Choose(Command[Desk]):
    """Print each pick piped, read through its provider's record bridge."""

    stdin: Stdin[Pick] = Stdin(via=Cards.to_pick)
    output: Output[Pick]

    def run(self) -> Iterator[Pick]:
        yield from self.stdin.rows()


class Counter(Plugin):
    """Pick cards."""

    settings = Desk
    commands = (Choose,)
    offers = (Cards,)


class ShelfPick(Record):
    """The lending plugin's own selector."""

    shelves: list[str]


class Other(Record):
    """A record of the lending plugin that no bridge takes."""

    shelves: list[str]


class ShelfCards(Cards, Provider):
    """Issues the owner's cards, and picks from its own selector."""

    def to_pick(self, item: ShelfPick) -> Pick:
        return Pick()

    def cards(self) -> list[Card]:
        return [Card(title="Dune"), Card(title="Emma")]

    def picked(self, pick: Pick) -> list[Card]:
        shelves = self.own(pick, ShelfPick).shelves
        return [card for card in self.cards() if card.title[0].lower() in shelves]


class PlainCards(Cards, Provider):
    """Fills only the listing."""

    def cards(self) -> list[Card]:
        return [Card(title="Atlas")]


class UnnamedPicks(Cards, Provider):
    """Fills the record bridge without naming the record it takes."""

    def to_pick(self, item: Record) -> Pick:
        return Pick()

    def cards(self) -> list[Card]:
        return []


#: ShelfPick's kind: its class name under the plugin that fills with it.
SHELF_PICK = "lend.shelf_pick"


def _line(kind: str, **record: object) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n"


def _counter(install: Installer) -> Install:
    return install(Counter, providers={"lend": (ShelfCards,), "plain": (PlainCards,)})


def _provider(name: str) -> BoundProvider:
    info = contract_of(Cards)
    assert info is not None
    found = [entry for entry in offers(info) if entry.plugin == name]
    assert len(found) == 1 and isinstance(found[0], BoundProvider)
    return found[0]


def _issued_pick(install: Installer) -> Pick:
    ran = _counter(install).cli(
        "counter choose --stdin -f json", input=_line(SHELF_PICK, shelves=["d"])
    )
    assert ran.exit == 0, ran.stderr
    return Pick.model_validate(json.loads(ran.stdout)[0])


def test_roles_come_from_the_types_and_one_item_bridge_is_allowed() -> None:
    info = contract_of(Cards)
    assert info is not None
    roles = {name: (method.bridge, method.item_bridge) for name, method in info.methods.items()}
    assert roles == {
        "to_card": (True, True),
        "to_pick": (True, False),
        "cards": (False, False),
        "picked": (False, False),
    }
    with pytest.raises(TypeError, match="both take T"):

        class Twice[T: Record = Card](Contract):
            def a(self, item: T) -> Card:
                raise NotImplementedError

            def b(self, item: T) -> Card:
                raise NotImplementedError


def test_s1_a_piped_selector_comes_back_stamped_with_its_record(install: Installer) -> None:
    pick = _issued_pick(install)
    assert pick.source is not None
    assert pick.source.model_dump() == {
        "plugin": "lend",
        "kind": SHELF_PICK,
        "record": {"shelves": ["d"]},
    }


def test_s1_own_reads_the_record_typed_and_refuses_another(install: Installer) -> None:
    pick = _issued_pick(install)
    with _counter(install).running():
        provider = _provider("lend").instance
        assert provider.own(pick, ShelfPick) == ShelfPick(shelves=["d"])
        with pytest.raises(UntapedError, match="not from Other") as raised:
            provider.own(pick, Other)
        assert raised.value.category == "invalid"
        with pytest.raises(UntapedError, match="not issued by plain"):
            _provider("plain").instance.own(pick, ShelfPick)


def test_s1_the_selector_reaches_the_provider_that_issued_it(install: Installer) -> None:
    pick = _issued_pick(install)
    with _counter(install).running():
        answers = gather(Cards.picked, plugins={"lend"})(pick)
    assert [card.title for ok in answers.ok for card in ok.value] == ["Dune"]


class Lending(BaseModel):
    """The lending plugin's settings: none."""


class Lend(Plugin):
    """Lend cards."""

    settings = Lending
    fills = (ShelfCards,)


def test_s1_assert_fills_round_trips_a_selector_sample() -> None:
    with compose_with(Counter, Lend):
        assert_fills(ShelfCards, samples=[ShelfPick(shelves=["d"])])
        with pytest.raises(TypeError, match="needs a ShelfPick among samples"):
            assert_fills(ShelfCards)
        with pytest.raises(AssertionError, match="no bridge of ShelfCards takes"):
            assert_fills(ShelfCards, samples=[ShelfPick(shelves=["d"]), Other(shelves=["d"])])


def test_s1_an_unfilled_record_bridge_quarantines_nothing(install: Installer) -> None:
    with _counter(install).running():
        assert isinstance(_provider("plain"), BoundProvider)


def test_a_record_bridge_filled_without_naming_its_record_is_quarantined(
    install: Installer,
) -> None:
    with install(Counter, providers={"vague": (UnnamedPicks,)}).running():
        info = contract_of(Cards)
        assert info is not None
        [entry] = [each for each in offers(info) if each.plugin == "vague"]
    assert isinstance(entry, Quarantined)
    assert entry.reason is Reason.unresolved_item_type
    assert "to_pick" in entry.detail


def test_s2_a_named_plugin_that_does_not_fill_the_method_is_not_filled(
    install: Installer,
) -> None:
    with _counter(install).running():
        answers = gather(Cards.picked, plugins={"plain"})(Pick())
        everyone = gather(Cards.picked)(Pick())
    [skip] = answers.skipped
    assert skip == Skipped("plain", Reason.not_filled, "plain doesn't fill Cards.picked")
    assert [answer.plugin for answer in everyone] == ["lend"]  # unnamed: left out, as before


def test_nobody_named_and_nothing_filled_is_still_no_provider_ready(install: Installer) -> None:
    with (
        install(Counter, providers={"plain": (PlainCards,)}).running(),
        pytest.raises(NoProviderReady),
    ):
        gather(Cards.picked)(Pick())


class Lent(Record):
    """A record the lending plugin issues cards from: its own ``T``."""

    name: str


class LentCards(Cards[Lent], Provider):
    """Issues cards from its own records and picks from its own selector, as GithubRepos will."""

    def to_card(self, item: Lent) -> Card:
        return Card(title=item.name)

    def to_pick(self, item: ShelfPick) -> Pick:
        return Pick()

    def cards(self) -> list[Card]:
        return [self.to_card(Lent(name="Dune"))]


class Lender(Plugin):
    """Lend cards issued from its own records."""

    settings = Lending
    fills = (LentCards,)


class HiddenPicks(Cards, Provider):
    """Names its record under ``TYPE_CHECKING`` only, so its annotation doesn't resolve."""

    def to_pick(self, item: Hidden) -> Pick:
        return Pick()

    def cards(self) -> list[Card]:
        return []


def test_a_provider_with_its_own_t_fills_a_record_bridge_too(install: Installer) -> None:
    lending = install(Counter, providers={"lend": (LentCards,)})
    picked = lending.cli("counter choose --stdin -f json", input=_line(SHELF_PICK, shelves=["d"]))
    assert picked.exit == 0, picked.stderr
    assert json.loads(picked.stdout)[0]["source"]["kind"] == SHELF_PICK
    refused = lending.cli("counter choose --stdin", input=_line("lend.lent", name="Dune"))
    assert refused.exit == 2  # its T is read by the item bridge, not by this one
    with lending.running():
        provider = _provider("lend").instance
        assert isinstance(provider, LentCards)
        [card] = provider.cards()
        assert provider.own(card) == Lent(name="Dune")  # the default reads its T
    with compose_with(Counter, Lender):
        assert_fills(LentCards, samples=[Lent(name="Dune"), ShelfPick(shelves=["d"])])


def test_help_names_the_records_the_piped_bridge_reads(install: Installer) -> None:
    ran = install(Counter, providers={"lend": (LentCards,)}).cli("docs counter commands choose")
    assert ran.exit == 0, ran.stderr
    assert "`lend.shelf_pick`" in ran.stdout
    assert "`lend.lent`" not in ran.stdout


def test_a_record_bridge_stamps_and_reads_its_kind_off_the_run_s_thread(
    install: Installer,
) -> None:
    pick = _issued_pick(install)
    with _counter(install).running():
        provider = _provider("lend").instance
        assert isinstance(provider, ShelfCards)
        done: dict[str, object] = {}

        def aside() -> None:  # a plain thread: it doesn't see the run
            done["stamped"] = provider.to_pick(ShelfPick(shelves=["e"])).source
            done["owned"] = provider.own(pick, ShelfPick)

        thread = threading.Thread(target=aside)
        thread.start()
        thread.join()
    assert done == {
        "stamped": Source(plugin="lend", kind=SHELF_PICK, record={"shelves": ["e"]}),
        "owned": ShelfPick(shelves=["d"]),
    }


def test_own_refuses_a_record_that_no_longer_reads(install: Installer) -> None:
    stale = Pick(source=Source(plugin="lend", kind=SHELF_PICK, record={"shelves": "d"}))
    with _counter(install).running(), pytest.raises(UntapedError, match="no longer reads"):
        _provider("lend").instance.own(stale, ShelfPick)


def test_a_fill_whose_annotation_does_not_resolve_says_so(install: Installer) -> None:
    with install(Counter, providers={"hidden": (HiddenPicks,)}).running():
        info = contract_of(Cards)
        assert info is not None
        [entry] = [each for each in offers(info) if each.plugin == "hidden"]
    assert isinstance(entry, Quarantined)
    assert entry.reason is Reason.unresolved_item_type
    assert "outside TYPE_CHECKING" in entry.detail


def test_provider_record_is_a_record_bridge_s_parameter_only() -> None:
    with pytest.raises(TypeError, match="ProviderRecord is a record bridge's one parameter"):

        class Plain(Contract):
            def pick(self, item: ProviderRecord) -> Pick:
                raise NotImplementedError

    with pytest.raises(TypeError, match="ProviderRecord is a record bridge's one parameter"):

        class Counted[T: Record = Card](Contract):
            def count(self, item: ProviderRecord) -> int:
                raise NotImplementedError


def test_named_plugins_answer_each_its_own_skip(install: Installer) -> None:
    """One named plugin not filled and one not ready: both skips come back, nothing raises."""

    class Unready(Cards, Provider):
        def ready(self) -> NotReady | None:
            return NotReady("no shelf")

        def picked(self, pick: Pick) -> list[Card]:
            return []

        def cards(self) -> list[Card]:
            return []

    with install(Counter, providers={"plain": (PlainCards,), "shut": (Unready,)}).running():
        answers = gather(Cards.picked, plugins={"plain", "shut"})(Pick())
    assert {(skip.plugin, skip.reason) for skip in answers.skipped} == {
        ("plain", Reason.not_filled),
        ("shut", Reason.not_configured),
    }
