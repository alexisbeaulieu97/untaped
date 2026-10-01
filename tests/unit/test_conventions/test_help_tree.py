"""Help-tree rules on synthetic commands: write declarations and their flags."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from cyclopts import Parameter

from untaped.conventions.help_tree import command_violations
from untaped.sdk import create_app, writes

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


def _violations(command: Callable[..., None]) -> set[tuple[str, str]]:
    """The help-tree violations of ``command`` registered as ``demo nuke``."""
    app = create_app(name="demo")
    app.command(command, name="nuke")
    return set(command_violations(("demo", "nuke"), app["nuke"]))


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
