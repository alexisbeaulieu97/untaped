"""Standardized destructive-batch UX: preview → confirm → execute → summarize.

:func:`batch_apply` is the shared front-end a tool uses to act on a *set* of
already-resolved items (typically read from a ``--format pipe`` stream via
:func:`untaped.stdin.read_identifiers` / :func:`untaped.stdin.read_records`). It
previews the targets, gates a destructive verb behind a confirmation (or
``--yes``), runs the per-item ``action`` under a progress indicator, and reports
per-item failures — leaving the caller to render the outcome rows and choose the
exit code (summary shape and prior-failure composition are caller concerns).

This is distinct from the ``apply`` *command* some tools expose (a file-based
declarative reconciler): :func:`batch_apply` is a pipe-consumer mutation helper,
not a YAML applier, and its ``--yes`` means "skip the confirm" (not "write").
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from untaped.cli import echo
from untaped.errors import ConfigError, UntapedError
from untaped.ui import UiContext


@dataclass(frozen=True)
class BatchOutcome[T, R]:
    """The result of a :func:`batch_apply` run.

    ``results`` pairs each successfully actioned item with its action result so
    callers keep the originating input (e.g. for a ``(name, job)`` monitor
    phase). ``planned_rows`` is ``describe(item)`` for every input — reused for
    the ``--dry-run`` output and any summary so callers don't recompute it.
    """

    results: list[tuple[T, R]]
    failed: int
    planned_rows: list[dict[str, object]]

    @property
    def any_failed(self) -> bool:
        return self.failed > 0

    @property
    def total(self) -> int:
        return len(self.planned_rows)


def _stdin_is_interactive() -> bool:
    # Whether stdin is a real terminal we can prompt on. False when stdin is the
    # data pipe (``list --format pipe | <verb> --stdin``), so the gate never
    # tries to read a y/N from the data — it refuses with a --yes hint instead.
    return sys.stdin.isatty()


def batch_apply[T, R](
    items: Sequence[T],
    action: Callable[[T], R],
    *,
    verb: str,
    noun: str,
    label: Callable[[T], str],
    describe: Callable[[T], dict[str, object]],
    ui: UiContext,
    destructive: bool = False,
    assume_yes: bool = False,
    preview_only: bool = False,
) -> BatchOutcome[T, R]:
    """Preview, optionally confirm, then run ``action`` over ``items``.

    ``verb``/``noun`` phrase the preview and progress ("delete"/"JobTemplate").
    ``label(item)`` is the identifier shown in progress and ``error: <label>: …``
    lines; ``describe(item)`` is the row used for the preview and ``planned_rows``.

    A **destructive** verb gates execution: with ``assume_yes`` it proceeds; on an
    interactive stdin it previews then prompts (decline → no action run);
    otherwise it raises :class:`ConfigError` (stdin is the data pipe, so there is
    nothing to confirm against — pass ``--yes``). Benign verbs and ``--yes`` skip
    straight to execution. ``preview_only`` (``--dry-run``) returns ``planned_rows``
    without running ``action``.

    Per-item :class:`UntapedError` is caught and counted; anything else
    propagates. The helper never renders the summary or raises ``SystemExit`` —
    the caller owns stdout and the exit code.
    """
    planned_rows = [describe(item) for item in items]
    if not items or preview_only:
        return BatchOutcome(results=[], failed=0, planned_rows=planned_rows)
    total = len(planned_rows)
    if destructive and not assume_yes:
        if _stdin_is_interactive():
            echo(f"About to {verb} {total} {noun}(s):", err=True)
            for row in planned_rows:
                echo("  - " + "\t".join(str(value) for value in row.values()), err=True)
            if not ui.confirm("Continue?"):
                return BatchOutcome(results=[], failed=0, planned_rows=planned_rows)
        else:
            raise ConfigError(f"{verb} requires --yes when stdin is not interactive")
    results: list[tuple[T, R]] = []
    failed = 0
    with ui.progress(f"{verb.capitalize()} {total} {noun}(s)") as handle:
        for index, item in enumerate(items, 1):
            handle.update(label(item), fraction=index / total)
            try:
                results.append((item, action(item)))
            except UntapedError as exc:
                echo(f"error: {label(item)}: {exc}", err=True)
                failed += 1
    return BatchOutcome(results=results, failed=failed, planned_rows=planned_rows)
