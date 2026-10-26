"""Runs for tests: blocks a plugin class holds, ``--quiet`` on a run, and a started line."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from untaped.runtime.root_options import RootLine, _root_options, applied_root_options
from untaped.runtime.run import entered, start_run
from untaped.sdk._run import Composition, Run

if TYPE_CHECKING:
    from untaped.runtime.declaration import Declaration


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
    with (
        entered(start_run()) as run,
        applied_root_options(run, tokens, _root_options(), declaration) as line,
    ):
        yield line
