"""Use case: run a shell command in each repo of a workspace."""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from untaped_workspace.application.ports import Filesystem, ManifestReader, ShellRunner
from untaped_workspace.domain import ForeachOutcome, Repo, Workspace


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
    ) -> list[ForeachOutcome]:
        manifest = self._manifests.read(workspace.path)
        repos = manifest.repos

        if parallel <= 1:
            return self._run_serial(workspace, repos, command, continue_on_error)
        return self._run_parallel(workspace, repos, command, parallel, continue_on_error)

    def _run_serial(
        self,
        workspace: Workspace,
        repos: Sequence[Repo],
        command: str,
        continue_on_error: bool,
    ) -> list[ForeachOutcome]:
        outcomes: list[ForeachOutcome] = []
        for repo in repos:
            outcome = self._run_one(workspace, repo, command)
            outcomes.append(outcome)
            if outcome.returncode != 0 and not continue_on_error:
                break
        return outcomes

    def _run_parallel(
        self,
        workspace: Workspace,
        # ``Sequence`` — not ``Iterable`` — because the body walks ``repos``
        # twice (submit pass and the ``enumerate`` ordering pass below); a
        # single-shot iterator would exhaust on the first walk and silently
        # empty the ``order`` map.
        repos: Sequence[Repo],
        command: str,
        parallel: int,
        continue_on_error: bool,
    ) -> list[ForeachOutcome]:
        outcomes: list[ForeachOutcome] = []
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(self._run_one, workspace, repo, command): repo for repo in repos}
            for fut in as_completed(futures):
                outcome = fut.result()
                outcomes.append(outcome)
                if outcome.returncode != 0 and not continue_on_error:
                    for other in futures:
                        other.cancel()
                    break
        order = {repo.name: i for i, repo in enumerate(repos)}
        outcomes.sort(key=lambda o: order.get(o.repo, len(order)))
        return outcomes

    def _run_one(self, workspace: Workspace, repo: Repo, command: str) -> ForeachOutcome:
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
            completed = self._runner(command, local)
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
