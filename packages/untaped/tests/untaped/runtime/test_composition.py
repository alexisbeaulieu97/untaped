"""Composition: one walk from plugin classes to the declaration, each problem naming its class."""

from __future__ import annotations

import enum
from collections.abc import Iterator
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from core_test_support.contracts.support import BOOKS, BookSource, Shop
from untaped.runtime.composition import PluginEntry, compose, kebab, snake, words
from untaped.runtime.declaration import CommandDecl, Declaration, GroupDecl, Problem
from untaped.sdk import (
    Column,
    Command,
    Confirm,
    File,
    Group,
    Key,
    KeyValue,
    Output,
    Plugin,
    Positional,
    Refresh,
    Short,
    Stdin,
    StubSkill,
    Written,
    deprecated,
    experimental,
)
from untaped.sdk._block import Block

PACKAGE = __name__.partition(".")[0]


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


def test_an_unresolved_field_type_quarantines_the_plugin() -> None:
    class Later(Command):
        """Later."""

        missing: Undefined = Field(description="Never defined.")  # type: ignore[name-defined]  # noqa: F821

        def run(self) -> None:
            return None

    assert [p.rule for p in _problems(_plugin_with(Later))] == ["unresolved-type"]


def test_refresh_names_a_held_cache_or_a_cached_contract_method() -> None:
    class Books(Command):
        """Books."""

        refresh: Refresh = Refresh(BOOKS, BookSource.books)

        def run(self) -> None:
            return None

    class Shelf(Plugin):
        """Shelf."""

        settings = HelloSettings
        commands = (Books,)
        offers = (BookSource,)
        books = BOOKS

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


def test_two_plugins_cannot_share_a_kind_they_do_not_define() -> None:
    class Peer(Plugin):
        """Peer."""

        settings = HelloSettings
        commands = (Say,)

    declaration = compose([_entry(Hello), _entry(Peer, "peer", module="untaped_peer.plugin")])
    [refused] = declaration.quarantined
    assert refused.name == "peer"
    assert {p.rule for p in refused.problems} == {"foreign-kind"}


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
