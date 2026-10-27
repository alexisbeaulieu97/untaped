"""What the recipe tests share: ``untaped recipe`` run under an install, and its library.

The library is the default one, ``plugins/recipe/library/`` under the
test's home; a test that sets ``recipe.*`` does it through
``UNTAPED_RECIPE__*`` variables or ``settings=``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from untaped.sdk import Command, Diagnostic, Failure, Output, Plugin, Record, home
from untaped.sdk.testing import Install, Ran, install
from untaped_recipe.plugin import RecipePlugin
from untaped_recipe.settings import RecipeSettings


@dataclass(frozen=True)
class Result:
    """What one ``untaped recipe`` run did, its stderr as one text."""

    exit_code: int
    stdout: str
    stderr: str
    ran: Ran

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self.ran.diagnostics

    @property
    def failures(self) -> list[tuple[str | None, str]]:
        """Each counted error line, as ``(item, message)``."""
        return [(d.item, d.message) for d in self.diagnostics if isinstance(d, Failure)]

    @property
    def said(self) -> str:
        """What the diagnostics said, one message a line, whichever format stderr took."""
        return "".join(f"{diagnostic.message}\n" for diagnostic in self.diagnostics)


class RepoStatus(Record):
    """A workspace repo as ``apply --stdin`` reads it from a pipe, with fields recipes read."""

    path: str
    target_path: str | None = None
    repo: str | None = None
    token: str | None = None
    team: str | None = None
    collections: list[str] | None = None


class WorkspaceSettings(BaseModel):
    """Nothing to configure."""


class Status(Command[WorkspaceSettings]):
    """List no repos: the command that declares the ``workspace.repo_status`` kind."""

    output: Output[RepoStatus]

    def run(self) -> Iterator[RepoStatus]:
        yield from ()


class Workspace(Plugin):
    """A stand-in for workspace, whose ``workspace.repo_status`` rows ``apply`` reads."""

    settings = WorkspaceSettings
    commands = (Status,)


def installed(settings: RecipeSettings | None = None) -> Install:
    """recipe installed in the test's home, with ``settings`` as its section."""
    return install(RecipePlugin, Workspace, settings=() if settings is None else [settings])


def run(
    args: Sequence[str], *, input: str | None = None, settings: RecipeSettings | None = None
) -> Result:
    """``untaped recipe ARGS``, with ``input`` piped on stdin (rows of the stand-in workspace's
    kind included)."""
    return _result(installed(settings).cli(["recipe", *args], input=input))


def run_root(args: Sequence[str], *, input: str | None = None) -> Result:
    """``untaped ARGS``, recipe installed: a root command such as ``doctor`` or ``docs``."""
    return _result(installed().cli(list(args), input=input))


def library_dir() -> Path:
    """The library the runs use: the default one under the test's home."""
    return home().expanduser() / "plugins" / "recipe" / "library"


def _result(ran: Ran) -> Result:
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)
