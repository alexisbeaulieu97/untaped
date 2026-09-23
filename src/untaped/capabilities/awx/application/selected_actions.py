"""Bounded scheduling for independent actions on already validated fixed targets."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from untaped.capabilities.awx.application.scheduling import Schedule, ScheduleInterruptedError
from untaped.capabilities.awx.application.selection import SelectedResource


@dataclass(frozen=True)
class SelectedActionOutcome[T]:
    target: SelectedResource
    action: Literal["completed", "failed", "skipped"]
    result: T | None = None
    detail: str | None = None
    error: Exception | None = field(default=None, repr=False)
    """Typed evidence for the caller; never render the unsanitized exception."""


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
    continue_on_error: bool = False,
    error_detail: Callable[[Exception, SelectedResource], str] = lambda exc, _: str(exc),
) -> list[SelectedActionOutcome[T]]:
    """Stop new submissions on failure; retain in-flight results in target order.

    Ctrl-C stops new submissions, lets in-flight submissions finish, and
    raises :class:`ActionsInterruptedError` with every outcome gathered so far.
    """
    schedule = Schedule(parallel=parallel)

    def run(index: int) -> SelectedActionOutcome[T]:
        target = targets[index]
        try:
            return SelectedActionOutcome(target, "completed", worker(target))
        except Exception as exc:
            if not continue_on_error:
                schedule.stop()
            return SelectedActionOutcome(
                target, "failed", detail=error_detail(exc, target), error=exc
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
