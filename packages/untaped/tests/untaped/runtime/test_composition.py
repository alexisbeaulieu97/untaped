"""Composition: one walk from plugin classes to the declaration, each problem naming its class."""

from __future__ import annotations

import enum
import itertools
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from core_test_support.contracts.support import BOOKS, Book, BookSource, Shop
from untaped.runtime.composition import PluginEntry, bundle_panel, compose, kebab, snake, words
from untaped.runtime.declaration import AliasDecl, CommandDecl, Declaration, GroupDecl, Problem
from untaped.sdk import (
    Cache,
    Column,
    Command,
    Confirm,
    Credential,
    CredentialKey,
    Deprecated,
    DeprecatedBehaviour,
    Document,
    DryRun,
    File,
    Following,
    Group,
    Key,
    KeyValue,
    Output,
    Parallel,
    Plugin,
    Positional,
    Refresh,
    Short,
    Stdin,
    StubSkill,
    TextOrStdin,
    Written,
    deprecated,
    deprecated_behaviour,
    experimental,
    key,
)
from untaped.sdk._block import Block
from untaped.sdk.contracts import Contract, Provider, cached

PACKAGE = __name__.partition(".")[0]


class HelloKey(CredentialKey):
    name: str = "hello"


class HelloSettings(BaseModel):
    greeting: str = Field("hello", description="What say prints.")


class Salutation(BaseModel):
    name: Annotated[str, Key, Column]
    text: str


class Suite(BaseModel):
    """A test suite."""

    name: str


class Selection(BaseModel):
    """How the names are picked: every one, or those given."""

    every: bool = Field(False, description="Greet everyone.")


class Tone(enum.Enum):
    """How warm the greeting sounds."""

    WARM = "warm"
    COOL = "cool"


class Say(Command[HelloSettings]):
    """Greet each name.

    Prints one salutation per name.
    """

    names: Positional[list[str]] = Field(default_factory=list, description="Whom to greet.")
    suites: list[File[Suite]] = Field(default_factory=list, description="Suites to read.")
    report: Annotated[File[Suite] | None, Written] = Field(None, description="Where to write.")
    selection: Selection
    tone: Tone = Field(Tone.WARM, description="The tone.")
    level: Literal[1, 2] = Field(1, description="How loud.")
    loud: bool = Field(True, description="Shout.")
    repo: Annotated[str, Short("-r")] = Field("main", description="The repository.")
    labels: list[KeyValue] = Field(default_factory=list, description="Labels.")
    stdin: Stdin[Salutation]
    output: Output[Salutation]
    confirm: Confirm

    def run(self) -> Iterator[Salutation]:
        yield from ()


class Version(Command):
    """Print the version."""

    def run(self) -> str:
        return "1"


class ProfileGroup(Group):
    """Manage profiles."""

    commands = (Version,)
    runs = Version


class Hello(Plugin):
    """Say hello.

    A plugin for tests.
    """

    settings = HelloSettings
    stability = experimental
    commands = (Say, ProfileGroup)
    runs = Version
    skill = StubSkill("Uses hello. Use when greeting.")


def _entry(plugin: object, name: str = "hello", module: str = __name__) -> PluginEntry:
    return PluginEntry(name, f"{module}:{getattr(plugin, '__qualname__', plugin)}", plugin)


def _problems(plugin: object, name: str = "hello") -> list[Problem]:
    declaration = compose([_entry(plugin, name)])
    assert not declaration.plugins
    [refused] = declaration.quarantined
    return list(refused.problems)


def _rules(plugin: object) -> list[tuple[str, str]]:
    """``(rule, where)`` of each problem, ``where`` without the test function's ``<locals>``."""
    return [(p.rule, p.where.rpartition("<locals>.")[2]) for p in _problems(plugin)]


def _plugin_with(*commands: type[Command] | type[Group]) -> type[Plugin]:
    class Demo(Plugin):
        """Demo."""

        settings = HelloSettings

    Demo.commands = commands
    return Demo


@pytest.fixture(scope="module")
def declaration() -> Declaration:
    return compose([_entry(Hello)])


def test_names_come_from_class_names() -> None:
    assert words("SSHKeys") == ["ssh", "keys"]
    assert kebab("JobTemplates") == "job-templates"
    assert snake("TestResult") == "test_result"


def test_a_plugin_composes_from_its_class(declaration: Declaration) -> None:
    assert declaration.quarantined == ()
    plugin = declaration.plugin("hello")
    assert plugin.target == f"{__name__}:Hello"
    assert plugin.package == PACKAGE
    assert (plugin.summary, plugin.doc) == ("Say hello.", "Say hello.\n\nA plugin for tests.")
    assert plugin.settings == f"{__name__}:HelloSettings"
    assert plugin.stability is not None
    assert plugin.stability.stability == "experimental"
    assert plugin.skill == "Uses hello. Use when greeting."
    assert plugin.runs is not None
    assert plugin.runs.path == ("hello",)


def test_commands_and_groups_take_kebab_names(declaration: Declaration) -> None:
    say, profile = declaration.plugin("hello").commands
    assert isinstance(say, CommandDecl)
    assert say.path == ("hello", "say")
    assert isinstance(profile, GroupDecl)
    assert profile.path == ("hello", "profile")
    assert [c.path for c in profile.commands] == [("hello", "profile", "version")]


def test_a_command_declares_what_it_reads_and_prints(declaration: Declaration) -> None:
    say = next(c for c in declaration.commands() if c.name == "say")
    assert say.summary == "Greet each name."
    assert say.settings == "hello"
    assert say.returns == "rows"
    assert say.output == "hello.salutation"
    assert say.formats == ("table", "json", "yaml", "pipe", "raw")
    assert say.stdin == (f"{__name__}:Salutation",)
    assert say.inputs == ("hello.suite",)
    assert say.writes == ("hello.suite",)
    assert say.guides == (f"{__name__}:Selection", f"{__name__}:Tone")
    assert say.asks_first


def test_options_carry_what_the_parser_and_help_need(declaration: Declaration) -> None:
    say = next(c for c in declaration.commands() if c.name == "say")
    options = {option.field: option for option in say.options if option.block is None}
    names = options["names"]
    assert (names.positional, names.flag, names.token) == (True, None, "list[str]")
    assert options["selection.every"].panel == "Selection"
    assert options["selection.every"].flag == "--every"
    assert options["tone"].choices == ("warm", "cool")
    assert options["tone"].default == "warm"
    assert (options["level"].token, options["level"].choices) == ("int", ("1", "2"))
    assert options["loud"].negative == "--no-loud"
    assert options["repo"].short == "-r"
    assert options["labels"].token == "list[str]"
    assert options["suites"].token == "list[str]"
    flags = {option.flag for option in say.options if option.block is not None}
    assert {"--format", "--columns", "--stdin", "--yes", "--dry-run"} <= flags
    fmt = next(option for option in say.options if option.flag == "--format")
    assert (fmt.block, fmt.panel, fmt.short) == ("output", "Output", "-f")


def test_a_bundle_s_panel_is_its_class_name_as_words() -> None:
    class SourceData(BaseModel):
        pass

    class HTTPProxy(BaseModel):
        pass

    assert bundle_panel(SourceData) == "Source data"
    assert bundle_panel(HTTPProxy) == "HTTP proxy"
    assert bundle_panel(Selection) == "Selection"


def test_kinds_are_the_plugin_and_the_snake_case_class(declaration: Declaration) -> None:
    assert [(kind.kind, kind.plugin) for kind in declaration.kinds] == [
        ("hello.salutation", "hello"),
        ("hello.suite", "hello"),
    ]


