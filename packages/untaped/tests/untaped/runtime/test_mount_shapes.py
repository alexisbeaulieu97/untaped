"""The mount on the shapes a command line, a bundle and a return value can take."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app, compose_root
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import (
    Column,
    Command,
    Failure,
    Group,
    Key,
    Output,
    Plugin,
    Positional,
    Record,
    Stdin,
    TextOrStdin,
    derive,
)
from untaped.sdk.testing import CliInvoker, CliResult

#: What ``touch`` did before it printed: an unknown ``--columns`` must leave it empty.
TOUCHED: list[str] = []


class Shape(BaseModel):
    """One shape."""

    name: Annotated[str, Key, Column]
    sides: int = 0
    error: Failure | None = None


class Square(Shape):
    """A shape with four sides."""


class Tag(Command):
    """Tag a shape."""

    tags: list[str] = Field(default_factory=list, description="A tag, repeated.")
    name: Positional[str] = Field(description="The shape.")

    def run(self) -> str:
        return f"{self.name}: {','.join(self.tags)}"


class Word(Command):
    """Print this command's word."""

    word: ClassVar[str] = "word"

    def run(self) -> str:
        return self.word


Left = derive(Word, "Left", doc="Print left.", word="left")
Right = derive(Word, "Right", doc="Print right.", word="right")


class AGroup(Group):
    """Group a."""

    commands = (derive(Word, "List", doc="List a.", word="a"),)


class BGroup(Group):
    """Group b."""

    commands = (derive(Word, "List", doc="List b.", word="b"),)


class Count(Command):
    """Count the rows piped in, of any kind."""

    stdin: Stdin[Record]

    def run(self) -> str:
        return str(len(self.stdin.rows()))


class Note(Command):
    """Print the note given, - for stdin, or nothing."""

    text: Positional[TextOrStdin | None] = Field(None, description="The note, or - for stdin.")

    def run(self) -> str | None:
        return None if self.text is None else self.text.strip()


class Touch(Command):
    """Touch shapes, then list them."""

    names: Positional[list[str]] = Field(default_factory=list, description="What to touch.")
    output: Output[Shape]

    def run(self) -> Iterator[Shape]:
        TOUCHED.extend(self.names)
        yield from (Shape(name=name) for name in self.names)


class Level(BaseModel):
    """How deep to go."""

    depth: int = Field(description="How deep.")


class Limits(BaseModel):
    """What to stop at."""

    level: Level | None = None
    colour: bool = Field(True, description="Colour the output.")


class Inner(BaseModel):
    """The inner limit."""

    floor: int = Field(description="The lowest.")


class Outer(BaseModel):
    """The outer limit."""

    inner: Inner


class Measure(Command):
    """Measure a shape."""

    limits: Limits
    outer: Outer | None = None
    max_sides: int = Field(0, validation_alias="maxSides", ge=0, description="At most.")
    output: Output[Shape]

    def run(self) -> Shape:
        level = self.limits.level
        sides = -1 if level is None else level.depth
        sides = self.outer.inner.floor if self.outer is not None else sides
        sides = self.max_sides or sides
        return Shape(name="red" if self.limits.colour else "plain", sides=sides)


class Squares(Command):
    """List squares as the shapes they are."""

    output: Output[Shape]

    def run(self) -> Iterator[Shape]:
        yield Square(name="box", sides=4)


class Pick(Command):
    """One shape for one name, every shape for none."""

    name: Positional[str] = Field("", description="The shape.")
    output: Output[Shape]

    def run(self) -> Iterator[Shape] | Shape:
        if self.name:
            return Shape(name=self.name)
        return iter([Shape(name="a"), Shape(name="b")])


class Broke(Command):
    """Draw a failed shape in the plugin's own format."""

    output: Output[Shape] = Output(render={"dots": lambda rows: print("." * len(rows))})

    def run(self) -> Iterator[Shape]:
        yield Shape(name="bad", error=Failure(message="no", category="failed", system="x"))


class Pin(Command):
    """Show one shape as a document."""

    output: Output[Shape] = Output(format="yaml")

    def run(self) -> Shape:
        return Shape(name="pin")


class ShapesSettings(BaseModel):
    pass


class Shapes(Plugin):
    """Shapes, every way."""

    settings = ShapesSettings
    commands = (
        Tag,
        Left,
        Right,
        AGroup,
        BGroup,
        Count,
        Note,
        Touch,
        Measure,
        Squares,
        Pick,
        Broke,
        Pin,
    )


class TakenSettings(BaseModel):
    extensions: str = Field("", description="A key untaped reserves.")


class Loot(BaseModel):
    """What the taken plugin lists."""

    name: Annotated[str, Key]


class Grab(Command[TakenSettings]):
    """List loot."""

    output: Output[Loot]

    def run(self) -> Iterator[Loot]:
        yield Loot(name="gold")


class Taken(Plugin):
    """A plugin whose settings take a reserved key."""

    settings = TakenSettings
    commands = (Grab,)


SHAPES = PluginCandidate(distribution="untaped-shapes", name="shapes", target=Shapes)


def _run(*argv: str, input: str | None = None) -> CliResult:
    root = build_root_app(candidates=[SHAPES])
    return CliInvoker().invoke(root.meta, list(argv), input=input)


def test_a_list_option_leaves_the_argument_after_it() -> None:
    result = _run("shapes", "tag", "--tags", "a", "--tags", "b", "box")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "box: a,b\n"


