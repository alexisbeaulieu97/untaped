"""Run one command in every selected repo of a workspace, bounded in parallel."""

from __future__ import annotations

import signal
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.models import CommandResult, RepoSpec
from untaped.capabilities.workspace.domain.records import RunAction, RunOutcome
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capability_api import ErrorInfo, note_failure

if TYPE_CHECKING:
    from untaped.capabilities.workspace.application.ports import CommandRunner


@dataclass(frozen=True)
class RunTarget:
    """One repo directory to run the command in."""

    workspace: str
    spec: RepoSpec
    path: Path


class RunInRepos:
    """Run ``argv`` in each target; every target runs unless ``fail_fast`` stops scheduling."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        parallel: int,
        timeout: float,
        fail_fast: bool,
        on_done: Callable[[RunOutcome], None] | None = None,
    ) -> None:
        self._runner = runner
        self._parallel = max(1, parallel)
        self._timeout = timeout
        self._fail_fast = fail_fast
        self._on_done = on_done

    def __call__(self, targets: Sequence[RunTarget], argv: Sequence[str]) -> list[RunOutcome]:
        rows: dict[int, RunOutcome] = {}
        pending: dict[Future[CommandResult], int] = {}
        queue = deque(enumerate(targets))
        stop = False
        pool = ThreadPoolExecutor(max_workers=self._parallel)
        try:
            while True:
                # Rows that already finished come first: a failure stops the next start.
                stop = self._reap(rows, targets, pending, block=False) or stop
                if queue and not stop and len(pending) < self._parallel:
                    index, target = queue.popleft()
                    if target.path.is_dir():
                        pending[pool.submit(self._execute, target, argv)] = index
                    else:
                        stop = self._finish(rows, index, self._missing(target)) or stop
                    continue
                if not pending:
                    break
                stop = self._reap(rows, targets, pending, block=True) or stop
        except BaseException:
            # Ctrl-C: the commands run in their own sessions, so stop them explicitly.
            pool.shutdown(wait=False, cancel_futures=True)
            self._runner.cancel()
            raise
        pool.shutdown()
        for index, target in queue:
            self._finish(rows, index, _skipped(target))
        return [rows[i] for i in sorted(rows)]

    def _reap(
        self,
        rows: dict[int, RunOutcome],
        targets: Sequence[RunTarget],
        pending: dict[Future[CommandResult], int],
        *,
        block: bool,
    ) -> bool:
        """Record every finished run (waiting for one when ``block``); whether to stop.

        Runs that finish while ``on_done`` reports a row are recorded too.
        """
        timeout = None if block else 0
        stop = False
        while pending:
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            timeout = 0
            for future in done:
                index = pending.pop(future)
                row = self._row(targets[index], future.result())
                stop = self._finish(rows, index, row) or stop
        return stop

    def _finish(self, rows: dict[int, RunOutcome], index: int, row: RunOutcome) -> bool:
        """Record ``row``; whether scheduling should stop (a failure under ``fail_fast``)."""
        rows[index] = row
        if self._on_done is not None:
            self._on_done(row)
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
    error: ErrorInfo | None = None,
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
    return _outcome(target, "failed", result, detail, note_failure(error))


def _skipped(target: RunTarget) -> RunOutcome:
    return _outcome(target, "skipped", None, "not run (--fail-fast)")