def test_a_block_on_the_plugin_class_is_named_by_its_attribute() -> None:
    class Thing(Block):
        block_kind = "thing"
        name_style = "kebab"

    class Stocked(Plugin):
        """Stocked."""

        settings = HelloSettings
        repo_store = Thing()

    [block] = compose([_entry(Stocked)]).plugin("hello").blocks
    assert (block.block, block.attribute, block.name) == ("thing", "repo_store", "repo-store")
    assert block.module == __name__


def test_a_block_name_its_kind_refuses_is_a_problem() -> None:
    class Shouty(Plugin):
        """Shouty."""

        settings = HelloSettings
        MOTD = Cache(label="message of the day", max_age=timedelta(hours=1))

    [problem] = _problems(Shouty)
    assert (problem.rule, problem.where.rpartition("<locals>.")[2]) == ("block-name", "Shouty.MOTD")
    assert "must be one settings segment" in problem.detail


def test_a_plugin_class_names_its_blocks_itself() -> None:
    class Spelled(Plugin):
        """Spelled."""

        settings = HelloSettings
        other = Cache("hello", "motd", label="message of the day", max_age=timedelta(hours=1))

    [problem] = _problems(Spelled)
    assert (problem.rule, problem.where.rpartition("<locals>.")[2]) == (
        "block-name",
        "Spelled.other",
    )
    assert "is named hello.motd by hand" in problem.detail


def test_every_owned_block_is_named_by_the_plugin_class() -> None:
    class Spelled(Plugin):
        """Spelled."""

        settings = HelloSettings
        token = Credential("hello", "token", key=HelloKey)
        old = deprecated_behaviour("old-way", removed_in=13)
        Tok = Credential(key=HelloKey)
        Gone = DeprecatedBehaviour(removed_in=13)

    assert sorted(_rules(Spelled)) == [
        ("block-name", "Spelled.Gone"),
        ("block-name", "Spelled.Tok"),
        ("block-name", "Spelled.old"),
        ("block-name", "Spelled.token"),
    ]


def test_one_block_held_twice_is_a_problem() -> None:
    class Thing(Block):
        block_kind = "thing"

    thing = Thing()

    class Twice(Plugin):
        """Twice."""

        settings = HelloSettings
        a = thing
        b = thing

    assert _rules(Twice) == [("duplicate-block", "Twice.b")]


def test_a_stub_skill_belongs_under_skill() -> None:
    class Misplaced(Plugin):
        """Misplaced."""

        settings = HelloSettings
        teach = StubSkill("Uses it.")

    assert _rules(Misplaced) == [("reserved-name", "Misplaced.teach")]


def test_a_name_already_taken_is_quarantined() -> None:
    declaration = compose([_entry(Hello, "config")], taken=("config",))
    assert [p.rule for q in declaration.quarantined for p in q.problems] == ["duplicate-name"]
    twice = compose([_entry(Hello), _entry(Hello)])
    assert [p.name for p in twice.plugins] == ["hello"]
    assert [q.name for q in twice.quarantined] == ["hello"]


def test_an_entry_that_is_not_a_plugin_class() -> None:
    assert [p.rule for p in _problems(HelloSettings)] == ["not-a-plugin"]


def test_a_plugin_class_in_a_package_init_is_refused() -> None:
    declaration = compose([PluginEntry("hello", "untaped.sdk:Hello", Hello)])
    assert [p.rule for q in declaration.quarantined for p in q.problems] == ["plugin-module"]


def test_settings_is_required_and_a_model() -> None:
    class NoSettings(Plugin):
        """No settings."""

    class WrongSettings(Plugin):
        """Wrong settings."""

        settings = dict  # type: ignore[assignment]

    assert _rules(NoSettings) == [("reserved-name", "NoSettings.settings")]
    assert _rules(WrongSettings) == [("reserved-name", "WrongSettings.settings")]


def test_a_group_s_runs_is_declared_under_the_group_s_words(declaration: Declaration) -> None:
    _, profile = declaration.plugin("hello").commands
    assert isinstance(profile, GroupDecl)
    assert profile.runs is not None
    assert profile.runs.path == ("hello", "profile")
    assert profile.runs in declaration.commands()


def test_a_group_s_runs_holds_a_command() -> None:
    class Muddled(Group):
        """Muddled."""

        runs = HelloSettings  # type: ignore[assignment]

    assert _rules(_plugin_with(Muddled)) == [("reserved-name", "Muddled.runs")]


def test_reserved_names_hold_what_they_name() -> None:
    class Muddled(Plugin):
        settings = HelloSettings
        commands = [Version]  # type: ignore[assignment]  # noqa: RUF012 - the wrong shape on purpose
        stability = "beta"  # type: ignore[assignment]
        skill = "teach"  # type: ignore[assignment]
        runs = HelloSettings  # type: ignore[assignment]
        offers = (Shop,)
        fills = (BookSource,)

    assert _rules(Muddled) == [
        ("reserved-name", "Muddled.commands"),
        ("reserved-name", "Muddled.stability"),
        ("reserved-name", "Muddled.skill"),
        ("reserved-name", "Muddled.runs"),
        ("reserved-name", "Muddled.offers"),
        ("reserved-name", "Muddled.fills"),
        ("missing-docstring", "Muddled"),
    ]


def test_a_command_list_holds_only_commands_and_groups() -> None:
    assert _rules(_plugin_with(HelloSettings)) == [("reserved-name", "Demo.commands")]  # type: ignore[arg-type]


def test_two_commands_with_one_name() -> None:
    class Other(Group):
        """Other."""

        commands = (Version, Version)

    assert _rules(_plugin_with(Other)) == [("duplicate-command", "Other.commands")]


def test_a_command_reads_its_plugins_settings_only() -> None:
    class Elsewhere(BaseModel):
        pass

    class Peek(Command[Elsewhere]):
        """Peek."""

        def run(self) -> None:
            return None

    assert [p.rule for p in _problems(_plugin_with(Peek))] == ["settings-model"]


def test_run_is_defined_and_annotated() -> None:
    class NoRun(Command):
        """No run."""

    class Untyped(Command):
        """Untyped."""

        def run(self):  # type: ignore[no-untyped-def]
            return None

    class Odd(Command):
        """Odd."""

        def run(self) -> int:
            return 1

    problems = _problems(_plugin_with(NoRun, Untyped, Odd))
    assert [p.rule for p in problems] == ["missing-run", "missing-return", "output-return"]
    assert problems[1].where.endswith("Untyped.run")


def test_records_need_their_output_field_and_back() -> None:
    class NoOutput(Command):
        """No output."""

        def run(self) -> Salutation:
            return Salutation(name="a", text="b")

    class NoRecords(Command):
        """No records."""

        output: Output[Salutation]

        def run(self) -> str:
            return ""

    class Mismatch(Command):
        """Mismatch."""

        output: Output[Salutation]

        def run(self) -> Iterator[Suite]:
            yield from ()

    problems = _problems(_plugin_with(NoOutput, NoRecords, Mismatch))
    assert [(p.rule, p.where.rpartition(".")[2]) for p in problems] == [
        ("output-return", "NoOutput"),
        ("output-return", "NoRecords"),
        ("output-return", "Mismatch"),
    ]


class Thread(BaseModel):
    name: Annotated[str, Key, Column]
    replies: Annotated[list[Salutation] | None, Following] = None
    topic: Annotated[str, Following] = ""


def test_a_following_field_is_a_list_of_models() -> None:
    class Show(Command):
        """Show."""

        output: Output[Thread]

        def run(self) -> Thread:
            return Thread(name="a")

    problems = _problems(_plugin_with(Show))
    assert [(p.rule, p.where.rpartition(".")[2]) for p in problems] == [("field-type", "output")]
    assert "Thread.topic is marked Following" in problems[0].detail


def test_a_subclass_of_the_returned_model_is_its_output() -> None:
    class Loud(Salutation):
        pass

    class Shout(Command):
        """Shout."""

        output: Output[Loud]

        def run(self) -> Salutation | None:
            return None

    declaration = compose([_entry(_plugin_with(Shout))])
    assert declaration.quarantined == ()
    assert declaration.commands()[0].returns == "record-or-none"


