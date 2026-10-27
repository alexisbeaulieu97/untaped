"""What the github tests share: ``untaped github`` run under an install, and its result.

The ``settings`` fixture is the ``github`` section the next run writes, by
field (``settings["server"] = {"url": ...}``); the conftest makes it the one
:func:`run` and :func:`installed` use, with a token from ``GH_TOKEN``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from untaped.sdk import Diagnostic
from untaped.sdk.testing import Install, Ran, install
from untaped_github.plugin import Github
from untaped_github.settings import GithubSettings

#: The ``github`` settings the next run uses (the ``settings`` fixture), by field.
type Settings = dict[str, Any]

_SETTINGS: ContextVar[Settings | None] = ContextVar("github_test_settings", default=None)


@dataclass(frozen=True)
class Result:
    """What one ``untaped github`` run did, its stderr as one text."""

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


@contextmanager
def using(settings: Settings) -> Iterator[None]:
    """Runs in the block write ``settings`` as the ``github`` section."""
    token = _SETTINGS.set(settings)
    try:
        yield
    finally:
        _SETTINGS.reset(token)


def installed() -> Install:
    """github (and git) installed with the test's ``github`` settings."""
    return install(Github, settings=GithubSettings(**(_SETTINGS.get() or {})))


def run(args: Sequence[str], *, input: str | None = None) -> Result:
    """``untaped github ARGS``, with ``input`` piped on stdin."""
    ran = installed().cli(["github", *args], input=input)
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)
