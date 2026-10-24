"""Composition: one walk from plugin classes to the declaration, each problem naming its class."""

from __future__ import annotations

import enum
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from core_test_support.contracts.support import BOOKS, BookSource, Shop
from untaped.runtime.composition import PluginEntry, compose, kebab, snake, words
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
    File,
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