def test_a_document_mark_makes_its_model_an_input_core_never_reads() -> None:
    class Load(Command):
        """Load a suite by name or path."""

        suite: Annotated[Positional[str], Document(Suite)] = Field(
            description="A suite name, or a path to one."
        )

        def run(self) -> None:
            return None

    declaration = compose([_entry(_plugin_with(Load))])
    assert declaration.quarantined == ()
    (load,) = declaration.commands()
    assert load.inputs == ("hello.suite",)
    (suite,) = (option for option in load.options if option.block is None)
    assert (suite.positional, suite.token, suite.reads_file) == (True, "str", False)


def test_field_problems_name_the_field() -> None:
    class Messy(Command):
        """Messy."""

        ctx: int = Field(1, description="Reserved.")
        nodoc: str = "x"
        force: Annotated[bool, Short("-f")] = Field(False, description="Clashes with --format.")
        blob: bytes = Field(b"", description="No option of bytes.")
        nested: list[Confirm] = Field(default_factory=list, description="A block inside.")
        repo: Annotated[str, Short("-r")] = Field("", description="The repository.")
        remote: Annotated[str, Short("-r")] = Field("", description="The remote.")
        output: Output[Salutation]
        again: Output[Salutation]

        def run(self) -> Iterator[Salutation]:
            yield from ()

    problems = _problems(_plugin_with(Messy))
    assert sorted((p.rule, p.where.rpartition(".")[2]) for p in problems) == [
        ("core-short", "force"),
        ("duplicate-core-field", "again"),
        ("duplicate-field", "output"),
        ("duplicate-field", "remote"),
        ("field-type", "blob"),
        ("field-type", "nested"),
        ("missing-description", "nodoc"),
        ("reserved-field", "ctx"),
    ]


def test_a_dry_run_beside_a_confirmation_is_one_flag_twice() -> None:
    class Both(Command):
        """Both."""

        confirm: Confirm
        dry_run: DryRun

        def run(self) -> None:
            pass

    problems = _problems(_plugin_with(Both))
    assert [
        (p.rule, p.where.rpartition(".")[2], p.detail.endswith("Both.confirm's")) for p in problems
    ] == [("duplicate-field", "dry_run", True)]


def test_marks_on_a_command_reach_its_declaration() -> None:
    class Mend(Command):
        """Mend."""

        repairs = True
        dry_run: DryRun

        def run(self) -> None:
            pass

    class Broken(Command):
        """Broken."""

        repairs = "yes"  # type: ignore[assignment]

        def run(self) -> None:
            pass

    (mend,) = compose([_entry(_plugin_with(Mend))]).commands()
    assert (mend.repairs, mend.asks_first) == (True, False)
    assert [o.flag for o in mend.options] == ["--dry-run"]
    problems = _problems(_plugin_with(Broken))
    assert [(p.rule, p.where.rpartition(".")[2]) for p in problems] == [
        ("reserved-name", "repairs")
    ]


def test_arguments_must_fill_in_field_order() -> None:
    class Copy(Command):
        """Copy."""

        sources: Positional[list[str]] = Field(description="What to copy.")
        target: Positional[str] = Field(description="Where to.")

        def run(self) -> None:
            pass

    class Move(Command):
        """Move."""

        source: Positional[str] = Field("", description="What to move.")
        target: Positional[str] = Field(description="Where to.")

        def run(self) -> None:
            pass

    class Link(Command):
        """Link."""

        target: Positional[str] = Field(description="Where to.")
        names: Positional[list[str]] = Field(default_factory=list, description="What to link.")

        def run(self) -> None:
            pass

    class Where(BaseModel):
        place: Positional[str] = Field(description="Where from.")

    class Send(Command):
        """Send."""

        where: Where | None = None
        target: Positional[str] = Field(description="Where to.")

        def run(self) -> None:
            pass

    assert sorted(_rules(_plugin_with(Copy, Move, Link, Send))) == [
        ("positional-order", "Copy.target"),
        ("positional-order", "Move.target"),
        ("positional-order", "Send.target"),
    ]


def test_an_unresolved_field_type_quarantines_the_plugin() -> None:
    class Later(Command):
        """Later."""

        missing: Undefined = Field(description="Never defined.")  # type: ignore[name-defined]  # noqa: F821

        def run(self) -> None:
            return None

    assert [p.rule for p in _problems(_plugin_with(Later))] == ["unresolved-type"]


def test_refresh_names_a_held_cache_or_a_cached_contract_method() -> None:
    held = Cache(label="books", max_age=timedelta(hours=1))

    class Books(Command):
        """Books."""

        refresh: Refresh = Refresh(held, BookSource.books)

        def run(self) -> None:
            return None

    class Shelf(Plugin):
        """Shelf."""

        settings = HelloSettings
        commands = (Books,)
        offers = (BookSource,)
        books = held

    assert compose([_entry(Shelf)]).quarantined == ()

    class Bare(Command):
        """Bare."""

        refresh: Refresh

        def run(self) -> None:
            return None

    class Stray(Command):
        """Stray."""

        refresh: Refresh = Refresh(BOOKS)

        def run(self) -> None:
            return None

    assert [p.rule for p in _problems(_plugin_with(Bare, Stray))] == [
        "refresh-cache",
        "refresh-cache",
    ]

    marker = object()

    class Odd(Command):
        """Odd."""

        refresh: Refresh = Refresh(marker)

        def run(self) -> None:
            return None

    class Holder(Plugin):
        """Holder."""

        settings = HelloSettings
        commands = (Odd,)
        held = marker

    [problem] = _problems(Holder)
    assert (problem.rule, "neither a cache" in problem.detail) == ("refresh-cache", True)


def test_refresh_refuses_a_version_only_cache_even_on_the_plugin_class() -> None:
    held = Cache(
        label="composed", max_age=None, model=HelloSettings, version=lambda: "1", split="none"
    )

    class Again(Command):
        """Again."""

        refresh: Refresh = Refresh(held)

        def run(self) -> None:
            return None

    class Demo(Plugin):
        """Demo."""

        settings = HelloSettings
        commands = (Again,)
        composed = held

    [problem] = _problems(Demo)
    assert (problem.rule, "version-only" in problem.detail) == ("refresh-cache", True)


def _refresh_help(plugin: type[Plugin], name: str = "hello") -> str:
    [command] = compose([_entry(plugin, name)]).commands()
    [option] = [option for option in command.options if option.block == "refresh"]
    return option.help


def test_refresh_help_names_the_cache_its_label_and_its_setting() -> None:
    motd = Cache(label="message of the day", max_age=timedelta(hours=1))

    class Motd(Command):
        """Motd."""

        refresh: Refresh = Refresh(motd)

        def run(self) -> None:
            return None

    class Demo(Plugin):
        """Demo."""

        settings = HelloSettings
        commands = (Motd,)
        message = motd

    assert _refresh_help(Demo) == (
        "--refresh checks upstream for message of the day now; --cached reads what is cached "
        "and never checks. Default: check when the cached data is older than "
        "hello.caches.message.max_age_seconds (1 h)."
    )


def test_refresh_help_names_every_cache_a_held_one_and_a_contract_methods() -> None:
    held = Cache(label="covers", max_age=timedelta(days=1))

    class Books(Command):
        """Books."""

        refresh: Refresh = Refresh(held, BookSource.books)

        def run(self) -> None:
            return None

    class Shelf(Plugin):
        """Shelf."""

        settings = HelloSettings
        commands = (Books,)
        offers = (BookSource,)
        covers = held

    assert _refresh_help(Shelf, "shelf") == (
        "--refresh checks upstream for covers and books now; --cached reads what is cached "
        "and never checks. Default: check when the cached data is older than "
        "shelf.caches.covers.max_age_seconds (24 h) for covers and "
        "shelf.caches.book_source_books.max_age_seconds (1 h) for books."
    )


