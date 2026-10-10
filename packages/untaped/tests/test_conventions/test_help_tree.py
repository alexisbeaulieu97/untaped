"""Help-tree rules on synthetic commands: parameters, names, short flags, writes and panels."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Annotated

import pytest
from cyclopts import App, Group, Parameter

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped.conventions.help_tree import command_violations, help_tree_violations
from untaped.sdk import create_app, experimental, writes
from untaped.stability import Experimental
from untaped.testing import plugin_candidate

Yes = Annotated[bool, Parameter(name="--yes", negative="", help="Skip the prompt.")]
DryRun = Annotated[bool, Parameter(name="--dry-run", negative="", help="Only show the plan.")]
Format = Annotated[str, Parameter(name="--format", help="Output format.")]


def _undeclared(*, yes: Yes = False, dry_run: DryRun = False, fmt: Format = "table") -> None:
    """Do it."""


@writes
def _write(*, yes: Yes = False, dry_run: DryRun = False, fmt: Format = "table") -> None:
    """Do it."""


@writes(destructive=True)
def _destructive(*, yes: Yes = False, dry_run: DryRun = False, fmt: Format = "table") -> None:
    """Do it."""


@writes
def _write_without_format(*, yes: Yes = False, dry_run: DryRun = False) -> None:
    """Do it."""


@writes(destructive=True)
def _destructive_without_dry_run(*, yes: Yes = False, fmt: Format = "table") -> None:
    """Do it."""


@writes(destructive=True)
def _destructive_without_yes(*, dry_run: DryRun = False, fmt: Format = "table") -> None:
    """Do it."""


def _without_help(*, target: Annotated[str, Parameter(name="--target")] = "") -> None:
    """Do it."""


def _one_name_twice(
    *,
    first: Annotated[str, Parameter(name="--name", help="First.")] = "",
    second: Annotated[str, Parameter(name="--name", help="Second.")] = "",
) -> None:
    """Do it."""


def _list_option(
    *, tags: Annotated[list[str] | None, Parameter(name="--tag", help="Tags.")] = None
) -> None:
    """Do it."""


def _false_flag_with_negative(
    *, force: Annotated[bool, Parameter(name="--force", help="Force.")] = False
) -> None:
    """Do it."""


def _positional_or_keyword(target: Annotated[str, Parameter(help="Target.")]) -> None:
    """Do it."""


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


@pytest.mark.parametrize(
    ("command", "violation"),
    [
        (_without_help, ("missing-help", "--target")),
        (_one_name_twice, ("duplicate-option", "--name")),
        (_list_option, ("empty-negative", "--empty-tag")),
        (_false_flag_with_negative, ("needless-negative", "--no-force")),
        (_positional_or_keyword, ("positional-or-keyword", "--target")),
    ],
    ids=[
        "missing-help",
        "duplicate-option",
        "empty-negative",
        "needless-negative",
        "positional-or-keyword",
    ],
)
def test_a_parameter_rule_is_flagged(
    command: Callable[..., None], violation: tuple[str, str]
) -> None:
    assert _violations(command) == {violation}


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


def test_flags_without_a_declaration_are_flagged() -> None:
    assert _violations(_undeclared) == {("undeclared-write", "--dry-run --yes")}


def test_declared_write_without_format_is_flagged() -> None:
    assert _violations(_write_without_format) == {("mutation-format", "nuke")}


def test_declared_destructive_without_a_control_is_flagged() -> None:
    assert _violations(_destructive_without_dry_run) == {("destructive-controls", "--dry-run")}
    assert _violations(_destructive_without_yes) == {("destructive-controls", "--yes")}


def test_declared_commands_with_all_their_flags_are_clean() -> None:
    assert _violations(_destructive) == set()
    assert _violations(_write) == set()


# --- panels -------------------------------------------------------------------


def _composed(factory: Callable[[], App], *, stability: Experimental | None = None) -> list[str]:
    """The help-tree violations of ``factory``'s app, composed as plugin ``svc`` by core."""
    spec = replace(make_spec(name="svc", factory=factory), stability=stability)
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])
    return [line for line in help_tree_violations(root, ["svc"]) if "-panel::" in line]


def _option_in(group: Group) -> Callable[..., None]:
    """A command whose ``--fast`` option lists in ``group``."""

    def command(*, fast: bool = False) -> None:
        """Do it."""

    # A closure variable can't appear in a postponed annotation, so set the real one.
    command.__annotations__["fast"] = Annotated[
        bool, Parameter(name="--fast", negative="", help="F.", group=group)
    ]
    return command


def test_reserved_panel_for_any_group_named_like_a_core_panel() -> None:
    def factory() -> App:
        app = create_app(name="svc", help="Service.")
        app.group = (Group("Plugins"),)  # core replaces the top app's group: passes
        app.command(_clean, name="set")
        app["set"].group = "Experimental"
        other = create_app(name="other", help="Other.")
        other.group = (Group("Deprecated"),)
        other.command(_clean, name="x")
        app.command(other)
        app.command(_option_in(Group("Global options")), name="opt")
        app.command(_option_in(Group("Deprecated")), name="dep")
        lab = create_app(name="lab", help="Lab.")
        lab.group_commands = Group("Plugins")
        lab.command(_clean, name="y")
        app.command(lab)
        return app

    assert sorted(_composed(factory)) == [
        "svc dep::reserved-panel::Deprecated",
        "svc lab::reserved-panel::Plugins",
        "svc opt::reserved-panel::Global options",
        "svc other::reserved-panel::Deprecated",
        "svc set::reserved-panel::Experimental",
    ]


def test_core_panels_pass_for_a_marked_plugin_and_its_marks() -> None:
    def factory() -> App:
        app = create_app(name="svc", help="Service.")
        app.command(_clean, name="set")
        lab = create_app(name="lab", help="Lab.", stability=experimental)
        lab.command(_clean, name="y")
        app.command(lab)
        return app

    assert _composed(factory) == []
    assert _composed(factory, stability=experimental) == []


@pytest.mark.parametrize(
    ("group", "flagged"),
    [
        (Group("Extra"), True),
        (Group("Late", sort_key=100), True),
        (Group("Named", sort_key="a"), True),
        (Group("Early", sort_key=10), False),
        (Group.create_ordered("Ordered", sort_key=5), False),
        (Group("Hidden", show=False), False),
    ],
    ids=["unkeyed", "key-100", "text-key", "key-10", "ordered", "hidden"],
)
def test_unkeyed_panel_for_a_plugin_group_not_keyed_below_100(group: Group, flagged: bool) -> None:
    def factory() -> App:
        app = create_app(name="svc", help="Service.")
        app.command(_option_in(group), name="opt")
        return app

    expected = (
        [
            f"svc opt::unkeyed-panel::{group.name}: give it a sort_key below 100 "
            "so Global options stays the last panel"
        ]
        if flagged
        else []
    )
    assert _composed(factory) == expected


def test_unkeyed_panel_covers_command_groups() -> None:
    def factory() -> App:
        app = create_app(name="svc", help="Service.")
        app.command(_clean, name="set", group=Group("Admin"))
        return app

    assert [line.split("::")[:2] for line in _composed(factory)] == [["svc set", "unkeyed-panel"]]
