"""Run one command in every selected repo of a workspace, bounded in parallel."""

from __future__ import annotations

import signal
from collections import deque
from collections.abc import Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Failure
from untaped_workspace.domain.models import CommandResult, RepoSpec
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.records import RunAction, RunOutcome

if TYPE_CHECKING:
    from untaped_workspace.application.ports import CommandRunner


@dataclass(frozen=True)
class RunTarget:
    """One repo directory to run the command in."""

    workspace: str
    spec: RepoSpec
    path: Path


class RunInRepos:
    """Run ``argv`` in each target; every target runs unless ``fail_fast`` stops scheduling."""

    def __init__(
        self, runner: CommandRunner, *, parallel: int, timeout: float, fail_fast: bool
    ) -> None:
        self._runner = runner
        self._parallel = max(1, parallel)
        self._timeout = timeout
        self._fail_fast = fail_fast

    def __call__(self, targets: Sequence[RunTarget], argv: Sequence[str]) -> Iterator[RunOutcome]:
        """Each target's row as its run finishes; those ``fail_fast`` never started come last."""
        finished: list[RunOutcome] = []
        pending: dict[Future[CommandResult], RunTarget] = {}
        queue = deque(targets)
        stop = False
        pool = ThreadPoolExecutor(max_workers=self._parallel)
        try:
            while True:
                # Rows that already finished come first: a failure stops the next start.
                stop = self._reap(finished, pending, block=False) or stop
                if finished:
                    yield from finished
                    finished.clear()
                    continue  # runs that ended while a row was read count before the next start
                if queue and not stop and len(pending) < self._parallel:
                    target = queue.popleft()
                    if target.path.is_dir():
                        pending[pool.submit(self._execute, target, argv)] = target
                    else:
                        stop = self._finish(finished, self._missing(target)) or stop
                    continue
                if not pending:
                    break
                stop = self._reap(finished, pending, block=True) or stop
        except BaseException:
            # Ctrl-C, or the reader stopped: the commands run in their own sessions,
            # so stop them explicitly.
            pool.shutdown(wait=False, cancel_futures=True)
            self._runner.cancel()
            raise
        pool.shutdown()
        yield from finished
        yield from (_skipped(target) for target in queue)

    def _reap(
        self,
        finished: list[RunOutcome],
        pending: dict[Future[CommandResult], RunTarget],
        *,
        block: bool,
    ) -> bool:
        """Record every finished run (waiting for one when ``block``); whether to stop."""
        timeout = None if block else 0
        stop = False
        while pending:
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            timeout = 0
            for future in done:
                target = pending.pop(future)
                stop = self._finish(finished, self._row(target, future.result())) or stop
        return stop

    def _finish(self, finished: list[RunOutcome], row: RunOutcome) -> bool:
        """Record ``row``; whether scheduling should stop (a failure under ``fail_fast``)."""
        finished.append(row)
        return self._fail_fast and row.action == "failed"

    def _execute(self, target: RunTarget, argv: Sequence[str]) -> CommandResult:
        return self._runner.run(argv, cwd=target.path, env=_env(target), timeout=self._timeout)

    def _missing(self, target: RunTarget) -> RunOutcome:
        return _failed(target, WorkspaceError("missing", category="failed"), None, "missing")

    def _row(self, target: RunTarget, result: CommandResult) -> RunOutcome:
        if result.cancelled:
            return _outcome(target, "skipped", result, "cancelled")
        if result.timed_out:
            detail = f"timed out after {self._timeout:g}s"
        elif result.returncode:
            detail = _exit_detail(result.returncode)
        else:
            return _outcome(target, "ran", result)
        return _failed(target, WorkspaceError(detail, category="failed"), result, detail)


def _exit_detail(returncode: int) -> str:
    """``exit N``, or ``killed by SIGTERM`` for a signal death (a negative code)."""
    if returncode > 0:
        return f"exit {returncode}"
    try:
        return f"killed by {signal.Signals(-returncode).name}"
    except ValueError:
        return f"killed by signal {-returncode}"


def _env(target: RunTarget) -> dict[str, str]:
    spec = target.spec
    return {
        "UNTAPED_WORKSPACE": target.workspace,
        "UNTAPED_REPO": spec.name,
        "UNTAPED_BRANCH": spec.branch or "",
        "UNTAPED_BASE": spec.base,
        "UNTAPED_READ_ONLY": "1" if spec.read_only else "0",
    }


def _outcome(
    target: RunTarget,
    action: RunAction,
    result: CommandResult | None,
    detail: str = "",
    error: Failure | None = None,
) -> RunOutcome:
    result = result or _NOT_RUN
    return RunOutcome(
        workspace=target.workspace,
        repo=target.spec.name,
        dir=target.spec.dir,
        action=action,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_s=result.duration_s,
        detail=detail,
        target_path=target.path,
        error=error,
    )


_NOT_RUN = CommandResult(returncode=None, stdout="", stderr="", duration_s=0.0, timed_out=False)
"""The result fields of a row whose command never ran."""


def _failed(
    target: RunTarget, error: WorkspaceError, result: CommandResult | None, detail: str
) -> RunOutcome:
    return _outcome(target, "failed", result, detail, Failure.from_exception(error))


def _skipped(target: RunTarget) -> RunOutcome:
    return _outcome(target, "skipped", None, "not run (--fail-fast)")
