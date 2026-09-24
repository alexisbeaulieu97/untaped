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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from untaped.cli import echo, format_error
from untaped.errors import ExitCode, OperationCancelledError, UntapedError
from untaped.messages import plural
from untaped.ui import UiContext


@dataclass(frozen=True)
class BatchOutcome[T, R]:
    """The result of a :func:`batch_apply` run.

    ``results`` pairs each successfully actioned item with its action result so
    callers keep the originating input (e.g. for a ``(name, job)`` monitor
    phase). ``planned_rows`` is ``describe(item)`` for every input — reused for
    the ``--dry-run`` output and any summary so callers don't recompute it.
    ``cancelled`` marks a declined confirmation (:func:`finish` exits ``1``).
    """

    results: list[tuple[T, R]]
    failed: int
    planned_rows: list[dict[str, object]]
    cancelled: bool = False
    """The user declined the confirmation; nothing ran."""

    @property
    def any_failed(self) -> bool:
        return self.failed > 0

    @property
    def total(self) -> int:
        return len(self.planned_rows)


def finish(outcome: BatchOutcome[Any, Any] | bool, *, predicate_hit: bool = False) -> None:
    """Turn a batch/aggregate outcome into the suite's exit-code contract.

    Raises ``SystemExit(1)`` when any item failed ("3 of 5 deleted" is a
    failure) or the confirmation was declined (after printing the standard
    ``cancelled; no changes made`` line), ``SystemExit(3)`` when
    ``predicate_hit`` (``--check`` drift, ``--fail-on-match``, ``--strict``)
    and nothing failed, and returns otherwise. Accepts a :class:`BatchOutcome`
    or a bare ``any_failed``-style bool so non-batch aggregate paths
    (``resolve_each`` callers, hand-rolled loops) share the same guarantee.
    """
    if isinstance(outcome, BatchOutcome) and outcome.cancelled:
        echo(str(OperationCancelledError()), err=True)
        raise SystemExit(ExitCode.FAILURE)
    failed = outcome.any_failed if isinstance(outcome, BatchOutcome) else bool(outcome)
    if failed:
        raise SystemExit(ExitCode.FAILURE)
    if predicate_hit:
        raise SystemExit(ExitCode.PREDICATE)


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
    render_generic_preview: bool = True,
    preview: Callable[[Sequence[dict[str, object]]], None] | None = None,
) -> BatchOutcome[T, R]:
    """Preview, optionally confirm, then run ``action`` over ``items``.

    ``verb``/``noun`` phrase the preview and progress ("delete"/"JobTemplate").
    ``label(item)`` is the identifier shown in progress and ``error: <label>: …``
    lines; ``describe(item)`` is the row used for the preview and ``planned_rows``.

    A **destructive** verb gates execution: with ``assume_yes`` it proceeds;
    otherwise it previews then prompts through :meth:`UiContext.confirm_action`
    — on ``ui.stdin`` when it is a TTY, else on the controlling terminal (stdin
    is the data pipe) — and a decline returns ``cancelled=True`` with no action
    run. With no terminal at all it raises :class:`UsageError` (exit 2; pass
    ``--yes``). Callers can pass
    ``preview`` to render the planned rows for the confirmation preview; the
    generic delete-style row dump remains the default. Callers that already
    rendered a richer preview can pass ``render_generic_preview=False`` to keep
    the confirmation prompt without the generic tab-row preview. Benign verbs
    and ``--yes`` skip straight to execution. ``preview_only`` (``--dry-run``)
    returns ``planned_rows`` without running ``action``.

    Per-item :class:`UntapedError` is caught, counted, and printed as
    ``error: <label>: …`` on ``ui.stderr`` (formatted like ``report_errors``,
    without garbling the progress spinner); anything else propagates. The
    helper never renders the summary or raises ``SystemExit`` — the caller
    owns stdout and the exit code.
    """
    planned_rows = [describe(item) for item in items]
    if not items or preview_only:
        return BatchOutcome(results=[], failed=0, planned_rows=planned_rows)
    total = len(planned_rows)
    if destructive and not assume_yes:
        with ui.terminal(refusal=f"{verb} requires --yes when not interactive"):
            if preview is not None:
                preview(planned_rows)
            elif render_generic_preview:
                echo(f"About to {verb} {plural(total, noun)}:", err=True)
                for row in planned_rows:
                    echo("  - " + "\t".join(str(value) for value in row.values()), err=True)
            if not ui.confirm("Continue?"):
                return BatchOutcome(results=[], failed=0, planned_rows=planned_rows, cancelled=True)
    results: list[tuple[T, R]] = []
    failed = 0
    with ui.progress(f"{verb.capitalize()} {plural(total, noun)}") as handle:
        for index, item in enumerate(items, 1):
            handle.update(label(item), fraction=index / total)
            try:
                results.append((item, action(item)))
            except UntapedError as exc:
                handle.log(f"error: {label(item)}: {format_error(exc)}")
                failed += 1
    return BatchOutcome(results=results, failed=failed, planned_rows=planned_rows)
