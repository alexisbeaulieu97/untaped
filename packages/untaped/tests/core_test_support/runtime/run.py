"""Runs for tests: blocks a plugin class holds, ``--quiet`` on a run, a started line, held runs."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from untaped.runtime.root_options import RootLine, _root_options, applied_root_options
from untaped.runtime.run import build_run, composition_of, entered, start_run
from untaped.sdk._run import Composition, Run, run_or_none

if TYPE_CHECKING:
    from untaped.runtime.declaration import Declaration
    from untaped.runtime.registry import CompositionResult


def run_owning(home: Path, package: str, plugin: str) -> Run:
    """A run whose composition gives ``package``'s classes to ``plugin``; no settings, no sink."""
    return Run(
        locate_home=lambda: home,
        composition=Composition(packages={package: plugin}),
        picks=lambda _section, model: model(),
        diag=None,  # type: ignore[arg-type]  # these tests write nothing to stderr
        terminal=lambda: pytest.fail("no terminal in this test"),
    )


def quiet(run: Run) -> Run:
    """Put ``--quiet`` on ``run``'s policy, as the token pass writes it; the run."""
    run.set_policy(replace(run.policy, quiet=True))
    return run


@contextmanager
def started_line(
    tokens: Sequence[str], declaration: Declaration | None = None
) -> Iterator[RootLine]:
    """The token pass over ``tokens`` onto a run started for the block, as an invocation's.

    The root flags it takes are the run's, so they end with the block.
    """
    with entered(start_run()) as run:
        yield applied_root_options(run, tokens, _root_options(), declaration)


@dataclass
class _Held:
    """The run :func:`hold_run` keeps current until :func:`holding_runs` ends."""

    stack: ExitStack
    run: Run | None = None


_HELD: list[_Held] = []


@contextmanager
def holding_runs() -> Iterator[None]:
    """Let :func:`hold_run` keep a run current until the block ends (a fixture's body)."""
    with ExitStack() as stack:
        _HELD.append(_Held(stack))
        try:
            yield
        finally:
            _HELD.pop()


def hold_run(result: CompositionResult) -> None:
    """Under :func:`holding_runs`, enter a run of ``result`` until the block ends.

    What a plugin class holds (a provider, a check, a credential) reads its
    plugin and settings from the run, as it would inside a command, so a test
    that composes and then calls into the composition outside any invocation
    holds one. It replaces only the run it entered before, so a run the test
    enters itself (``home_run``, ``invocation()``) stays current, and is
    given this composition when it has none yet, as an invocation's is.
    """
    if not _HELD:
        return
    held, current = _HELD[-1], run_or_none()
    if current is not None and current is not held.run:
        if not current.attached:
            current.attach(composition_of(result.mount.declaration, result.mount.settings))
        return
    held.stack.close()
    held.run = build_run(result.mount.declaration, result.mount.settings)
    held.stack.enter_context(entered(held.run))
