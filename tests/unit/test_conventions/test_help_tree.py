"""Help-tree rules on synthetic commands: write declarations and their flags."""

from __future__ import annotations

import inspect
from typing import Annotated

from cyclopts import App, Parameter

from untaped.conventions.help_tree import command_violations
from untaped.sdk import create_app, writes

_ALL_FLAGS = frozenset({"--yes", "--dry-run", "--format"})
_FLAG_PARAMS: dict[str, tuple[str, type]] = {
    "--yes": ("yes", bool),
    "--dry-run": ("dry_run", bool),
    "--format": ("format", str),
}


def _leaf(*, declare: str | None, flags: frozenset[str] = _ALL_FLAGS) -> App:
    """A ``demo nuke`` command exposing exactly ``flags``, declared as ``declare``."""

    def body(**_: object) -> None:
        """Do it."""

    body.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                _FLAG_PARAMS[flag][0],
                inspect.Parameter.KEYWORD_ONLY,
                default=False if _FLAG_PARAMS[flag][1] is bool else "table",
                annotation=Annotated[
                    _FLAG_PARAMS[flag][1],
                    Parameter(name=flag, negative="", help=f"The {flag} option."),
                ],
            )
            for flag in sorted(flags)
        ]
    )
    body.__annotations__ = {}
    if declare == "write":
        body = writes(body)
    elif declare == "destructive":
        body = writes(destructive=True)(body)
    app = create_app(name="demo")
    app.command(body, name="nuke")
    return app["nuke"]


def _violations(*, declare: str | None, flags: frozenset[str] = _ALL_FLAGS) -> set[tuple[str, str]]:
    return set(command_violations(("demo", "nuke"), _leaf(declare=declare, flags=flags)))


def test_flags_without_a_declaration_are_flagged() -> None:
    assert _violations(declare=None) == {("undeclared-write", "--dry-run --yes")}


def test_declared_write_without_format_is_flagged() -> None:
    assert _violations(declare="write", flags=frozenset()) == {("mutation-format", "nuke")}


def test_declared_destructive_without_a_control_is_flagged() -> None:
    no_dry_run = _ALL_FLAGS - {"--dry-run"}
    no_yes = _ALL_FLAGS - {"--yes"}
    assert _violations(declare="destructive", flags=no_dry_run) == {
        ("destructive-controls", "--dry-run")
    }
    assert _violations(declare="destructive", flags=no_yes) == {("destructive-controls", "--yes")}


def test_declared_command_with_any_name_and_all_flags_is_clean() -> None:
    assert _violations(declare="destructive") == set()
    assert _violations(declare="write") == set()