def test_refresh_help_names_a_shared_label_once_and_each_setting_alone() -> None:
    class Books(Command):
        """Books."""

        refresh: Refresh = Refresh(BookSource.books, BookSource.by_author)

        def run(self) -> None:
            return None

    class Shelf(Plugin):
        """Shelf."""

        settings = HelloSettings
        commands = (Books,)
        offers = (BookSource,)

    assert _refresh_help(Shelf, "shelf") == (
        "--refresh checks upstream for books now; --cached reads what is cached and never "
        "checks. Default: check when the cached data is older than "
        "shelf.caches.book_source_books.max_age_seconds (1 h) and "
        "shelf.caches.book_source_by_author.max_age_seconds (1 h)."
    )


def _paging(cache: Cache) -> type[Contract]:
    class PageSource(Contract):
        """Where pages come from."""

        @cached(cache)
        def pages(self) -> list[Book]:
            raise NotImplementedError

    return PageSource


def _pages_plugin(source: type[Contract]) -> type[Plugin]:
    class Pages(Command):
        """Pages."""

        refresh: Refresh = Refresh(source.pages)  # type: ignore[attr-defined]

        def run(self) -> None:
            return None

    class Shelf(Plugin):
        """Shelf."""

        settings = HelloSettings
        commands = (Pages,)
        offers = (source,)

    return Shelf


def test_refresh_help_names_a_contract_cache_bound_where_declared() -> None:
    bound = Cache(label="pages", max_age=timedelta(hours=2))._bound_to("shelf", "page_source_pages")
    shelf = _pages_plugin(_paging(bound))
    assert "older than shelf.caches.page_source_pages.max_age_seconds (2 h)." in _refresh_help(
        shelf, "shelf"
    )


def test_refresh_help_names_a_contract_cache_the_offering_class_holds() -> None:
    held = Cache(label="pages", max_age=timedelta(hours=2))
    shelf = _pages_plugin(_paging(held))

    class Holding(shelf):  # type: ignore[valid-type,misc]  # a class made at run time
        """Holding."""

        page_source_pages = held

    # the owner's package is this test module's, which the entry composes as shelf
    assert "older than shelf.caches.page_source_pages.max_age_seconds (2 h)." in _refresh_help(
        Holding, "shelf"
    )


def test_a_record_class_belongs_to_the_plugin_whose_package_defines_it() -> None:
    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    hello, peer = _entry(Hello), _entry(Peer, "peer", module="untaped_peer.plugin")
    for entries in ([hello, peer], [peer, hello]):
        declaration = compose(entries)
        assert declaration.quarantined == ()
        outputs = {c.output for c in declaration.commands() if c.name == "say"}
        assert outputs == {"hello.salutation"}
        assert ("hello.salutation", "hello") in [(k.kind, k.plugin) for k in declaration.kinds]


def test_a_root_entry_takes_its_name_and_its_commands_words() -> None:
    class Core(Plugin):
        """Core."""

        settings = HelloSettings
        commands = (Say,)

    core = PluginEntry("core", f"{__name__}:Core", Core, root=True)
    declaration = compose([core, _entry(Hello, "say"), _entry(Hello, "core")])
    assert [p.name for p in declaration.plugins] == ["core"]
    assert declaration.plugin("core").root
    assert [q.name for q in declaration.quarantined] == ["say", "core"]
    assert {p.rule for q in declaration.quarantined for p in q.problems} == {"duplicate-name"}


def test_a_quarantined_plugin_owns_no_kind() -> None:
    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    declaration = compose(
        [_entry(Hello, "config"), _entry(Peer, "peer", module="untaped_peer.plugin")],
        taken=("config",),
    )
    assert [q.name for q in declaration.quarantined] == ["config"]
    assert [c.output for c in declaration.commands() if c.name == "say"] == ["peer.salutation"]
    assert {kind.plugin for kind in declaration.kinds} == {"peer"}


def test_taken_names_hold_on_every_pass() -> None:
    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    entries = [_entry(Hello, "config"), _entry(Peer, "peer", module="untaped_peer.plugin")]
    declaration = compose(entries, taken=(name for name in ["config"]))
    assert [q.name for q in declaration.quarantined] == ["config"]


def test_a_refused_duplicate_leaves_the_first_entry_its_kinds() -> None:
    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    entries = [
        _entry(Hello),
        _entry(Hello, module="untaped_other.plugin"),
        _entry(Peer, "peer", module="untaped_peer.plugin"),
    ]
    declaration = compose(entries)
    assert [q.name for q in declaration.quarantined] == ["hello"]
    assert {c.output for c in declaration.commands() if c.name == "say"} == {"hello.salutation"}


def test_a_class_no_plugin_defines_belongs_to_the_plugin_reaching_it() -> None:
    declaration = compose([_entry(Hello, module="untaped_hello.plugin")])
    assert [kind.kind for kind in declaration.kinds] == ["hello.salutation", "hello.suite"]


def test_two_classes_that_would_be_one_kind() -> None:
    def suite_model() -> type[BaseModel]:
        class Suite(BaseModel):
            name: str

        return Suite

    other = suite_model()

    class Twin(Command):
        """Twin."""

        output: Output[other]  # type: ignore[valid-type]
        source: File[Suite] | None = Field(None, description="A suite.")

        def run(self) -> Iterator[BaseModel]:
            yield from ()

    assert "duplicate-kind" in [p.rule for p in _problems(_plugin_with(Twin))]


def test_stability_marks_reach_the_declaration() -> None:
    class Old(Command):
        """Old."""

        stability = deprecated(removed_in=13, replacement="hello say")

        def run(self) -> None:
            return None

    [old] = compose([_entry(_plugin_with(Old))]).commands()
    assert old.stability is not None
    assert (old.stability.removed_in, old.stability.replacement) == (13, "hello say")


def test_composing_twice_gives_the_same_declaration(declaration: Declaration) -> None:
    assert compose([_entry(Hello)]) == declaration


def test_marks_inside_an_optional_reach_the_option() -> None:
    class Marked(Command):
        """Marked."""

        pos: Positional[str] | None = Field(None, description="An argument.")
        many: Positional[list[str]] | None = Field(None, description="Arguments.")
        repo: Annotated[str, Short("-r")] | None = Field(None, description="The repository.")
        text: TextOrStdin | None = Field(None, description="Text or -.")

        def run(self) -> None:
            return None

    [marked] = compose([_entry(_plugin_with(Marked))]).commands()
    options = {option.field: option for option in marked.options}
    assert options["pos"].positional
    assert (options["many"].positional, options["many"].token) == (True, "list[str]")
    assert options["repo"].short == "-r"
    assert options["text"].or_stdin


def test_a_core_short_inside_an_optional_is_still_refused() -> None:
    class Clash(Command):
        """Clash."""

        force: Annotated[bool, Short("-y")] | None = Field(None, description="Force.")

        def run(self) -> None:
            return None

    assert ("core-short", "Clash.force") in _rules(_plugin_with(Clash))


@pytest.mark.parametrize(
    ("field", "annotation", "default"),
    [
        ("deprecated", bool, False),
        ("profile_", str, ""),
        ("verbose", bool, True),
        ("loud", Annotated[bool, Short("-v")], False),
        ("hush", Annotated[bool, Short("-q")], False),
    ],
)
def test_a_root_option_s_spelling_is_refused(
    field: str, annotation: object, default: object
) -> None:
    clash = type(
        "Clash",
        (Command,),
        {
            "__doc__": "Clash.",
            "__annotations__": {field: annotation},
            field: Field(default, description="Clashes with a root option."),
            "run": lambda self: None,
        },
    )

    assert ("root-option", f"Clash.{field}") in _rules(_plugin_with(clash))


