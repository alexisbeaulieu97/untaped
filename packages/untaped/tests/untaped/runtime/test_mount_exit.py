"""The exit rule on a mounted command: counted rows, ``Failed`` and ``Matched`` after them."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated

from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import (
    NOT_COUNTED,
    Column,
    Command,
    Failed,
    Failure,
    Key,
    Matched,
    OutcomeRecord,
    Output,
    Plugin,
    Positional,
    UntapedError,
)
from untaped.sdk.testing import CliInvoker, CliResult


class Step(OutcomeRecord):
    """One step a command took."""

    name: Annotated[str, Key, Column]


class Remembered(OutcomeRecord):
    """A step whose failure is from an earlier run."""

    name: Annotated[str, Key, Column]
    failure: Annotated[Failure | None, NOT_COUNTED] = None


def _rejected() -> Failure:
    return Failure(message="token rejected", category="auth", system="acme")


class Walk(Command):
    """Take the steps named, then end as asked."""

    output: Output[Step]
    steps: Positional[list[str]] = Field(default_factory=list, description="name=action, each.")
    end: str = Field("", description="Raise failed, matched or broken after the rows.")
    rejected: bool = Field(False, description="Put a rejected token on the first row.")

    def run(self) -> Iterator[Step]:
        for index, step in enumerate(self.steps):
            name, _, action = step.partition("=")
            error = _rejected() if self.rejected and index == 0 else None
            yield Step(name=name, action=action or "done", error=error)
        if self.end == "failed":
            raise Failed()
        if self.end == "matched":
            raise Matched()
        if self.end == "broken":
            raise UntapedError("the service went away", category="unavailable")


class Recall(Command):
    """Show a failure remembered from an earlier run."""

    output: Output[Remembered]

    def run(self) -> Iterator[Remembered]:
        yield Remembered(name="old", action="failed", failure=_rejected())


class ExitsSettings(BaseModel):
    pass


class Exits(Plugin):
    """Exit codes, every way."""

    settings = ExitsSettings
    commands = (Walk, Recall)


EXITS = PluginCandidate(distribution="untaped-exits", name="exits", target=Exits)


def _run(*argv: str, fmt: str = "json") -> CliResult:
    root = build_root_app(candidates=[EXITS])
    return CliInvoker().invoke(root.meta, ["exits", *argv, "--format", fmt])


def _names(result: CliResult) -> list[str]:
    return [row["name"] for row in json.loads(result.stdout)]


def test_done_rows_exit_zero() -> None:
    result = _run("walk", "a", "b")
    assert (result.exit_code, _names(result)) == (0, ["a", "b"])


def test_a_row_that_says_it_failed_exits_one_without_an_error() -> None:
    for action in ("failed", "partial", "conflict"):
        result = _run("walk", "a", f"b={action}")
        assert (result.exit_code, _names(result)) == (1, ["a", "b"]), action


def test_failed_after_the_rows_prints_them_and_nothing_more() -> None:
    result = _run("walk", "a", "b", "--end", "failed")
    assert (result.exit_code, _names(result)) == (1, ["a", "b"])
    assert result.stderr == ""


def test_matched_exits_three_and_prints_nothing_more() -> None:
    result = _run("walk", "a", "--end", "matched")
    assert (result.exit_code, _names(result)) == (3, ["a"])
    assert result.stderr == ""


def test_a_counted_failure_beats_matched_and_failed() -> None:
    assert _run("walk", "a", "--rejected").exit_code == 4
    assert _run("walk", "a", "--rejected", "--end", "matched").exit_code == 4
    assert _run("walk", "a", "--rejected", "--end", "failed").exit_code == 4
    assert _run("walk", "a=failed", "--end", "matched").exit_code == 1


def test_a_remembered_failure_is_not_counted() -> None:
    result = _run("recall")
    assert (result.exit_code, _names(result)) == (0, ["old"])


def test_failed_after_no_rows_prints_an_empty_list() -> None:
    result = _run("walk", "--end", "failed")
    assert (result.exit_code, json.loads(result.stdout), result.stderr) == (1, [], "")


def test_failed_in_a_table_prints_the_rows_and_no_error_line() -> None:
    result = _run("walk", "a", "--end", "failed", fmt="table")
    assert result.exit_code == 1
    assert "a" in result.stdout
    assert "error" not in result.stderr


def test_rows_before_another_error_are_printed_then_the_error() -> None:
    result = _run("walk", "a", "--end", "broken")
    assert (result.exit_code, _names(result)) == (5, ["a"])
    assert "the service went away" in result.stderr
