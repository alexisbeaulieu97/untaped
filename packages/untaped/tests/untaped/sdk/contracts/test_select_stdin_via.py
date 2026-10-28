"""``Stdin(via=Contract.bridge)``: a provider's own rows read as the owner's model (design §6)."""

from __future__ import annotations

import importlib
import json
import sys
from abc import abstractmethod
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import target_of
from untaped.sdk import Command, Key, Output, Plugin, Positional, Stdin, experimental, key
from untaped.sdk._pipe import PipeEnvelope
from untaped.sdk.contracts import Contract, Issued, NotReady, Provider, Record, convert, gather
from untaped.sdk.testing import Install

Installer = Callable[..., Install]


class Card(Issued):
    """The owner's model."""

    title: Annotated[str, Key]


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


class Titles(Command[Desk]):
    """Print the title of each card given or piped."""

    titles: Positional[list[str]] = Field(default_factory=list, description="Titles.")
    stdin: Stdin[Card] = Stdin(via=Cards.to_card)

    def run(self) -> str:
        return ",".join(self.stdin.identifiers(self.titles))


class Aside(Command[Desk]):
    """Print every card, asking from a thread that doesn't see the run."""

    output: Output[Card]

    def run(self) -> Iterator[Card]:
        with ThreadPoolExecutor(1) as pool:
            answers = pool.submit(lambda: gather(Cards.cards)()).result()
        for ok in answers.ok:
            yield from ok.value


class Counter(Plugin):
    """Show cards."""

    settings = Desk
    commands = (Show, Big, Every, Titles, Aside)
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


def test_convert_reads_a_provider_row_whose_kind_composition_named(install: Installer) -> None:
    """A provider's record names no ``kind=``: ``convert`` finds its model by the composed kind."""
    envelope = PipeEnvelope(kind=LOAN, record={"shelf_mark": "A1", "name": "Dune"}, lineno=1)
    with _counter(install).running():
        card = convert(Cards.to_card, envelope)
    assert card.title == "Dune (north)"
    assert card.source is not None and card.source.kind == LOAN


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


class Kept(Cards, Provider):
    def cards(self) -> list[Card]:
        return [Card(title="Kept")]


def test_a_first_ask_off_the_run_s_thread_binds_the_same_kinds(install: Installer) -> None:
    counter = install(
        Counter, providers={"lend": (Loans,), "keep": (Kept,)}, settings=[Lending(branch="n")]
    )
    ran = counter.cli("counter aside -f json")
    assert ran.exit == 0, ran.stderr
    sources = {row["title"]: row["source"] for row in json.loads(ran.stdout)}
    assert sources["Dune (n)"]["kind"] == LOAN
    assert sources["Kept"] == {"plugin": "keep", "kind": "counter.card", "record": {}}


def test_identifiers_of_a_provider_row_are_its_bridged_key(install: Installer) -> None:
    piped = _line(LOAN, shelf_mark="A1", name="Dune") + _line("counter.card", title="Emma")
    ran = _counter(install).cli("counter titles --stdin", input=piped)
    assert ran.exit == 0, ran.stderr
    assert ran.stdout == "Dune (north),Emma\n"


class Lend(Plugin):
    """Lend cards."""

    settings = Lending
    fills = (Loans,)


def test_the_surface_lists_the_provider_kinds_a_via_reads() -> None:
    from untaped.core.surface import generate, render
    from untaped.runtime.registry import PluginCandidate

    candidates = [
        PluginCandidate(distribution=f"untaped-{name}", name=name, target=target_of(cls))
        for name, cls in (("counter", Counter), ("lend", Lend))
    ]
    lines = render(generate(candidates)).splitlines()
    assert f"record {LOAN}(name, shelf_mark)" in lines  # written: a command reads it


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
    [(rule, detail)] = _bad_via(Stdin(via=print))
    assert "takes a contract's bridge method" in detail
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


def _composed(*plugins: tuple[str, type[Plugin]]) -> Any:
    return compose(
        [PluginEntry(name=name, target=target_of(cls), plugin=cls) for name, cls in plugins]
    )


class Taken(Record):
    note: str


Taken.__name__ = "Loan"  # snake-cased like Loan: the same kind under one plugin


def test_a_provider_record_a_command_also_prints_must_not_clash() -> None:
    class Mine(Command[Lending]):
        """Print a loan."""

        output: Output[Loan]

        def run(self) -> Loan:
            raise NotImplementedError

    class Theirs(Command[Lending]):
        """Print the other loan."""

        output: Output[Taken]

        def run(self) -> Taken:
            raise NotImplementedError

    class Clash(Cards[Taken], Provider[Lending]):
        def to_card(self, item: Taken) -> Card:
            return Card(title="x")

        def cards(self) -> list[Card]:
            return []

    class Lender(Plugin):
        """Lend and print loans."""

        settings = Lending
        commands = (Mine, Theirs)
        fills = (Clash,)

    [refused] = _composed(("lender", Lender)).quarantined
    assert [problem.rule for problem in refused.problems] == ["duplicate-kind"]


