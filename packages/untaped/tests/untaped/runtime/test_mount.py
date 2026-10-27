"""The mount: a 12.0 plugin class composed, parsed from its declaration, run and printed."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app, compose_root
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk import (
    Column,
    Command,
    Deprecated,
    Group,
    Key,
    Output,
    Plugin,
    Positional,
    Stdin,
    StubSkill,
    TextOrStdin,
    deprecated,
    experimental,
)
from untaped.sdk.testing import CliInvoker, CliResult


class HelloSettings(BaseModel):
    greeting: str = Field("hello", description="What say prints.")


class Salutation(BaseModel):
    """One greeting."""

    name: Annotated[str, Key, Column]
    text: Annotated[str, Column]
    length: int = 0


class Selection(BaseModel):
    """Which names to greet."""

    shout: bool = Field(False, description="Greet in capitals.")
    times: int = Field(1, ge=1, description="How many times to greet each.")


class Say(Command[HelloSettings]):
    """Greet each name.

    One salutation per name.
    """

    names: Positional[list[str]] = Field(default_factory=list, description="Whom to greet.")
    selection: Selection
    stdin: Stdin[Salutation]
    output: Output[Salutation]

    def run(self) -> Iterator[Salutation]:
        for name in self.stdin.identifiers(self.names):
            text = f"{self.settings.greeting}, {name}"
            text = text.upper() if self.selection.shout else text
            for _ in range(self.selection.times):
                yield Salutation(name=name, text=text, length=len(text))


class Rows(Command[HelloSettings]):
    """Read the salutations piped in, whole."""

    stdin: Stdin[Salutation]
    output: Output[Salutation]

    def run(self) -> Iterator[Salutation]:
        yield from self.stdin.rows()


class Greet(Command[HelloSettings]):
    """Print the configured greeting."""

    def run(self) -> str:
        return self.settings.greeting


class Echo(Command):
    """Print the text given, or what stdin carries with ``-``."""

    text: Positional[TextOrStdin] = Field(description="The text, or - for stdin.")

    def run(self) -> str:
        return self.text.strip()


class Show(Command):
    """Show one salutation, or nothing for nobody."""

    name: Positional[str] = Field("", description="Whom to show.")
    output: Output[Salutation]

    def run(self) -> Salutation | None:
        return Salutation(name=self.name, text=f"hi, {self.name}") if self.name else None


class Lengths(Command):
    """List salutations with their lengths; none prints the empty text."""

    names: Positional[list[str]] = Field(default_factory=list, description="Whom to measure.")
    output: Output[Salutation]

    def run(self) -> Iterator[Salutation]:
        self.output.columns.add("length")
        self.output.empty("Nobody to measure.")
        for name in self.names:
            yield Salutation(name=name, text=name, length=len(name))


class Peek(Command):
    """An experimental way to look."""

    stability = experimental

    def run(self) -> str:
        return "peeked"


class Whoami(Command):
    """Print who you are."""

    aliases = (Deprecated(replacement="me", removed_in=13),)

    def run(self) -> str:
        return "ada"


class Draw(Command):
    """Draw the salutations, in a format of the plugin's own too."""

    loud: Annotated[bool, deprecated(removed_in=13, replacement="--shout")] = Field(
        False, description="Shout (renamed)."
    )
    output: Output[Salutation] = Output(render={"banner": lambda rows: print(f"*{len(rows)}*")})

    def run(self) -> Iterator[Salutation]:
        yield Salutation(name="ada", text="hi")


class Count(Command):
    """Count your friends: what ``untaped hello friends`` runs on its own."""

    def run(self) -> str:
        return "2 friends"


class FriendsGroup(Group):
    """Friends you greet."""

    stability = experimental
    commands = (Whoami, Draw)
    runs = Count


class Status(Command):
    """Print the status: what ``untaped hello`` runs on its own."""

    def run(self) -> str:
        return "all good"


class Hello(Plugin):
    """Say hello (a 12.0 plugin)."""

    settings = HelloSettings
    commands = (Greet, Say, Rows, Echo, Show, Lengths, Peek, FriendsGroup)
    runs = Status
    skill = StubSkill("Uses the hello plugin. Use when greeting.")


class Undocumented(Command):
    def run(self) -> str:
        return ""