def test_aliases_are_old_spellings_with_their_removal() -> None:
    class Greet(Command):
        """Greet."""

        aliases = (Deprecated(replacement="hi", removed_in=13),)

        def run(self) -> None:
            return None

    [greet] = compose([_entry(_plugin_with(Greet))]).commands()
    assert greet.aliases == (AliasDecl(name="hi", removed_in=13),)


def test_an_alias_is_a_spelling_no_sibling_may_take() -> None:
    class Hi(Command):
        """Hi."""

        def run(self) -> None:
            return None

    class Greet(Command):
        """Greet."""

        aliases = (Deprecated(replacement="hi", removed_in=13),)

        def run(self) -> None:
            return None

    assert [p.rule for p in _problems(_plugin_with(Hi, Greet))] == ["duplicate-command"]


def test_marks_on_commands_and_groups_are_checked() -> None:
    class Loose(Command):
        """Loose."""

        aliases = ("old",)  # type: ignore[assignment]
        stability = "experimental"  # type: ignore[assignment]

        def run(self) -> None:
            return None

    class Shaky(Group):
        """Shaky."""

        stability = "beta"  # type: ignore[assignment]

    assert sorted(_rules(_plugin_with(Loose, Shaky))) == [
        ("reserved-name", "Loose.aliases"),
        ("reserved-name", "Loose.stability"),
        ("reserved-name", "Shaky.stability"),
    ]


def test_a_refresh_on_a_plugin_whose_offers_is_not_a_tuple() -> None:
    class Books(Command):
        """Books."""

        refresh: Refresh = Refresh(BookSource.books)

        def run(self) -> None:
            return None

    class Loose(Plugin):
        """Loose."""

        settings = HelloSettings
        commands = (Books,)
        offers = BookSource  # type: ignore[assignment]

    assert _rules(Loose) == [
        ("reserved-name", "Loose.offers"),
        ("refresh-cache", "Books.refresh"),
    ]


def test_parallel_reads_its_own_plugins_settings() -> None:
    class Elsewhere(BaseModel):
        workers: int = 4

    class Fan(Command):
        """Fan."""

        parallel: Parallel = Parallel(default=key(Elsewhere).workers)

        def run(self) -> None:
            return None

    assert _rules(_plugin_with(Fan)) == [("settings-model", "Fan.parallel")]


def test_every_return_shape() -> None:
    class Maybe(Command):
        """Maybe."""

        def run(self) -> str | None:
            return None

    class Either(Command):
        """Either."""

        output: Output[Salutation]

        def run(self) -> Salutation | Iterator[Salutation]:
            return iter(())

    shapes = [c.returns for c in compose([_entry(_plugin_with(Maybe, Either))]).commands()]
    assert shapes == ["line-or-none", "rows-or-record"]


class _OtherSettings(BaseModel):
    """Another plugin's settings."""


class _Bare(BookSource):
    def books(self) -> list[Book]:
        return []


class _Ours(BookSource, Provider[HelloSettings]):
    def books(self) -> list[Book]:
        return []


class _Theirs(BookSource, Provider[_OtherSettings]):
    def books(self) -> list[Book]:
        return []


def test_a_filled_class_is_a_provider_reading_its_own_plugin_s_settings() -> None:
    class Lender(Plugin):
        """Lender."""

        settings = HelloSettings
        fills = (_Bare, _Ours, _Theirs)

    problems = _problems(Lender, "lender")
    assert [(p.rule, p.where.rpartition(".<locals>.")[2]) for p in problems] == [
        ("bad-provider", "Lender.fills"),
        ("bad-provider", "Lender.fills"),
    ]
    assert "_Bare is no Provider" in problems[0].detail
    assert "reads Provider[_OtherSettings]" in problems[1].detail

    class Good(Plugin):
        """Good."""

        settings = HelloSettings
        fills = (_Ours,)

    [plugin] = compose([_entry(Good, "good")]).plugins
    [fill] = plugin.fills
    assert (fill.target.endswith(":_Ours"), fill.contract.endswith(":BookSource")) == (True, True)


