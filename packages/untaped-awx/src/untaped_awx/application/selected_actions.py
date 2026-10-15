"""Bounded scheduling for independent actions on already validated fixed targets.

A failed action's outcome keeps its exception (``error``) and the row's
structured error (``error_info``, counted toward the run's exit code).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from untaped.sdk import Failure
from untaped_awx.application.scheduling import Schedule, ScheduleInterruptedError, dooms_batch
from untaped_awx.application.selection import SelectedResource


@dataclass(frozen=True)
class SelectedActionOutcome[T]:
    target: SelectedResource
    action: Literal["completed", "failed", "skipped"]
    result: T | None = None
    detail: str | None = None
    error: Exception | None = field(default=None, repr=False)
    """Typed evidence for the caller; never render the unsanitized exception."""
    error_info: Failure | None = None
    """The failure's category, system and sanitized ``detail``: a row's ``error``."""

    def row_error(self) -> dict[str, Any]:
        """``{"error": ...}`` for the row of a failed action, else ``{}``."""
        if self.error_info is None:
            return {}
        return {"error": self.error_info.model_dump(mode="json")}


class ActionsInterruptedError(KeyboardInterrupt):
    """Ctrl-C during submission; ``outcomes`` covers every action already submitted."""

    def __init__(self, outcomes: list[SelectedActionOutcome[Any]]) -> None:
        super().__init__()
        self.outcomes = outcomes


def run_selected_actions[T](
    targets: Sequence[SelectedResource],
    worker: Callable[[SelectedResource], T],
    *,
    parallel: int = 1,
    fail_fast: bool = False,
    error_detail: Callable[[Exception, SelectedResource], str] = lambda exc, _: str(exc),
) -> list[SelectedActionOutcome[T]]:
    """Run every target unless a failure stops new submissions; keep target order.

    A failure stops them under ``fail_fast``, and a rejected token or broken
    setup (:func:`dooms_batch`) always does.

    Ctrl-C stops new submissions, lets in-flight submissions finish, and
    raises :class:`ActionsInterruptedError` with every outcome gathered so far.
    """
    schedule = Schedule(parallel=parallel)

    def run(index: int) -> SelectedActionOutcome[T]:
        target = targets[index]
        try:
            return SelectedActionOutcome(target, "completed", worker(target))
        except Exception as exc:
            if fail_fast or dooms_batch(exc):
                schedule.stop()
            detail = error_detail(exc, target)
            return SelectedActionOutcome(
                target,
                "failed",
                detail=detail,
                error=exc,
                error_info=Failure.from_exception(exc, message=detail),
            )

    try:
        outcomes = schedule.run(
            len(targets),
            run,
            skipped=lambda index: SelectedActionOutcome(
                targets[index], "skipped", detail="skipped after a runtime failure"
            ),
        )
    except ScheduleInterruptedError as interrupted:
        submitted = [
            outcome
            for _index, outcome in sorted(interrupted.results.items())
            if isinstance(outcome, SelectedActionOutcome) and outcome.action != "skipped"
        ]
        raise ActionsInterruptedError(submitted) from None
    return [outcomes[index] for index in range(len(targets))]
