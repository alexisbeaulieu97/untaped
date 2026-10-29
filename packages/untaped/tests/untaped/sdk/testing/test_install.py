"""``install``: plugin classes composed into the hermetic home, commands run by instance or line."""

from __future__ import annotations

import json
from abc import abstractmethod
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field, SecretStr

from core_test_support.contracts.support import Book
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import (
    Column,
    Command,
    Confirm,
    Diagnostics,
    Failed,
    Filled,
    Key,
    Output,
    Plugin,
    Positional,
    ProgressHandle,
    Record,
    Refresh,
    Stdin,
    UntapedError,
    experimental,
)
from untaped.sdk._run import NoRunError
from untaped.sdk.contracts import Contract, Ok, Provider, cached, gather
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.storage import Cache
from untaped.sdk.testing import Home, Install, ManualClock, Ran, Running

Installer = Callable[..., Install]


class ShopSettings(BaseModel):
    greeting: str = Field("hello", description="What a greeting says.")
    token: SecretStr | None = Field(None, description="The till's token.")
    max_items: int = Field(1, alias="max-items", description="At most this many.")


class Item(Record):
    """One item on the shelf."""

    name: Annotated[str, Key, Column]
    price: Annotated[int, Column] = 1


class Greet(Command[ShopSettings]):
    """Say the greeting."""

    def run(self) -> str:
        return self.settings.greeting


class Stock(Command[ShopSettings]):
    """List the items named, or piped in."""

    names: Positional[list[str]] = Field(default_factory=list, description="The items.")
    stdin: Stdin[Item]
    output: Output[Item]

    def run(self) -> Iterator[Item]:
        for name in self.stdin.identifiers(self.names):
            yield Item(name=name)


class Remove(Command):
    """Remove an item, asking first."""

    name: Positional[str] = Field(description="The item.")
    confirm: Confirm

    def run(self) -> str:
        return "removed" if self.confirm(f"Remove {self.name}?") else "planned"


PRICES: Cache[Any] = Cache(label="prices", max_age=timedelta(hours=6))


class Prices(Command):
    """Print how many times the prices were fetched."""

    refresh: Refresh = Refresh(PRICES)

    def run(self) -> str:
        served = PRICES.serve(
            {"all": "all"}, refresh=self.refresh.directive, fill=_fill, read=_read
        )
        return str(served.value)


FETCHED: list[int] = []


def _fill(_key: str, _task: ProgressHandle) -> Filled:
    FETCHED.append(1)
    return Filled(summary="fetched")


def _read() -> int:
    return len(FETCHED)


class Till(Command[ShopSettings]):
    """Print what the till was set up with."""

    def run(self) -> str:
        token = self.settings.token
        return f"{None if token is None else token.get_secret_value()} {self.settings.max_items}"


class Sold(Command):
    """List what sold, then fail."""

    output: Output[Item]

    def run(self) -> Iterator[Item]:
        yield Item(name="tea")
        raise Failed("the till jammed")


class Broken(Command):
    """Crash."""

    def run(self) -> str:
        return {}["x"]  # type: ignore[no-any-return]


class Listing(BaseModel):
    """How the shelf is listed."""

    output: Output[Item]


class Shelf(Command):
    """List the shelf."""

    listing: Listing | None = None

    def run(self) -> Iterator[Item]:
        yield Item(name="tea")


class Shop(Plugin):
    """Sell things (a test's plugin)."""

    settings = ShopSettings
    commands = (Greet, Stock, Remove, Prices, Till, Sold, Broken, Shelf)
    prices = PRICES


@pytest.fixture(autouse=True)
def _no_fetches() -> None:
    FETCHED.clear()


def test_run_uses_the_settings_the_test_gave(install: Installer) -> None:
    ran = install(Shop, settings=ShopSettings(greeting="hi")).run(Greet())
    assert (ran.exit, ran.text, ran.stdout) == (0, "hi", "hi\n")
    assert install(Shop).run(Greet()).text == "hello"


def test_run_keeps_the_rows_as_objects(install: Installer) -> None:
    ran = install(Shop).run(Stock(names=["tea", "jam"]))
    assert ran.rows_as(Item) == [Item(name="tea"), Item(name="jam")]
    assert "tea" in ran.stdout  # a table, as a user would see it