class Broken(Plugin):
    """A plugin whose command has no docstring."""

    settings = HelloSettings
    commands = (Undocumented,)


def _candidate(name: str, target: object) -> PluginCandidate:
    return PluginCandidate(distribution=f"untaped-{name}", name=name, target=target)


HELLO = _candidate("hello", Hello)


def _run(
    *argv: str, input: str | None = None, plugins: list[PluginCandidate] | None = None
) -> CliResult:
    root = build_root_app(candidates=[HELLO] if plugins is None else plugins)
    return CliInvoker().invoke(root.meta, list(argv), input=input)


def test_root_help_lists_the_plugin_from_its_summary() -> None:
    result = _run("--help")
    assert result.exit_code == 0, result.stderr
    assert "hello  Say hello (a 12.0 plugin)." in result.stdout


def test_a_command_s_help_comes_from_the_declaration_in_its_panels() -> None:
    result = _run("hello", "say", "--help")
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert "Greet each name." in out and "One salutation per name." in out
    for line in ("NAMES", "--stdin", "--format -f", "--columns -c"):
        assert line in out
    assert "╭─ Selection" in out and "--shout" in out and "--times" in out
    assert "╭─ Output" in out
    assert "--no-shout" not in out


def test_a_line_command_prints_its_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run("hello", "greet").stdout == "hello\n"
    monkeypatch.setenv("UNTAPED_HELLO__GREETING", "hi")
    assert _run("hello", "greet").stdout == "hi\n"


def test_config_set_reaches_a_mounted_plugin_s_settings() -> None:
    assert _run("config", "set", "hello.greeting", "bonjour").exit_code == 0
    assert _run("hello", "greet").stdout == "bonjour\n"


def test_rows_show_their_column_fields_in_a_table() -> None:
    result = _run("hello", "say", "ada")
    assert result.exit_code == 0, result.stderr
    header = result.stdout.splitlines()[1]
    assert "name" in header and "text" in header and "length" not in header


def test_options_of_a_bundle_reach_the_bundle() -> None:
    result = _run("hello", "say", "ada", "--shout", "--times", "2", "--format", "json")
    assert result.exit_code == 0, result.stderr
    assert [row["text"] for row in json.loads(result.stdout)] == ["HELLO, ADA"] * 2


def test_a_value_the_model_refuses_is_exit_2_naming_its_flag() -> None:
    result = _run("hello", "say", "ada", "--times", "0")
    assert result.exit_code == 2
    assert "--times: Input should be greater than or equal to 1" in result.stderr


def test_a_token_the_parser_cannot_convert_is_exit_2() -> None:
    result = _run("hello", "say", "ada", "--times", "many")
    assert result.exit_code == 2
    assert "--times" in result.stderr


def test_pipe_rows_carry_the_declared_kind_and_read_back_by_their_key() -> None:
    piped = _run("hello", "say", "ada", "grace", "--format", "pipe")
    assert piped.exit_code == 0, piped.stderr
    assert {json.loads(line)["kind"] for line in piped.stdout.splitlines()} == {"hello.salutation"}

    again = _run("hello", "say", "--stdin", "--format", "json", input=piped.stdout)
    assert again.exit_code == 0, again.stderr
    assert [row["name"] for row in json.loads(again.stdout)] == ["ada", "grace"]

    rows = _run("hello", "rows", "--stdin", "--format", "json", input=piped.stdout)
    assert rows.exit_code == 0, rows.stderr
    assert [row["text"] for row in json.loads(rows.stdout)] == ["hello, ada", "hello, grace"]


def test_stdin_takes_bare_lines_and_refuses_another_kind() -> None:
    bare = _run("hello", "say", "--stdin", "--format", "json", input="ada\ngrace\n")
    assert [row["name"] for row in json.loads(bare.stdout)] == ["ada", "grace"]

    other = json.dumps({"untaped": "1", "kind": "acme.thing", "record": {"name": "x"}})
    refused = _run("hello", "say", "--stdin", input=other + "\n")
    assert refused.exit_code == 2
    assert "'acme.thing' is not accepted here" in refused.stderr


