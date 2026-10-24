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
    steps: Positional[list[str]] = Field(description="name=action, one per step.")
    end: str = Field("", description="Raise failed or matched after the rows.")
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


def _run(*argv: str) -> CliResult:
    root = build_root_app(candidates=[EXITS])
    return CliInvoker().invoke(root.meta, ["exits", *argv, "--format", "json"])


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
