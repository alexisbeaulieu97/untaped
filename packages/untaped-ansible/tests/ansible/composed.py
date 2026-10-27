"""Run an ansible command the way an invocation does: git, github and ansible composed.

ansible reads GitHub through github's ``api``, and github's token belongs to
the composed github plugin, so a command that reaches GitHub needs it composed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from cyclopts import App

from untaped.sdk.testing import CliInvoker, CliResult, compose_with
from untaped_ansible import SPEC as ANSIBLE
from untaped_git.plugin import Git
from untaped_github.plugin import Github


@contextmanager
def composed_run() -> Iterator[None]:
    """The block runs as an ansible command would: under a run composing git, github and
    ansible (a cache it fills is then ansible's, by version, as a command reads it)."""
    with compose_with(Git, Github, ANSIBLE):
        yield


def invoke(app: App, args: Sequence[str], *, input: str | None = None) -> CliResult:
    """``app`` with ``args``, under a run composing git, github and ansible."""
    with composed_run():
        return CliInvoker().invoke(app, list(args), input=input)
