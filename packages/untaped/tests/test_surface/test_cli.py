"""The ``cli`` walker, one shape per command, over a small app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import pytest
from cyclopts import App, Parameter

from untaped.bootstrap import build_root_app, kept_composition
from untaped.stability import (
    DEPRECATED_OPTIONS,
    apply_marks,
    deprecated,
    deprecated_alias,
    experimental,
    frozen,
)
from untaped.surface import DeprecatedMark, SurfaceError
from untaped.surface._cli import cli_lines, subtree_lines


@Parameter(name="*")
@dataclass
class Window:
    since: str | None = None
    limit: int = 50


def _root() -> App:
    root = App(name="untaped")
    demo = App(name="demo")
    root.command(demo)

    @demo.command
    def get(
        kind: str,
        names: list[str],
        /,
        *,
        owner: str,
        tags: list[str] | None = None,
        output_format: Annotated[
            Literal["json", "table"], Parameter(name=["--format", "-f"])
        ] = "table",
        refresh: Annotated[bool, Parameter(negative="--cached")] = True,
        stdin: bool = False,
        secret: Annotated[str | None, Parameter(show=False)] = None,
        legacy: Annotated[
            bool,
            Parameter(negative="", group=DEPRECATED_OPTIONS),
            deprecated(removed_in=12, replacement="--format json"),
        ] = False,
    ) -> None:
        """Required and repeatable positionals, options of every shape."""

    @demo.command
    def show(where: str, page: int = 1, /) -> None:
        """An optional positional."""

    @demo.command
    def against(target: str) -> None:
        """A positional that is also an option, against the conventions."""

    @demo.command
    def window(*, window: Window | None = None) -> None:
        """A flattened dataclass: its fields are the options."""

    @demo.command
    @experimental
    def preview() -> None:
        """Experimental."""

    @demo.command(show=False)
    def hidden() -> None:
        """Hidden, so not promised."""

    deprecated_alias(demo, "fetch", "get")
    deprecated_alias(demo["get"], "--fmt", "--format")
    apply_marks(root)
    return root


LEGACY = "cli untaped demo get --legacy switch  [deprecated, removed in 12.0; use --format json]"


@pytest.fixture(scope="module")
def lines() -> set[str]:
    return {str(line) for line in subtree_lines(_root(), "demo", None)}


def _of(lines: set[str], command: str) -> list[str]:
    prefix = f"cli untaped demo {command}"
    return sorted(line for line in lines if line == prefix or line.startswith(f"{prefix} "))


def test_a_command_with_every_option_shape(lines: set[str]) -> None:
    assert _of(lines, "get") == [
        "cli untaped demo get --fmt -> --format  [deprecated]",
        "cli untaped demo get --format VALUE default=table",
        "cli untaped demo get --format alias=-f",
        "cli untaped demo get --format choice=json",
        "cli untaped demo get --format choice=table",
        LEGACY,
        "cli untaped demo get --refresh negative=--cached",
        "cli untaped demo get --refresh switch default=true",
        "cli untaped demo get --stdin negative=--no-stdin",
        "cli untaped demo get --stdin switch",
        "cli untaped demo get --tags VALUE... default=unset",
        "cli untaped demo get --tags negative=--empty-tags",
        "cli untaped demo get KIND NAMES... --owner VALUE",
    ]


def test_an_optional_positional_has_its_position(lines: set[str]) -> None:
    assert _of(lines, "show") == [
        "cli untaped demo show WHERE",
        "cli untaped demo show [PAGE]@2 default=1",
    ]


def test_a_positional_that_is_also_an_option_has_an_alias_line(lines: set[str]) -> None:
    assert _of(lines, "against") == [
        "cli untaped demo against TARGET",
        "cli untaped demo against TARGET alias=--target",
    ]


def test_a_flattened_dataclass_gives_its_fields_as_options(lines: set[str]) -> None:
    assert _of(lines, "window") == [
        "cli untaped demo window",
        "cli untaped demo window --limit VALUE default=50",
        "cli untaped demo window --since VALUE default=unset",
    ]


def test_marks_aliases_and_hidden_commands(lines: set[str]) -> None:
    assert "cli untaped demo" in lines
    assert "cli untaped demo fetch -> untaped demo get  [deprecated]" in lines
    assert _of(lines, "preview") == ["cli untaped demo preview  [experimental]"]
    assert _of(lines, "hidden") == []
    assert not [line for line in lines if "secret" in line]


def test_a_deprecated_flag_keeps_its_line_marked(lines: set[str]) -> None:
    # Hidden until --deprecated, so otherwise skipped: deprecating a flag in a minor must not
    # read as removing it.
    assert LEGACY in lines


def test_a_plugin_mark_reaches_every_line() -> None:
    lines = subtree_lines(_root(), "demo", "experimental")
    assert {line.mark for line in lines} == {
        "experimental",
        DeprecatedMark(),
        DeprecatedMark(removed_in=12, use="--format json"),
    }


def _frozen_root() -> App:
    root = App(name="untaped")
    demo = App(name="demo")
    root.command(demo)

    @demo.command
    @frozen
    def pages(*words: str, wide: bool = False) -> None:
        """Frozen: its line and its positional, not its options."""

    return root


def test_a_frozen_command_freezes_its_line_and_positionals_only() -> None:
    lines = {str(line) for line in subtree_lines(_frozen_root(), "demo", None)}
    assert _of(lines, "pages") == [
        "cli untaped demo pages  [frozen]",
        "cli untaped demo pages --wide negative=--no-wide",
        "cli untaped demo pages --wide switch",
        "cli untaped demo pages [WORDS...]@1 default=unset  [frozen]",
    ]


def test_a_frozen_command_under_a_marked_plugin_is_refused() -> None:
    with pytest.raises(SurfaceError, match="a frozen command is never marked experimental"):
        list(subtree_lines(_frozen_root(), "demo", "experimental"))


def test_the_root_lists_its_options_and_cyclopts_built_ins() -> None:
    with kept_composition(quiet=True):
        lines = {str(line) for line in cli_lines(build_root_app(candidates=[]), {})}
    assert {
        "cli untaped --help switch",
        "cli untaped --help alias=-h",
        "cli untaped --version switch",
        "cli untaped --install-completion",
        "cli untaped --install-completion --output alias=-o",
        "cli untaped --profile VALUE default=unset",
    } <= lines