def test_two_providers_of_one_record_give_it_one_kind() -> None:
    class Again(Cards[Loan], Provider[Lending]):
        def to_card(self, item: Loan) -> Card:
            return Card(title=item.name)

        def cards(self) -> list[Card]:
            return []

    class Archive(Plugin):
        """Lend the same loans."""

        settings = Lending
        fills = (Again,)

    declaration = _composed(("archive", Archive), ("lend", Lend))
    assert [plugin.name for plugin in declaration.plugins] == ["archive", "lend"]
    assert [kind.kind for kind in declaration.kinds if kind.target == target_of(Loan)] == [
        "archive.loan"
    ]


#: Three plugins in three top-level packages: a kind's plugin part is read from a
#: record's package or from the plugin listing it, never from the order entries come in.
_PRINTER = """
from __future__ import annotations
from pydantic import BaseModel
from untaped.sdk import Command, Output, Plugin
from untaped.sdk.contracts import Issued, Record

class Plain(BaseModel):
    \"\"\"No settings.\"\"\"

class Loan(Record):
    name: str

class Tag(Issued):
    name: str

class PrintLoan(Command[Plain]):
    \"\"\"Print a loan.\"\"\"
    output: Output[Loan]
    def run(self) -> Loan:
        return Loan(name="x")

class PrintTag(Command[Plain]):
    \"\"\"Print a tag.\"\"\"
    output: Output[Tag]
    def run(self) -> Tag:
        return Tag(name="x")

class Printer(Plugin):
    \"\"\"Print loans.\"\"\"
    settings = Plain
    commands = (PrintLoan,)

class Tagger(Plugin):
    \"\"\"Print tags.\"\"\"
    settings = Plain
    commands = (PrintTag,)
"""

_OWNER = """
from __future__ import annotations
from abc import abstractmethod
from pydantic import BaseModel
from untaped.sdk import Plugin, experimental
from untaped.sdk.contracts import Contract, Issued, Record
from kinds_printer import Tag

class Plain(BaseModel):
    \"\"\"No settings.\"\"\"

class Note(Issued):
    text: str

@experimental
class Notes[T: Record = Note](Contract):
    \"\"\"Notes.\"\"\"
    def to_note(self, item: T) -> Note:
        \"\"\"A provider's record as a note.\"\"\"
        raise NotImplementedError
    @abstractmethod
    def notes(self) -> list[Note]:
        \"\"\"Every note.\"\"\"

@experimental
class Tags[T: Record = Tag](Contract):
    \"\"\"Tags, whose model another plugin's package defines.\"\"\"
    def to_tag(self, item: T) -> Tag:
        \"\"\"A provider's record as a tag.\"\"\"
        raise NotImplementedError
    @abstractmethod
    def tags(self) -> list[Tag]:
        \"\"\"Every tag.\"\"\"

class NoteOwner(Plugin):
    \"\"\"Own notes.\"\"\"
    settings = Plain
    offers = (Notes,)

class TagOwner(Plugin):
    \"\"\"Own tags.\"\"\"
    settings = Plain
    offers = (Tags,)
"""

_LENDER = """
from __future__ import annotations
from pydantic import BaseModel
from untaped.sdk import Plugin
from untaped.sdk.contracts import Provider
from kinds_owner import Note, Notes
from kinds_printer import Loan

class Plain(BaseModel):
    \"\"\"No settings.\"\"\"

class LoanNotes(Notes[Loan], Provider[Plain]):
    def to_note(self, item: Loan) -> Note:
        return Note(text=item.name)
    def notes(self) -> list[Note]:
        return []

class Lender(Plugin):
    \"\"\"Lend loans as notes.\"\"\"
    settings = Plain
    fills = (LoanNotes,)
"""