def test_stdin_and_arguments_together_or_neither_are_usage_errors() -> None:
    both = _run("hello", "say", "ada", "--stdin", input="grace\n")
    assert both.exit_code == 2
    assert "not both" in both.stderr
    neither = _run("hello", "say")
    assert neither.exit_code == 2
    assert "at least one identifier is required" in neither.stderr


def test_rows_refuses_a_bare_line() -> None:
    result = _run("hello", "rows", "--stdin", input="ada\n")
    assert result.exit_code == 2
    assert "expected rows on stdin" in result.stderr


def test_a_text_or_stdin_value_of_dash_reads_stdin() -> None:
    assert _run("hello", "echo", "-", input="from the pipe\n").stdout == "from the pipe\n"
    assert _run("hello", "echo", "given").stdout == "given\n"


def test_one_record_is_the_detail_view_and_none_prints_nothing() -> None:
    shown = _run("hello", "show", "ada", "--format", "json")
    assert json.loads(shown.stdout)["name"] == "ada"
    nothing = _run("hello", "show")
    assert nothing.exit_code == 0
    assert nothing.stdout == ""


def test_run_adds_columns_and_sets_the_empty_text() -> None:
    measured = _run("hello", "lengths", "ada")
    assert "length" in measured.stdout.splitlines()[1]
    assert "Nobody to measure." in _run("hello", "lengths").stderr


def test_columns_picks_what_the_table_shows() -> None:
    result = _run("hello", "say", "ada", "--columns", "text")
    header = result.stdout.splitlines()[1]
    assert "text" in header and "name" not in header


def test_groups_and_marks_come_from_the_declaration() -> None:
    assert _run("hello", "friends", "whoami").stdout == "ada\n"
    renamed = _run("hello", "friends", "me")
    assert renamed.stdout == "ada\n"
    assert "deprecated" in renamed.stderr
    listing = _run("hello", "--help").stdout
    assert "╭─ Experimental" in listing and "peek" in listing and "friends" in listing
    assert _run("hello", "peek").stdout == "peeked\n"
    assert "--loud" not in _run("hello", "friends", "draw", "--help").stdout
    assert "--loud" in _run("--deprecated", "hello", "friends", "draw", "--help").stdout


def test_a_plugin_renderer_draws_its_own_format() -> None:
    result = _run("hello", "friends", "draw", "--format", "banner")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "*1*\n"


def test_the_plugin_root_runs_its_command() -> None:
    assert _run("hello").stdout == "all good\n"


def test_a_group_runs_its_command_beside_its_subcommands() -> None:
    assert _run("hello", "friends").stdout == "2 friends\n"
    assert _run("hello", "friends", "whoami").stdout == "ada\n"
    listing = _run("hello", "friends", "--help").stdout
    assert "whoami" in listing


def test_a_node_s_page_goes_on_with_the_words_of_the_command_it_runs() -> None:
    page = " ".join(_run("hello", "friends", "--help").stdout.split())
    assert "Friends you greet. Count your friends: what untaped hello friends runs" in page
    listing = _run("hello", "--help").stdout
    assert "Friends you greet." in listing and "Count your friends" not in listing
    assert "Print the status" in _run("hello", "--help").stdout


def test_the_stub_skill_and_status_reach_the_plugin_listing() -> None:
    rows = json.loads(_run("plugin", "list", "--format", "json").stdout)
    assert {row["name"]: row["status"] for row in rows}["hello"] == "ready"
    skills = _run("skills", "list", "--format", "json")
    assert "untaped-hello" in skills.stdout


def test_a_plugin_class_composition_refuses_is_quarantined_naming_the_problem() -> None:
    result = compose_root(candidates=[HELLO, _candidate("broken", Broken)])
    assert [plugin.spec.name for plugin in result.plugins] == ["untaped", "hello"]
    [record] = result.quarantine
    assert (record.name, record.reason) == ("broken", "bad-plugin-class")
    assert record.detail == "Undocumented: a command's docstring is its help [missing-docstring]"


def test_a_name_a_spec_and_a_plugin_class_share_quarantines_both() -> None:
    other = PluginCandidate(distribution="untaped-other", name="hello", target=PluginSpec("hello"))
    result = compose_root(candidates=[HELLO, other])
    assert [plugin.spec.name for plugin in result.plugins] == ["untaped"]
    assert [record.reason for record in result.quarantine] == ["duplicate-name"] * 2
