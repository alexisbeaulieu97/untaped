"""Bounded scheduling for independent actions on already validated fixed targets."""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Literal

from untaped.capabilities.awx.application.selection import SelectedResource


@dataclass(frozen=True)
class SelectedActionOutcome[T]:
    target: SelectedResource
    action: Literal["completed", "failed", "skipped"]
    result: T | None = None
    detail: str | None = None
    error: Exception | None = field(default=None, repr=False)
    """Typed evidence for the caller; never render the unsanitized exception."""


class ActionsInterrupted(KeyboardInterrupt):
    """Ctrl-C during submission; ``outcomes`` covers every action already submitted."""

    def __init__(self, outcomes: list[SelectedActionOutcome[Any]]) -> None:
        super().__init__()
        self.outcomes = outcomes


# C901: bounded scheduling plus the Ctrl-C drain of in-flight submissions.
def run_selected_actions[T](  # noqa: C901
    targets: Sequence[SelectedResource],
    worker: Callable[[SelectedResource], T],
    *,
    parallel: int = 1,
    continue_on_error: bool = False,
    error_detail: Callable[[Exception, SelectedResource], str] = lambda exc, _: str(exc),
) -> list[SelectedActionOutcome[T]]:
    """Stop new submissions on failure; retain in-flight results in target order.

    Ctrl-C stops new submissions, lets in-flight submissions finish, and
    raises :class:`ActionsInterrupted` with every outcome gathered so far.
    """
    if parallel < 1:
        raise ValueError("parallel must be >= 1")
    parallel = min(parallel, 10)
    outcomes: dict[int, SelectedActionOutcome[T]] = {}
    in_flight: dict[Future[T], int] = {}
    next_index = 0
    stopped = False

    def collect(future: Future[T], index: int) -> bool:
        target = targets[index]
        try:
            outcomes[index] = SelectedActionOutcome(target, "completed", future.result())
        except Exception as exc:
            outcomes[index] = SelectedActionOutcome(
                target, "failed", detail=error_detail(exc, target), error=exc
            )
            return False
        return True

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        try:
            while next_index < len(targets) or in_flight:
                while not stopped and next_index < len(targets) and len(in_flight) < parallel:
                    in_flight[pool.submit(worker, targets[next_index])] = next_index
                    next_index += 1
                if not in_flight:
                    break
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    if not collect(future, in_flight.pop(future)) and not continue_on_error:
                        stopped = True
        except KeyboardInterrupt:
            for future, index in in_flight.items():
                try:
                    collect(future, index)
                except KeyboardInterrupt:
                    continue
            raise ActionsInterrupted([outcomes[i] for i in sorted(outcomes)]) from None
    for index in range(next_index, len(targets)):
        outcomes[index] = SelectedActionOutcome(
            targets[index], "skipped", detail="skipped after a runtime failure"
        )
    return [outcomes[index] for index in range(len(targets))]
