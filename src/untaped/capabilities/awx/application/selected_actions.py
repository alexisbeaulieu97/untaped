"""Bounded scheduling for independent actions on already validated fixed targets."""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Literal

from untaped.capabilities.awx.application.selection import SelectedResource


@dataclass(frozen=True)
class SelectedActionOutcome[T]:
    target: SelectedResource
    action: Literal["completed", "failed", "skipped"]
    result: T | None = None
    detail: str | None = None
    error: Exception | None = field(default=None, repr=False)
    """Typed evidence for the caller; never render the unsanitized exception."""


def run_selected_actions[T](
    targets: Sequence[SelectedResource],
    worker: Callable[[SelectedResource], T],
    *,
    parallel: int = 1,
    continue_on_error: bool = False,
    error_detail: Callable[[Exception, SelectedResource], str] = lambda exc, _: str(exc),
) -> list[SelectedActionOutcome[T]]:
    """Stop new submissions on failure; retain in-flight results in target order."""
    if parallel < 1:
        raise ValueError("parallel must be >= 1")
    parallel = min(parallel, 10)
    outcomes: dict[int, SelectedActionOutcome[T]] = {}
    in_flight: dict[Future[T], int] = {}
    next_index = 0
    stopped = False
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while next_index < len(targets) or in_flight:
            while not stopped and next_index < len(targets) and len(in_flight) < parallel:
                in_flight[pool.submit(worker, targets[next_index])] = next_index
                next_index += 1
            if not in_flight:
                break
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                index = in_flight.pop(future)
                target = targets[index]
                try:
                    outcomes[index] = SelectedActionOutcome(target, "completed", future.result())
                except Exception as exc:
                    outcomes[index] = SelectedActionOutcome(
                        target, "failed", detail=error_detail(exc, target), error=exc
                    )
                    if not continue_on_error:
                        stopped = True
    for index in range(next_index, len(targets)):
        outcomes[index] = SelectedActionOutcome(
            targets[index], "skipped", detail="skipped after a runtime failure"
        )
    return [outcomes[index] for index in range(len(targets))]