#: A package whose plugin is broken on its own: two of its records are both ``broken.twin``.
_BROKEN = {
    "a": "from untaped.sdk.contracts import Record\nclass Twin(Record):\n    name: str\n",
    "b": "from untaped.sdk.contracts import Record\nclass Twin(Record):\n    name: str\n",
    "plugin": """
from __future__ import annotations
from abc import abstractmethod
from collections.abc import Iterator
from pydantic import BaseModel
from untaped.sdk import Command, Output, Plugin, experimental
from untaped.sdk.contracts import Contract, Record
from kinds_broken import a, b
from kinds_printer import Tag

class Plain(BaseModel):
    \"\"\"No settings.\"\"\"

@experimental
class Labels[T: Record = Tag](Contract):
    \"\"\"Labels, whose model the printer's package defines.\"\"\"
    def to_tag(self, item: T) -> Tag:
        \"\"\"A provider's record as a tag.\"\"\"
        raise NotImplementedError
    @abstractmethod
    def labels(self) -> list[Tag]:
        \"\"\"Every label.\"\"\"

class PrintA(Command[Plain]):
    \"\"\"Print a twin.\"\"\"
    output: Output[a.Twin]
    def run(self) -> Iterator[a.Twin]:
        yield from ()

class PrintB(Command[Plain]):
    \"\"\"Print the other twin.\"\"\"
    output: Output[b.Twin]
    def run(self) -> Iterator[b.Twin]:
        yield from ()

class Broken(Plugin):
    \"\"\"Offer labels, and print two records of one kind.\"\"\"
    settings = Plain
    offers = (Labels,)
    commands = (PrintA, PrintB)
""",
}


@pytest.fixture
def packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Any]]:
    for name, text in (("printer", _PRINTER), ("owner", _OWNER), ("lender", _LENDER)):
        (tmp_path / f"kinds_{name}.py").write_text(text, encoding="utf-8")
    (tmp_path / "kinds_broken").mkdir()
    (tmp_path / "kinds_broken" / "__init__.py").write_text("", encoding="utf-8")
    for name, text in _BROKEN.items():
        (tmp_path / "kinds_broken" / f"{name}.py").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)

    def composed(*named: tuple[str, str]) -> Any:
        entries = []
        for name, target in named:
            module, _, attr = target.partition(":")
            plugin = getattr(importlib.import_module(module), attr)
            entries.append(PluginEntry(name=name, target=target, plugin=plugin))
        return compose(entries)

    yield composed
    for module in [each for each in sys.modules if each.startswith("kinds_")]:
        sys.modules.pop(module)


@pytest.mark.parametrize("printer_first", [True, False])
def test_a_command_s_record_wins_over_a_provider_s_in_any_order(
    packages: Callable[..., Any], printer_first: bool
) -> None:
    named = [("printer", "kinds_printer:Printer"), ("lend", "kinds_lender:Lender")]
    declaration = packages(
        ("notes", "kinds_owner:NoteOwner"), *(named if printer_first else named[::-1])
    )
    assert sorted(plugin.name for plugin in declaration.plugins) == ["lend", "notes", "printer"]
    assert [kind.kind for kind in declaration.kinds if kind.target == "kinds_printer:Loan"] == [
        "printer.loan"
    ]


@pytest.mark.parametrize("owner_first", [True, False])
def test_the_offering_plugin_names_its_model_in_any_order(
    packages: Callable[..., Any], owner_first: bool
) -> None:
    named = [("tags", "kinds_owner:TagOwner"), ("tagger", "kinds_printer:Tagger")]
    declaration = packages(*(named if owner_first else named[::-1]))
    assert [plugin.name for plugin in declaration.plugins] == ["tags"]
    [refused] = declaration.quarantined
    assert refused.name == "tagger"
    assert "but it is tags.tag" in refused.problems[0].detail


@pytest.mark.parametrize("broken_first", [True, False])
def test_a_plugin_broken_on_its_own_claims_no_kind(
    packages: Callable[..., Any], broken_first: bool
) -> None:
    """broken offers a model of the printer's package but never composes.

    So the printer keeps it, in either order.
    """
    named = [("broken", "kinds_broken.plugin:Broken"), ("tagger", "kinds_printer:Tagger")]
    declaration = packages(*(named if broken_first else named[::-1]))
    assert [plugin.name for plugin in declaration.plugins] == ["tagger"]
    assert [kind.kind for kind in declaration.kinds] == ["tagger.tag"]
    [refused] = declaration.quarantined
    assert refused.name == "broken"
    assert [problem.detail for problem in refused.problems] == [
        "would be the kind broken.twin, which kinds_broken.a:Twin is: rename one"
    ]


def test_a_name_held_by_a_plugin_refused_for_its_kinds_is_free(
    packages: Callable[..., Any],
) -> None:
    declaration = packages(("x", "kinds_broken.plugin:Broken"), ("x", "kinds_printer:Tagger"))
    assert [plugin.target for plugin in declaration.plugins] == ["kinds_printer:Tagger"]
    assert [kind.kind for kind in declaration.kinds] == ["x.tag"]
    [refused] = declaration.quarantined
    assert [problem.rule for problem in refused.problems] == ["duplicate-kind"]


def test_the_command_page_says_it_also_reads_each_provider_s_own_records(
    install: Installer,
) -> None:
    ran = _counter(install).cli("docs counter commands show")
    assert ran.exit == 0, ran.stderr
    assert (
        "Also accepts on stdin each `cards` provider's own records, read through it "
        "(`untaped docs counter extensions cards`)."
    ) in ran.stdout
