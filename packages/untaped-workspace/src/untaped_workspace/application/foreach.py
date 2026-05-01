"""Use case: run a shell command in each repo of a workspace."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from untaped_workspace.domain import Repo, Workspace, WorkspaceManifest


class ForeachOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str
    returncode: int
    stdout: str
    stderr: str


class _ManifestReader(Protocol):
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...


CommandRunner = Callable[[str, Path], "subprocess.CompletedProcess[str]"]


def _default_runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, shell=True, cwd=cwd, text=True, capture_output=True, check=False)


class Foreach:
    def __init__(
        self,
        manifests: _ManifestReader,
        *,
        runner: CommandRunner = _default_runner,
    ) -> None:
        self._manifests = manifests
        self._runner = runner

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
        repos: list[Repo],
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
        repos: list[Repo],
        command: str,
        parallel: int,
        continue_on_error: bool,
    ) -> list[ForeachOutcome]:
        outcomes: list[ForeachOutcome] = []
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(self._run_one, workspace, repo, command): repo for repo in repos}
            for fut in futures:
                outcome = fut.result()
                outcomes.append(outcome)
                if outcome.returncode != 0 and not continue_on_error:
                    for other in futures:
                        other.cancel()
                    break
        # Sort outcomes back to declared order so output is stable
        order = {repo.name: i for i, repo in enumerate(repos)}
        outcomes.sort(key=lambda o: order.get(o.repo, len(order)))
        return outcomes

    def _run_one(self, workspace: Workspace, repo: Repo, command: str) -> ForeachOutcome:
        local = workspace.path / repo.name
        if not local.is_dir():
            return ForeachOutcome(
                workspace=workspace.name,
                repo=repo.name,
                returncode=-1,
                stdout="",
                stderr=f"not cloned: {local}",
            )
        try:
            completed = self._runner(command, local)
        except FileNotFoundError as exc:
            return ForeachOutcome(
                workspace=workspace.name,
                repo=repo.name,
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )
        return ForeachOutcome(
            workspace=workspace.name,
            repo=repo.name,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