def test_cli_reads_json_and_pipe_rows_back_and_refuses_a_table(install: Installer) -> None:
    shop = install(Shop)
    assert shop.cli("shop stock tea --format json").rows_as(Item) == [Item(name="tea")]
    piped = shop.cli("shop stock tea jam --format pipe")
    assert [row.name for row in piped.rows_as(Item)] == ["tea", "jam"]
    again = shop.cli(["shop", "stock", "--stdin", "-f", "json"], input=piped.stdout)
    assert [row.name for row in again.rows_as(Item)] == ["tea", "jam"]
    with pytest.raises(TypeError, match="printed as --format table"):
        shop.cli("shop stock tea").rows_as(Item)
    with pytest.raises(TypeError, match="no command of a plugin installed"):
        shop.cli("--version").rows_as(Item)
    with pytest.raises(TypeError, match=r"nothing was printed \(exit 2\)"):
        shop.cli("shop stock --nope -f json").rows_as(Item)


def test_stdin_takes_rows_or_bare_lines(install: Installer) -> None:
    shop = install(Shop)
    rows = shop.run(Stock(), stdin=[Item(name="tea", price=3), Item(name="jam")])
    assert [row.name for row in rows.rows_as(Item)] == ["tea", "jam"]
    lines = shop.run(Stock(), stdin=["tea", "jam"], format="json")
    assert [row.name for row in lines.rows_as(Item)] == ["tea", "jam"]


def test_run_answers_yes_unless_told_otherwise(install: Installer) -> None:
    shop = install(Shop)
    assert shop.run(Remove(name="tea")).text == "removed"
    declined = shop.run(Remove(name="tea"), answer=False)
    assert declined.exit == 1
    assert any("cancelled" in line for line in declined.stderr)
    assert shop.run(Remove(name="tea"), dry_run=True).text == "planned"


def test_cli_meets_the_absent_terminal(install: Installer) -> None:
    assert install(Shop).cli("shop remove tea").exit == 2


def test_a_knob_for_a_field_the_command_lacks_is_a_type_error(install: Installer) -> None:
    with pytest.raises(TypeError, match="Greet declares no confirm field"):
        install(Shop).run(Greet(), answer=False)
    with pytest.raises(TypeError, match="unexpected knob 'colour'"):
        install(Shop).run(Greet(), colour="red")


def test_an_error_is_the_exit_code_unless_asked_to_raise(install: Installer) -> None:
    shop = install(Shop)
    failed = shop.run(Stock())
    assert failed.exit == 2
    assert any("at least one identifier" in line for line in failed.stderr)
    with pytest.raises(Exception, match="at least one identifier"):
        shop.run(Stock(), raise_on_error=True)


def test_the_clock_ages_the_caches(install: Installer, clock: ManualClock) -> None:
    shop = install(Shop)
    assert shop.run(Prices()).text == "1"
    clock.advance(hours=5)
    assert shop.run(Prices()).text == "1"
    clock.advance(hours=2)
    assert shop.run(Prices()).text == "2"
    assert shop.run(Prices(), refresh=True).text == "3"  # a stopped clock still refreshes


def test_settings_of_no_installed_plugin_are_refused(install: Installer) -> None:
    class Other(BaseModel):
        x: int = 1

    with pytest.raises(TypeError, match="Other is the settings of no plugin installed"):
        install(Shop, settings=Other())


def test_a_test_plugin_is_named_after_its_class_unless_named(install: Installer) -> None:
    assert install(Shop).cli("shop greet").stdout == "hello\n"
    assert install(Shop, names={Shop: "store"}).cli("store greet").stdout == "hello\n"


def test_home_is_the_hermetic_home(home: Home) -> None:
    assert home.path == Path.home() / ".untaped"
    assert home.plugin("shop") == home.path / "plugins" / "shop"


def test_ran_is_one_type_for_both_spellings(install: Installer) -> None:
    shop = install(Shop)
    assert isinstance(shop.run(Greet()), Ran)
    assert isinstance(shop.cli("shop greet"), Ran)


def test_running_records_the_lines_shown_inside_as_ran_does(
    install: Installer, capsys: pytest.CaptureFixture[str]
) -> None:
    shop = install(Shop)
    Diagnostics().warning("before")  # outside the block: shown, not recorded
    with shop.running() as run:
        assert isinstance(run, Running)
        Diagnostics().warning("low stock")
        assert [each.message for each in run.diagnostics] == ["low stock"]
    Diagnostics().warning("after")
    assert [each.message for each in run.diagnostics] == ["low stock"]
    assert type(run.diagnostics) is type(shop.run(Greet()).diagnostics)
    assert "warning: low stock" in capsys.readouterr().err  # a tee: still shown


def test_settings_are_written_by_alias_and_secrets_in_clear(install: Installer) -> None:
    settings = ShopSettings.model_validate({"token": "s3cret", "max-items": 5})
    assert install(Shop, settings=settings).run(Till()).text == "s3cret 5"