def test_an_offered_contract_s_answer_cache_is_bound_to_its_owner() -> None:
    @experimental
    class Loose(Contract):
        """Its cache is made by @cached and named by composition."""

        @cached(max_age=timedelta(hours=2), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    assert "older than owner.caches.loose_pages.max_age_seconds (2 h)." in _refresh_help(
        _pages_plugin(Loose), "owner"
    )


def test_an_answer_cache_held_under_another_name_too_is_refused() -> None:
    held = Cache(label="pages", max_age=timedelta(hours=1))

    @experimental
    class Misheld(Contract):
        """Its cache is held under a name of the holder's own."""

        @cached(held)
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class Owner(Plugin):
        """Owner."""

        settings = HelloSettings
        offers = (Misheld,)
        wrong_name = held

    # the plugin holds it as misheld_pages, so the other name is a second hold
    [problem] = _problems(Owner, "owner")
    assert (problem.rule, problem.detail) == ("duplicate-block", "is the same cache as wrong_name")


def test_two_classes_offering_one_contract_are_both_refused() -> None:
    @experimental
    class Shared(Contract):
        """Offered by two classes, which is one too many."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class First(Plugin):
        """First."""

        settings = HelloSettings
        offers = (Shared,)

    class Second(Plugin):
        """Second."""

        settings = HelloSettings
        offers = (Shared,)

    # the class is never changed: each composes alone, holding the cache under its own name
    for plugin, name in ((First, "first"), (Second, "second"), (First, "renamed")):
        [decl] = compose([_entry(plugin, name)]).plugins
        assert [block.name for block in decl.blocks] == ["shared_pages"]
    # composed together, a contract has one owner: the fault names both, in either order
    for entries in (
        [_entry(First, "first"), _entry(Second, "second")],
        [_entry(Second, "second"), _entry(First, "first")],
    ):
        both = compose(entries)
        assert both.plugins == ()
        details = {q.name: [(p.rule, p.detail) for p in q.problems] for q in both.quarantined}
        assert details == {
            "first": [
                (
                    "duplicate-offer",
                    f"{Shared.__qualname__} is offered by second too; a contract has one owner",
                )
            ],
            "second": [
                (
                    "duplicate-offer",
                    f"{Shared.__qualname__} is offered by first too; a contract has one owner",
                )
            ],
        }


def test_a_later_offerer_owns_the_contract_when_the_first_is_quarantined() -> None:
    @experimental
    class Shared(Contract):
        """Offered by a broken plugin first, then by a sound one."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        offers = (Shared,)
        runs = 5  # type: ignore[assignment]

    both = compose([_entry(Broken, "broken"), _entry(_pages_plugin(Shared), "shelf")])
    assert [plugin.name for plugin in both.plugins] == ["shelf"]
    assert [refused.name for refused in both.quarantined] == ["broken"]


def test_a_later_offerer_that_owns_the_contract_keeps_its_package_s_kinds() -> None:
    @experimental
    class Shared(Contract):
        """Offered by a broken plugin first, then by this module's."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        offers = (Shared,)
        runs = 5  # type: ignore[assignment]

    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    class Offering(Plugin):
        """Defines Salutation (this module) and offers Shared."""

        settings = HelloSettings
        commands = (Say,)
        offers = (Shared,)

    entries = [
        _entry(Broken, "broken", module="untaped_broken.plugin"),
        _entry(Peer, "peer", module="untaped_peer.plugin"),
        _entry(Offering),
    ]
    declaration = compose(entries)
    assert [q.name for q in declaration.quarantined] == ["broken"]
    assert {c.output for c in declaration.commands() if c.name == "say"} == {"hello.salutation"}


def test_a_later_offerer_refused_for_its_own_problem_stays_quarantined() -> None:
    @experimental
    class Shared(Contract):
        """Offered by a broken plugin, a sound one, then one with a kind clash."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        offers = (Shared,)
        runs = 5  # type: ignore[assignment]

    class Offering(Plugin):
        """Defines Salutation (this module) and offers Shared."""

        settings = HelloSettings
        commands = (Say,)
        offers = (Shared,)

    class Salutation(BaseModel):  # its own package's, named like this module's
        __module__ = "untaped_late.models"
        name: str

    class Hail(Command[HelloSettings]):
        """Hail."""

        output: Output[Salutation]

        def run(self) -> None:
            return None

    class Late(Plugin):
        """Offers Shared too and reaches both Salutations."""

        settings = HelloSettings
        commands = (Say, Hail)
        offers = (Shared,)

    entries = [
        _entry(Broken, "broken", module="untaped_broken.plugin"),
        _entry(Offering),
        _entry(Late, "late", module="untaped_late.plugin"),
    ]
    # while both later offerers own no package, late's two Salutations are one kind
    declaration = compose(entries)
    assert [plugin.name for plugin in declaration.plugins] == ["hello"]
    assert [q.name for q in declaration.quarantined] == ["broken", "late"]


def test_the_next_entry_point_of_a_package_owns_it_when_the_first_is_quarantined() -> None:
    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        runs = 5  # type: ignore[assignment]

    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    entries = [
        _entry(Broken, "broken"),
        _entry(Peer, "second", module=f"{__name__}.second"),
        _entry(Peer, "peer", module="untaped_peer.plugin"),
    ]
    declaration = compose(entries)
    assert [q.name for q in declaration.quarantined] == ["broken"]
    assert {c.output for c in declaration.commands() if c.name == "say"} == {"second.salutation"}


def test_offering_a_contract_another_plugin_lists_refuses_both() -> None:
    @experimental
    class Pages(Contract):
        """Offered by a plugin refused as a later offerer of Notes, then by a sound one."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    @experimental
    class Notes(Contract):
        """Offered first by a sound plugin."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    def offering(*offers: type[Contract]) -> type[Plugin]:
        class Offering(Plugin):
            """Offers contracts."""

            settings = HelloSettings

        Offering.offers = offers  # type: ignore[assignment]
        return Offering

    entries = [
        _entry(offering(Notes), "first", module="untaped_first.plugin"),
        _entry(offering(Pages, Notes), "both", module="untaped_both.plugin"),
        _entry(offering(Pages), "pages", module="untaped_pages.plugin"),
    ]
    # each contract two plugins list refuses both: no order picks an owner
    declaration = compose(entries)
    assert declaration.plugins == ()
    assert {q.name: len(q.problems) for q in declaration.quarantined} == {
        "first": 1,
        "both": 2,
        "pages": 1,
    }


def test_a_later_offerer_refreshes_a_contract_only_it_lists() -> None:
    @experimental
    class Notes(Contract):
        """Offered first by a broken plugin."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        offers = (Notes,)
        runs = 5  # type: ignore[assignment]

    @experimental
    class Paging(Contract):
        """Only the later offerer lists it."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    shelf = _pages_plugin(Paging)
    shelf.offers = (Paging, Notes)
    entries = [_entry(Broken, "broken", module="untaped_broken.plugin"), _entry(shelf, "shelf")]
    declaration = compose(entries)
    assert [plugin.name for plugin in declaration.plugins] == ["shelf"]
    assert [q.name for q in declaration.quarantined] == ["broken"]


def test_a_later_offerer_holding_its_answer_cache_has_its_own_problems_only() -> None:
    held = Cache(label="pages", max_age=timedelta(hours=1))

    @experimental
    class Held(Contract):
        """Its cache is held by the offering class."""

        @cached(held)
        def pages(self) -> list[Book]:
            raise NotImplementedError

    @experimental
    class Notes(Contract):
        """Offered first by another plugin."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    class Earlier(Plugin):
        """Offers Notes first."""

        settings = HelloSettings
        offers = (Notes,)

    shelf = _pages_plugin(Held)
    shelf.offers = (Held, Notes)
    shelf.held_pages = held  # type: ignore[attr-defined]
    entries = [_entry(Earlier, "earlier", module="untaped_earlier.plugin"), _entry(shelf, "shelf")]
    rules = {q.name: [p.rule for p in q.problems] for q in compose(entries).quarantined}
    assert rules == {"earlier": ["duplicate-offer"], "shelf": ["duplicate-offer"]}
    # a misheld cache is a problem of its own, as when composed alone
    shelf.held_pages = 5  # type: ignore[attr-defined]
    rules = {q.name: [p.rule for p in q.problems] for q in compose(entries).quarantined}
    assert rules["shelf"] == ["answer-cache", "duplicate-offer"]
    shelf.held_pages = held  # type: ignore[attr-defined]
    # with the earlier one broken, the later offerer composes, as it does alone
    Earlier.runs = 5  # type: ignore[assignment]
    assert [plugin.name for plugin in compose(entries).plugins] == ["shelf"]


def test_a_filler_waits_for_the_later_offerer_to_take_the_contract_over() -> None:
    held = Cache(label="pages", max_age=timedelta(hours=1))

    @experimental
    class Held(Contract):
        """Its cache is held by the offering class."""

        @cached(held)
        def pages(self) -> list[Book]:
            raise NotImplementedError

    @experimental
    class Notes(Contract):
        """Offered first by a broken plugin."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        offers = (Notes,)
        runs = 5  # type: ignore[assignment]

    class Shelf(Plugin):
        """Offers Held behind Broken's Notes."""

        settings = HelloSettings
        offers = (Held, Notes)
        held_pages = held

    class Fill(Held, Provider[HelloSettings]):
        pass

    reader = _pages_plugin(Held)
    reader.offers = ()
    reader.fills = (Fill,)
    entries = [
        _entry(Broken, "broken", module="untaped_broken.plugin"),
        _entry(Shelf, "shelf"),
        _entry(reader, "reader", module="untaped_reader.plugin"),
    ]
    # the reader's cache has no owner only while the broken plugin is still standing
    declaration = compose(entries)
    assert [plugin.name for plugin in declaration.plugins] == ["shelf", "reader"]


@pytest.mark.parametrize("broken", ["taken name", "empty refresh"])
def test_a_filler_listed_first_waits_for_a_later_plugin_broken_on_its_own(broken: str) -> None:
    held = Cache(label="pages", max_age=timedelta(hours=1))

    @experimental
    class Held(Contract):
        """Its cache is held by the offering class."""

        @cached(held)
        def pages(self) -> list[Book]:
            raise NotImplementedError

    @experimental
    class Notes(Contract):
        """Offered first by a plugin broken on its own."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    class Bare(Command):
        """Refreshes nothing."""

        refresh: Refresh = Refresh()

        def run(self) -> None:
            return None

    class First(Plugin):
        """Offers Notes first."""

        settings = HelloSettings
        commands = (Bare,) if broken == "empty refresh" else ()
        offers = (Notes,)

    class Shelf(Plugin):
        """Offers Held behind First's Notes."""

        settings = HelloSettings
        offers = (Held, Notes)
        held_pages = held

    class Fill(Held, Provider[HelloSettings]):
        pass

    reader = _pages_plugin(Held)
    reader.offers = ()
    reader.fills = (Fill,)
    entries = [
        _entry(reader, "reader", module="untaped_reader.plugin"),
        _entry(First, "config", module="untaped_first.plugin"),
        _entry(Shelf, "shelf"),
    ]
    taken = {"config"} if broken == "taken name" else set()
    declaration = compose(entries, taken=taken)
    assert [plugin.name for plugin in declaration.plugins] == ["reader", "shelf"]


def test_a_name_is_free_once_its_first_holder_is_refused_for_the_others() -> None:
    @experimental
    class Paging(Contract):
        """Offered by a broken plugin, so its cache has no owner."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class Broken(Plugin):
        """Broken."""

        settings = HelloSettings
        offers = (Paging,)
        runs = 5  # type: ignore[assignment]

    class Fill(Paging, Provider[HelloSettings]):
        pass

    refreshing = _pages_plugin(Paging)
    refreshing.offers = ()
    refreshing.fills = (Fill,)

    class Plain(Plugin):
        """Takes the name the refreshing plugin cannot keep."""

        settings = HelloSettings

    entries = [
        _entry(Broken, "broken", module="untaped_broken.plugin"),
        _entry(refreshing, "reader", module="untaped_one.plugin"),
        _entry(Plain, "reader", module="untaped_two.plugin"),
    ]
    declaration = compose(entries)
    assert [plugin.target for plugin in declaration.plugins] == [
        "untaped_two.plugin:" + Plain.__qualname__
    ]


def test_a_contract_whose_one_lister_is_contested_elsewhere_has_no_owner() -> None:
    @experimental
    class Paging(Contract):
        """Listed only by a plugin that lists a contested contract too."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    @experimental
    class Notes(Contract):
        """Listed by two plugins."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    class Both(Plugin):
        """Lists Paging and Notes."""

        settings = HelloSettings
        offers = (Paging, Notes)

    class Other(Plugin):
        """Lists Notes too."""

        settings = HelloSettings
        offers = (Notes,)

    class Fill(Paging, Provider[HelloSettings]):
        pass

    reader = _pages_plugin(Paging)
    reader.offers = ()
    reader.fills = (Fill,)

    class Plain(Plugin):
        """Wants the name the reader cannot keep."""

        settings = HelloSettings

    entries = [
        _entry(Both, "both", module="untaped_both.plugin"),
        _entry(Other, "other", module="untaped_other.plugin"),
        _entry(reader, "x", module="untaped_reader.plugin"),
        _entry(Plain, "x", module="untaped_plain.plugin"),
    ]
    declaration = compose(entries)
    assert [plugin.target for plugin in declaration.plugins] == [
        f"untaped_plain.plugin:{Plain.__qualname__}"
    ]


def test_two_offerers_of_one_name_are_both_refused() -> None:
    @experimental
    class Notes(Contract):
        """Listed by two plugins of one name."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    class Offering(Plugin):
        """Lists Notes."""

        settings = HelloSettings
        offers = (Notes,)

    entries = [
        _entry(Offering, "x", module="untaped_one.plugin"),
        _entry(Offering, "x", module="untaped_two.plugin"),
    ]
    declaration = compose(entries)
    assert declaration.plugins == ()
    assert [p.detail for q in declaration.quarantined for p in q.problems] == [
        f"{Notes.__qualname__} is offered by another x too; a contract has one owner"
    ] * 2


def test_a_contested_lister_names_no_kind_of_its_package() -> None:
    def book(module: str) -> type[BaseModel]:
        class Book(BaseModel):
            title: Annotated[str, Key, Column]

        Book.__module__ = module
        return Book

    def lists(model: type[BaseModel]) -> type[Command]:
        class Books(Command):
            """List books."""

            output: Output[model]  # type: ignore[valid-type]

            def run(self) -> Iterator[BaseModel]:
                yield from ()

        return Books

    @experimental
    class Notes(Contract):
        """Listed by two plugins."""

        def notes(self) -> list[Book]:
            raise NotImplementedError

    def plugin(**held: object) -> type[Plugin]:
        return type("Each", (Plugin,), {"__doc__": "A plugin.", "settings": HelloSettings, **held})

    entries = [
        _entry(plugin(offers=(Notes,)), "lib", module="untaped_pk.a"),
        _entry(plugin(offers=(Notes,)), "m", module="untaped_m.plugin"),
        _entry(
            plugin(commands=(lists(book("untaped_q.models")),)), "lib", module="untaped_q.plugin"
        ),
        _entry(plugin(commands=(lists(book("untaped_pk.models")),)), "two", module="untaped_pk.b"),
    ]
    # the first lib is refused before the package it shares with two names two's kind
    declaration = compose(entries)
    assert [plugin.name for plugin in declaration.plugins] == ["lib", "two"]
    assert [kind.kind for kind in declaration.kinds] == ["lib.book", "two.book"]


def test_a_plugin_refused_for_its_name_names_no_kind_of_its_package() -> None:
    def book(module: str) -> type[BaseModel]:
        class Book(BaseModel):
            title: Annotated[str, Key, Column]

        Book.__module__ = module
        return Book

    def lists(model: type[BaseModel]) -> type[Command]:
        class Books(Command):
            """List books."""

            output: Output[model]  # type: ignore[valid-type]

            def run(self) -> Iterator[BaseModel]:
                yield from ()

        return Books

    def plugin(**held: object) -> type[Plugin]:
        return type("Each", (Plugin,), {"__doc__": "A plugin.", "settings": HelloSettings, **held})

    entries = [
        _entry(
            plugin(commands=(lists(book("untaped_d.models")),)), "dup", module="untaped_d.plugin"
        ),
        _entry(plugin(), "dup", module="untaped_pk.a"),
        _entry(plugin(commands=(lists(book("untaped_pk.models")),)), "p", module="untaped_pk.b"),
    ]
    # the second dup is refused for its name, so p, not it, owns their package's Book
    declaration = compose(entries)
    assert [plugin.name for plugin in declaration.plugins] == ["dup", "p"]
    assert [kind.kind for kind in declaration.kinds] == ["dup.book", "p.book"]


@pytest.mark.parametrize("reacher_first", [True, False])
def test_a_plugin_reaching_a_class_that_clashes_with_its_owner_s_own_is_refused(
    reacher_first: bool,
) -> None:
    """y reaches a class of o's package, which is o's kind and clashes with o's own record.

    o's own records are claimed before any plugin's reach, so y is refused in either order.
    """

    def book(module: str) -> type[BaseModel]:
        class Book(BaseModel):
            title: Annotated[str, Key, Column]

        Book.__module__ = module
        return Book

    def lists(model: type[BaseModel]) -> type[Command]:
        class Books(Command):
            """List books."""

            output: Output[model]  # type: ignore[valid-type]

            def run(self) -> Iterator[BaseModel]:
                yield from ()

        return Books

    def plugin(command: type[Command]) -> type[Plugin]:
        held = {"__doc__": "A plugin.", "settings": HelloSettings, "commands": (command,)}
        return type("Each", (Plugin,), held)

    entries = [
        _entry(plugin(lists(book("untaped_pk.other"))), "y", module="untaped_y.plugin"),
        _entry(plugin(lists(book("untaped_pk.models"))), "o", module="untaped_pk.plugin"),
    ]
    declaration = compose(entries if reacher_first else entries[::-1])
    assert [plugin.name for plugin in declaration.plugins] == ["o"]
    assert [(kind.kind, kind.plugin) for kind in declaration.kinds] == [("o.book", "o")]
    [refused] = declaration.quarantined
    assert refused.name == "y"
    assert [problem.rule for problem in refused.problems] == ["duplicate-kind"]


@pytest.mark.parametrize("reacher_first", [True, False])
def test_two_plugins_reaching_one_class_share_its_kind(reacher_first: bool) -> None:
    """A borrow is no conflict: y reaches o's own record, and both compose."""

    class Shared(BaseModel):
        title: Annotated[str, Key, Column]

    Shared.__module__ = "untaped_pk.models"

    def lists(model: type[BaseModel]) -> type[Command]:
        class Books(Command):
            """List books."""

            output: Output[model]  # type: ignore[valid-type]

            def run(self) -> Iterator[BaseModel]:
                yield from ()

        return Books

    def plugin() -> type[Plugin]:
        held = {"__doc__": "A plugin.", "settings": HelloSettings, "commands": (lists(Shared),)}
        return type("Each", (Plugin,), held)

    entries = [
        _entry(plugin(), "y", module="untaped_y.plugin"),
        _entry(plugin(), "o", module="untaped_pk.plugin"),
    ]
    declaration = compose(entries if reacher_first else entries[::-1])
    assert sorted(plugin.name for plugin in declaration.plugins) == ["o", "y"]
    assert [(kind.kind, kind.plugin) for kind in declaration.kinds] == [("o.shared", "o")]


def _record(name: str, module: str) -> type[BaseModel]:
    held = {"__annotations__": {"title": Annotated[str, Key, Column]}, "__module__": module}
    return type(name, (BaseModel,), held)


def _listing(*models: type[BaseModel]) -> type[Plugin]:
    """A plugin with one command listing each of ``models``."""

    def lists(index: int, model: type[BaseModel]) -> type[Command]:
        class Books(Command):
            """List."""

            output: Output[model]  # type: ignore[valid-type]

            def run(self) -> Iterator[BaseModel]:
                yield from ()

        Books.__name__ = Books.__qualname__ = f"List{index}"
        return Books

    commands = tuple(lists(index, model) for index, model in enumerate(models))
    held = {"__doc__": "A plugin.", "settings": HelloSettings, "commands": commands}
    return type("Each", (Plugin,), held)


_FREE = _record("Free", "untaped_free.models")
#: w reaches q's T4, which q's own outranks, so w is refused whatever the order;
#: without w, x2's own K outranks x1's reach of it, though w reaching F2 first
#: would have refused x2.
_RIVALS = {
    "x1": _entry(
        _listing(_record("K", "untaped_x2.other"), _record("T3", "untaped_w.other")),
        "x1",
        module="untaped_x1.plugin",
    ),
    "w": _entry(
        _listing(_FREE, _record("T3", "untaped_w.models"), _record("T4", "untaped_q.other")),
        "w",
        module="untaped_w.plugin",
    ),
    "x2": _entry(
        _listing(_record("K", "untaped_x2.models"), _FREE), "x2", module="untaped_x2.plugin"
    ),
    "q": _entry(_listing(_record("T4", "untaped_q.models")), "q", module="untaped_q.plugin"),
}


@pytest.mark.parametrize("order", list(itertools.permutations(_RIVALS)))
def test_a_refused_plugin_decides_no_other_s_kinds(order: tuple[str, ...]) -> None:
    declaration = compose([_RIVALS[name] for name in order])
    assert sorted(plugin.name for plugin in declaration.plugins) == ["q", "x2"]
    assert sorted(kind.kind for kind in declaration.kinds) == ["q.t4", "x2.free", "x2.k"]


_CYCLE = {
    "a": _entry(
        _listing(_record("W", "untaped_b.x"), _record("V", "untaped_c.x")),
        "a",
        module="untaped_a.plugin",
    ),
    "b": _entry(
        _listing(
            _record("Z", "untaped_b.models"),
            _record("Y", "untaped_c.other"),
            _record("W", "untaped_b.models"),
        ),
        "b",
        module="untaped_b.plugin",
    ),
    "c": _entry(
        _listing(
            _record("Y", "untaped_c.models"),
            _record("Z", "untaped_b.other"),
            _record("V", "untaped_c.models"),
        ),
        "c",
        module="untaped_c.plugin",
    ),
}


@pytest.mark.parametrize("order", list(itertools.permutations(_CYCLE)))
def test_the_first_of_a_cycle_composes_wherever_the_plugins_below_it_come(
    order: tuple[str, ...],
) -> None:
    """b and c outrank each other; both outrank a, whose place in the order decides nothing."""
    declaration = compose([_CYCLE[name] for name in order])
    first = next(name for name in order if name in "bc")
    assert [plugin.name for plugin in declaration.plugins] == [first]


def test_a_name_goes_to_the_first_plugin_sound_on_its_own_before_kinds() -> None:
    """The later x would outrank the earlier one's record, but it never holds the name."""
    first = _entry(_listing(_record("Tag", "untaped_a.models")), "x", module="untaped_a.plugin")
    later = _entry(_listing(_record("Tag", "untaped_a.other")), "x", module="untaped_b.plugin")
    declaration = compose([first, later])
    assert [plugin.target for plugin in declaration.plugins] == [first.target]
    [refused] = declaration.quarantined
    assert [problem.rule for problem in refused.problems] == ["duplicate-name"]


def test_the_reaching_plugin_is_refused_so_the_owner_s_contract_stays_owned() -> None:
    """y's reach clashes with o's own record (as above); x refreshes o's contract.

    o composes in any order, so x's cache is owned and the second x is
    refused for x's name.
    """

    def book(module: str) -> type[BaseModel]:
        class Book(BaseModel):
            title: Annotated[str, Key, Column]

        Book.__module__ = module
        return Book

    def lists(model: type[BaseModel]) -> type[Command]:
        class Books(Command):
            """List books."""

            output: Output[model]  # type: ignore[valid-type]

            def run(self) -> Iterator[BaseModel]:
                yield from ()

        return Books

    @experimental
    class Paging(Contract):
        """Paging."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[int]:
            raise NotImplementedError

    class PagingFill(Paging, Provider[HelloSettings]):
        pass

    class Pages(Command):
        """Refresh the pages."""

        refresh: Refresh = Refresh(Paging.pages)

        def run(self) -> None:
            return None

    def plugin(**held: object) -> type[Plugin]:
        return type("Each", (Plugin,), {"__doc__": "A plugin.", "settings": HelloSettings, **held})

    entries = [
        _entry(plugin(commands=(lists(book("untaped_pk.other")),)), "y", module="untaped_y.p"),
        _entry(
            plugin(commands=(lists(book("untaped_pk.models")),), offers=(Paging,)),
            "o",
            module="untaped_pk.p",
        ),
        _entry(plugin(fills=(PagingFill,), commands=(Pages,)), "x", module="untaped_x.p"),
        _entry(plugin(), "x", module="untaped_xx.p"),
    ]
    for order in (entries, [entries[2], entries[3], entries[0], entries[1]]):
        declaration = compose(order)
        assert {plugin.target for plugin in declaration.plugins} == {
            entries[1].target,
            entries[2].target,
        }
        assert [(kind.kind, kind.plugin) for kind in declaration.kinds] == [("o.book", "o")]
        assert {
            (refused.target, tuple(problem.rule for problem in refused.problems))
            for refused in declaration.quarantined
        } == {(entries[0].target, ("duplicate-kind",)), (entries[3].target, ("duplicate-name",))}


def test_one_cache_caches_one_contract_method() -> None:
    shared = Cache(label="pages", max_age=timedelta(hours=1))

    @experimental
    class Alpha(Contract):
        """Alpha."""

        @cached(shared)
        def pages(self) -> list[Book]:
            raise NotImplementedError

    with pytest.raises(
        TypeError,
        match=r"Beta\.pages: @cached takes a cache of its own; this one already caches alpha_pages",
    ):

        @experimental
        class Beta(Contract):
            """Beta."""

            @cached(shared)
            def pages(self) -> list[Book]:
                raise NotImplementedError


def test_an_answer_cache_s_name_taken_by_something_else_is_named() -> None:
    @experimental
    class Taken(Contract):
        """Its cache's name is an attribute the owner already uses."""

        @cached(max_age=timedelta(hours=1), label="pages")
        def pages(self) -> list[Book]:
            raise NotImplementedError

    class Owner(Plugin):
        """Owner."""

        settings = HelloSettings
        offers = (Taken,)
        taken_pages = "oops"

    [problem] = _problems(Owner, "owner")
    assert problem.rule == "answer-cache"
    assert problem.detail.endswith("Owner.taken_pages, which holds 'oops': rename that attribute")