def test_derived_commands_of_one_base_each_run_their_own_class() -> None:
    assert _run("shapes", "left").stdout == "left\n"
    assert _run("shapes", "right").stdout == "right\n"
    assert _run("shapes", "a", "list").stdout == "a\n"
    assert _run("shapes", "b", "list").stdout == "b\n"


def test_stdin_of_any_record_reads_known_kinds_and_refuses_an_unknown_one() -> None:
    piped = _run("shapes", "touch", "a", "b", "--format", "pipe").stdout
    counted = _run("shapes", "count", "--stdin", input=piped)
    assert counted.exit_code == 0, counted.stderr
    assert counted.stdout == "2\n"

    other = json.dumps({"untaped": "1", "kind": "acme.thing", "record": {"name": "x"}})
    refused = _run("shapes", "count", "--stdin", input=other + "\n")
    assert refused.exit_code != 0
    assert "no installed plugin declares the kind 'acme.thing'" in refused.stderr


def test_an_optional_text_or_stdin_reads_a_dash() -> None:
    assert _run("shapes", "note", "-", input="piped\n").stdout == "piped\n"
    assert _run("shapes", "note").stdout == ""


def test_an_unknown_column_is_refused_before_run() -> None:
    TOUCHED.clear()
    result = _run("shapes", "touch", "a", "--columns", "nope")
    assert result.exit_code == 2
    assert "unknown column 'nope'" in result.stderr
    assert TOUCHED == []


def test_an_optional_bundle_s_required_field_is_needed_only_when_used() -> None:
    plain = json.loads(_run("shapes", "measure", "--format", "json").stdout)
    assert plain["sides"] == -1
    deep = _run("shapes", "measure", "--depth", "3", "--no-colour", "--format", "json")
    assert deep.exit_code == 0, deep.stderr
    assert (json.loads(deep.stdout)["name"], json.loads(deep.stdout)["sides"]) == ("plain", 3)
    assert "--no-colour" in _run("shapes", "measure", "--help").stdout
    floor = _run("shapes", "measure", "--floor", "2", "--format", "json")
    assert json.loads(floor.stdout)["sides"] == 2


def test_an_option_takes_its_field_s_validation_alias() -> None:
    capped = _run("shapes", "measure", "--max-sides", "5", "--format", "json")
    assert capped.exit_code == 0, capped.stderr
    assert json.loads(capped.stdout)["sides"] == 5
    refused = _run("shapes", "measure", "--max-sides", "-1")
    assert refused.exit_code == 2
    assert "--max-sides: Input should be greater than or equal to 0" in refused.stderr


def test_a_row_of_a_subclass_carries_the_declared_kind() -> None:
    piped = _run("shapes", "squares", "--format", "pipe")
    assert json.loads(piped.stdout)["kind"] == "shapes.shape"


def test_rows_or_record_prints_either() -> None:
    one = json.loads(_run("shapes", "pick", "a", "--format", "json").stdout)
    assert one["name"] == "a"
    every = json.loads(_run("shapes", "pick", "--format", "json").stdout)
    assert [row["name"] for row in every] == ["a", "b"]


def test_a_failed_row_counts_under_a_plugin_renderer() -> None:
    result = _run("shapes", "broke", "--format", "dots")
    assert result.stdout == ".\n"
    assert result.exit_code == 1


def test_a_class_quarantined_after_composing_leaves_the_others_mounted() -> None:
    taken = PluginCandidate(distribution="untaped-taken", name="taken", target=Taken)
    result = compose_root(candidates=[SHAPES, taken])
    assert [plugin.spec.name for plugin in result.plugins] == ["shapes"]
    [record] = result.quarantine
    assert (record.name, record.reason) == ("taken", "bad-settings-keys")

    # one module holds both plugins, so composition names Loot's kind after its first walker
    loot = json.dumps({"untaped": "1", "kind": "shapes.loot", "record": {"name": "gold"}})
    root = build_root_app(candidates=[SHAPES, taken])
    refused = CliInvoker().invoke(root.meta, ["shapes", "count", "--stdin"], input=loot + "\n")
    assert "no installed plugin declares the kind 'shapes.loot'" in refused.stderr


def test_a_plugin_class_s_name_is_checked_like_a_spec_s() -> None:
    taken = PluginCandidate(distribution="untaped-config", name="config", target=Shapes)
    nameless = PluginCandidate(distribution=" ", name="shapes", target=Shapes)
    result = compose_root(candidates=[taken, nameless])
    assert result.plugins == ()
    assert sorted(record.reason for record in result.quarantine) == [
        "bad-metadata",
        "duplicate-name",
    ]


def test_an_output_s_own_default_format_prints_without_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")  # follows only a table default

    shown = _run("shapes", "pin")
    asked = _run("shapes", "pin", "--format", "json")
    listed = _run("shapes", "touch", "a")

    assert (shown.exit_code, shown.stdout.splitlines()[0]) == (0, "name: pin"), shown.stderr
    assert json.loads(asked.stdout)["name"] == "pin"
    assert [row["name"] for row in json.loads(listed.stdout)] == ["a"]


def test_an_output_refuses_a_default_it_does_not_offer() -> None:
    with pytest.raises(ValueError, match="Output\\(format='tree'\\) is not one of"):
        Output(format="tree")