def test_core_keys_in_a_section_stay_when_settings_change(install: Installer) -> None:
    assert install(Shop).cli("config set shop.caches.prices.max_age_seconds 0").exit == 0
    install(Shop, settings=ShopSettings(greeting="hi"))
    shown = install(Shop).cli("config get shop.caches.prices.max_age_seconds")
    assert shown.stdout.strip() == "0"


def test_a_crash_is_the_tests_error(install: Installer) -> None:
    with pytest.raises(KeyError):
        install(Shop).run(Broken())
    with pytest.raises(KeyError):
        install(Shop).cli("shop broken")


def test_failed_after_rows_keeps_the_rows(install: Installer) -> None:
    shop = install(Shop)
    ran = shop.run(Sold())
    assert (ran.exit, ran.rows_as(Item)) == (1, [Item(name="tea")])
    line = shop.cli("shop sold --format json")
    assert (line.exit, line.rows_as(Item)) == (1, [Item(name="tea")])


def test_an_optional_bundle_is_built_around_its_core_field(install: Installer) -> None:
    ran = install(Shop).run(Shelf(), format="json")
    assert (ran.exit, ran.rows_as(Item)) == (0, [Item(name="tea")])
    assert '"name": "tea"' in ran.stdout


def test_cli_reads_rows_back_whatever_spelled_the_format(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    shop = install(Shop)
    assert shop.cli("--verbose shop stock tea -f json").rows_as(Item) == [Item(name="tea")]
    monkeypatch.setenv("UNTAPED_FORMAT", "json")
    assert shop.cli("shop stock tea").rows_as(Item) == [Item(name="tea")]


def test_cli_goes_ahead_with_yes(install: Installer) -> None:
    ran = install(Shop).cli("shop remove tea --yes")
    assert (ran.exit, ran.text) == (0, "removed")


class Pages(BaseModel):
    """The owner of the contract the providers below fill (``books``)."""


@experimental
class Books(Contract):
    """Where the test's books come from."""

    @abstractmethod
    def books(self) -> list[Book]: ...


class Titles(Command[Pages]):
    """Print every title every provider keeps."""

    def run(self) -> str:
        return ",".join(
            f"{answer.plugin}:{book.title}"
            for answer in gather(Books.books)()
            if isinstance(answer, Ok)
            for book in answer.value
        )


class Reader(Plugin):
    """Ask for books."""

    settings = Pages
    commands = (Titles,)
    offers = (Books,)


class Fixed(Books, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]


class Plain(Books):
    def books(self) -> list[Book]:
        return []


def test_providers_compose_as_one_plugin_per_key(install: Installer) -> None:
    shelf = install(Reader, providers={"fake": (Fixed,), "spare": (Fixed,)})
    assert shelf.run(Titles()).text == "fake:Dune,spare:Dune"


def test_a_providers_plugin_is_never_listed_as_core_s_package(install: Installer) -> None:
    listed = install(Reader, providers={"fake": (Fixed,)}).cli("plugin list -f json")
    fake = next(row for row in json.loads(listed.stdout) if row["name"] == "fake")
    assert not (fake["package"] or "").startswith("untaped.")


def test_one_name_installed_again_with_other_providers_runs_the_new_ones(
    install: Installer,
) -> None:
    class Other(Books, Provider):
        def books(self) -> list[Book]:
            return [Book(title="Emma")]

    first = install(Reader, providers={"fake": (Fixed,)}).cli("reader titles")
    again = install(Reader, providers={"fake": (Other,)}).cli("reader titles")
    assert (first.text, again.text) == ("fake:Dune", "fake:Emma")


def test_providers_must_be_provider_classes(install: Installer) -> None:
    with pytest.raises(TypeError, match=r"providers\['fake'\] holds .*Plain.*not a provider"):
        install(Reader, providers={"fake": (Plain,)})
    with pytest.raises(TypeError, match=r"providers\['fake'\] holds no provider class"):
        install(Reader, providers={"fake": ()})


class Shelved(BaseModel):
    """What a test's provider reads."""

    title: str = "Dune"


class Configured(Books, Provider[Shelved]):
    def books(self) -> list[Book]:
        return [Book(title=self.settings.title)]


@experimental
class Shelves(Contract):
    """A second contract, so one test plugin fills two."""

    @abstractmethod
    def shelves(self) -> list[Book]: ...


class Racks(Command[Pages]):
    """Print every shelf every provider keeps."""

    def run(self) -> str:
        return ",".join(
            f"{answer.plugin}:{book.title}"
            for answer in gather(Shelves.shelves)()
            if isinstance(answer, Ok)
            for book in answer.value
        )


class Library(Plugin):
    """Ask for books and shelves."""

    settings = Pages
    commands = (Titles, Racks)
    offers = (Books, Shelves)


class Bare(Shelves, Provider):
    def shelves(self) -> list[Book]:
        return [Book(title="A1")]


class Other(Books, Provider[Pages]):
    def books(self) -> list[Book]:
        return []


def test_providers_reading_settings_get_the_section_the_test_writes(install: Installer) -> None:
    shelf = install(Reader, providers={"fake": (Configured,)}, settings=[Shelved(title="Emma")])
    assert shelf.run(Titles()).text == "fake:Emma"
    both = install(
        Library, providers={"fake": (Configured, Bare)}, settings=[Shelved(title="Emma")]
    )
    assert (both.run(Titles()).text, both.run(Racks()).text) == ("fake:Emma", "fake:A1")
    with pytest.raises(TypeError, match=r"providers\['fake'\] read different settings"):
        install(Reader, providers={"fake": (Configured, Other)})


def test_an_installed_plugin_brings_the_owner_it_declares_under_its_extra(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime import registry

    owner = PluginCandidate(distribution="untaped-reader", name="reader", target=_target(Reader))
    provider = PluginCandidate(
        distribution="untaped-lender",
        name="lender",
        target=_target(Lender),
        requires_dist=('untaped-reader>=1; extra == "reader"', 'untaped-gone; extra == "gone"'),
    )
    monkeypatch.setattr(registry, "discover_candidates", lambda: (owner, provider))
    lender = install(Lender, settings=[Pages()])  # the owner's section is writable too
    assert [candidate.name for candidate in lender.candidates] == ["lender", "reader"]
    assert lender.cli("reader titles").stdout == "lender:Dune\n"


def test_a_brought_plugin_that_fails_to_import_is_composition_s_to_report(
    install: Installer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from untaped.runtime import registry

    (tmp_path / "untaped_boom.py").write_text('raise RuntimeError("boom")\n')
    monkeypatch.syspath_prepend(tmp_path)
    boom = PluginCandidate(distribution="untaped-boom", name="boom", target="untaped_boom:SPEC")
    provider = PluginCandidate(
        distribution="untaped-lender",
        name="lender",
        target=_target(Lender),
        requires_dist=('untaped-boom>=1; extra == "boom"',),
    )
    monkeypatch.setattr(registry, "discover_candidates", lambda: (boom, provider))
    lender = install(Lender)  # reading boom's settings raises nothing here
    assert [candidate.name for candidate in lender.candidates] == ["lender", "boom"]


class Lender(Plugin):
    """Lend books to reader."""

    settings = ShopSettings
    fills = (Fixed,)


def _target(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


class Named(Books, Provider):
    """Its one book is titled after the plugin its context says it is."""

    def books(self) -> list[Book]:
        assert not hasattr(self.ctx, "terminal")  # only the command asks
        return [Book(title=self.ctx.plugin.name)]


def test_a_provider_s_context_is_its_own_plugin_s_without_a_terminal(install: Installer) -> None:
    assert install(Reader, providers={"fake": (Named,)}).run(Titles()).text == "fake:fake"
    with pytest.raises(UntapedError, match="not bound to a plugin, so it has no context"):
        Named().ctx  # noqa: B018 - reading it is the point


@experimental
class Logs(Contract):
    """A contract whose one method keeps its answers."""

    @cached(max_age=timedelta(hours=1), label="logs")
    @abstractmethod
    def entries(self) -> list[Book]: ...


class Tail(Command[Pages]):
    """Print every entry every provider keeps."""

    def run(self) -> str:
        return ",".join(
            book.title
            for answer in gather(Logs.entries)()
            if isinstance(answer, Ok)
            for book in answer.value
        )


class Logbook(Plugin):
    """Keep logs."""

    settings = Pages
    commands = (Tail,)
    offers = (Logs,)


class Written(Logs, Provider):
    def entries(self) -> list[Book]:
        return [Book(title="boot")]


def test_an_offered_answer_cache_is_the_owner_s_under_the_contract_s_name(
    install: Installer,
) -> None:
    assert install(Logbook, providers={"fake": (Written,)}).run(Tail()).text == "boot"
    info = contract_of(Logs)
    assert info is not None
    cache = info.methods["entries"].cache
    assert cache is not None
    assert cache.bindings == ()  # the contract names it; the class is never changed
    with pytest.raises(NoRunError):
        cache.plugin  # noqa: B018 - outside a run no plugin owns it
    with install(Logbook).running():
        assert (cache.plugin, cache.name) == ("logbook", "logs_entries")
    # the name is the run's, never kept from an earlier one
    renamed = install(Logbook, names={Logbook: "journal"}, providers={"other": (Written,)})
    assert renamed.run(Tail()).text == "boot"
    with renamed.running():
        assert cache.plugin == "journal"
