"""Use case: run a shell command in each repo of a workspace."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from untaped.api import bounded_map
from untaped.capabilities.workspace.application.ports import Filesystem, ManifestReader, ShellRunner
from untaped.capabilities.workspace.application.repo_selector import select_repos
from untaped.capabilities.workspace.domain import (
    DEFAULT_FOREACH_TIMEOUT,
    ForeachOutcome,
    Repo,
    Workspace,
)
from untaped.capabilities.workspace.errors import UnmatchedRepoFilter


class Foreach:
    def __init__(
        self,
        manifests: ManifestReader,
        *,
        runner: ShellRunner,
        fs: Filesystem,
    ) -> None:
        self._manifests = manifests
        self._runner = runner
        self._fs = fs

    def __call__(
        self,
        workspace: Workspace,
        *,
        command: str,
        parallel: int = 1,
        continue_on_error: bool = False,
        only: Sequence[str] | None = None,
        timeout: float = DEFAULT_FOREACH_TIMEOUT,
        on_result: Callable[[ForeachOutcome], None] | None = None,
    ) -> list[ForeachOutcome]:
        """Run ``command`` in each selected repo; return outcomes in manifest order.

        ``on_result`` is called on the calling thread as each repo
        finishes (completion order when ``parallel > 1``), so callers can
        stream output. Fail-fast (no ``continue_on_error``) stops queued
        repos from starting; in-flight commands finish and are reported.
        Anything escaping — including ``KeyboardInterrupt`` — cancels
        queued work instead of draining it.
        """
        manifest = self._manifests.read(workspace.path)
        repos, unmatched = select_repos(manifest, only)
        if unmatched:
            raise UnmatchedRepoFilter(unmatched)

        stop = threading.Event()
        outcomes: list[ForeachOutcome] = []

        def _run(repo: Repo) -> ForeachOutcome | None:
            if stop.is_set():
                return None
            return self._run_one(workspace, repo, command, timeout)

        def _collect(_repo: Repo, outcome: ForeachOutcome | None) -> None:
            if outcome is None:
                return
            outcomes.append(outcome)
            if outcome.returncode != 0 and not continue_on_error:
                stop.set()
            if on_result is not None:
                on_result(outcome)

        bounded_map(_run, repos, concurrency=max(parallel, 1), on_each=_collect)
        order = {repo.name: i for i, repo in enumerate(repos)}
        outcomes.sort(key=lambda o: order.get(o.repo, len(order)))
        return outcomes

    def _run_one(
        self, workspace: Workspace, repo: Repo, command: str, timeout: float
    ) -> ForeachOutcome:
        local = workspace.path / repo.name
        if not self._fs.is_dir(local):
            return ForeachOutcome(
                workspace=workspace.name,
                repo=repo.name,
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"not cloned: {local}",
                duration_s=0.0,
            )
        start = time.perf_counter()
        try:
            completed = self._runner(command, local, timeout=timeout)
        except FileNotFoundError as exc:
            return ForeachOutcome(
                workspace=workspace.name,
                repo=repo.name,
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(exc),
                duration_s=time.perf_counter() - start,
            )
        return ForeachOutcome(
            workspace=workspace.name,
            repo=repo.name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=time.perf_counter() - start,
        )
