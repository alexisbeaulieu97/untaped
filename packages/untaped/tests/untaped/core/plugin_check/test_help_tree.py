"""Help-tree rules on synthetic commands: names, short flags, panels and examples."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import pytest
from cyclopts import App, Parameter
from pydantic import Field

from core_test_support.commands.support import make_plugin
from untaped.core.plugin_check.help_tree import command_violations, help_tree_violations
from untaped.runtime import bootstrap
from untaped.sdk import Command, Group, create_app, experimental
from untaped.sdk.marks import Experimental
from untaped.sdk.testing import plugin_candidate

Format = Annotated[str, Parameter(name="--format", help="Output format.")]


def _clean(
    *, force: Annotated[bool, Parameter(name="--force", negative="", help="Force.")] = False
) -> None:
    """Do it."""


def _exclude(*, value: Annotated[str, Parameter(name=["--exclude", "-x"], help="X.")] = "") -> None:
    """Do it."""


def _extra(*, value: Annotated[str, Parameter(name=["--extra", "-x"], help="X.")] = "") -> None:
    """Do it."""


def _fields(*, value: Annotated[str, Parameter(name=["--fields", "-f"], help="F.")] = "") -> None:
    """Do it."""


def _violations(command: Callable[..., None], name: str = "nuke") -> set[tuple[str, str]]:
    """The help-tree violations of ``command`` registered as ``demo <name>``."""
    app = create_app(name="demo")
    app.command(command, name=name)
    return set(command_violations(("demo", name), app[name]))


def _root(**subtrees: dict[str, Callable[..., None]]) -> App:
    """A root app with one subtree per keyword, mapping command names to functions."""
    root = create_app(name="untaped")
    for top, commands in subtrees.items():
        sub = create_app(name=top)
        for name, command in commands.items():
            sub.command(command, name=name)
        root.command(sub, name=top)
    return root


def test_a_command_name_that_is_not_kebab_case_is_flagged() -> None:
    assert _violations(_clean, name="nuke_all") == {("command-name", "nuke_all")}
    assert _violations(_clean, name="nuke-all") == set()


def test_a_reserved_short_flag_with_another_meaning_is_flagged() -> None:
    root = _root(demo={"show": _fields})
    assert help_tree_violations(root, ["demo"]) == ["demo show::reserved-short::-f --fields"]


def test_a_short_flag_with_two_meanings_in_one_subtree_is_flagged() -> None:
    root = _root(demo={"drop": _exclude, "keep": _extra})
    assert help_tree_violations(root, ["demo"]) == [
        "demo drop::ambiguous-short::-x --exclude",
        "demo keep::ambiguous-short::-x --extra",
    ]


def test_a_short_flag_may_mean_different_things_in_different_subtrees() -> None:
    root = _root(one={"drop": _exclude}, two={"keep": _extra})
    assert help_tree_violations(root, ["one"]) == []
    assert help_tree_violations(root, ["two"]) == []


# --- panels -------------------------------------------------------------------


class _Set(Command):
    """Set it."""

    fast: bool = Field(False, description="Go fast.")

    def run(self) -> None:
        return None


class _Lab(Group):
    """Try things."""

    stability = experimental
    commands = (_Set,)


@pytest.mark.parametrize("stability", [None, experimental], ids=["stable", "experimental"])
def test_a_plugin_class_s_panels_are_core_s_and_pass(stability: Experimental | None) -> None:
    svc = make_plugin("svc", stability=stability, commands=(_Set, _Lab))
    root = bootstrap.build_root_app(candidates=[plugin_candidate(svc)])
    assert not bootstrap.composition().quarantine
    assert [line for line in help_tree_violations(root, ["svc"]) if "-panel::" in line] == []


def _examples(*, fmt: Format = "table") -> None:
    """Show it.

    Examples
    --------
    ```console
    $ untaped demo show --format json
    $ untaped demo show --format json | untaped demo show --nope
    ```
    """


def test_an_example_that_does_not_parse_is_flagged() -> None:
    root = _root(demo={"show": _examples})
    [line] = help_tree_violations(root, ["demo"])
    assert line.startswith("demo show::example-command::untaped demo show --nope: ")


def test_examples_that_parse_are_clean() -> None:
    def _good(*, fmt: Format = "table") -> None:
        """Show it.

        Examples
        --------
        ```console
        $ untaped demo show --format NAME
        ```
        """

    assert help_tree_violations(_root(demo={"show": _good}), ["demo"]) == []
