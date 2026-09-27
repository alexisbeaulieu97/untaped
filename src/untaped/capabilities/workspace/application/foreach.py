"""Use case: run a shell command in each repo of one or more workspaces."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from untaped.capabilities.workspace.application.ports import Filesystem, ManifestReader, ShellRunner
from untaped.capabilities.workspace.application.repo_selector import select_repos
from untaped.capabilities.workspace.domain import (
    DEFAULT_FOREACH_TIMEOUT,
    ForeachOutcome,
    Repo,
    Workspace,
)
from untaped.capabilities.workspace.errors import ManifestError, UnmatchedRepoFilterError
from untaped.capability_api import bounded_map, q


class Foreach:
    def __init__(
        self,
        manifests: ManifestReader,
        *,
        runner: ShellRunner,
        fs: Filesystem,
        on_interrupt: Callable[[], None] | None = None,
        warn: Callable[[str], None] = lambda _: None,
    ) -> None:
        """``on_interrupt`` runs on the calling thread when anything
        (typically ``KeyboardInterrupt``) escapes the run, before waiting
        for in-flight repos; pass the runner's hook that kills running
        commands so Ctrl-C under ``--parallel`` returns promptly. ``warn``
        reports workspaces skipped under ``skip_manifest_errors``.
        """
        self._manifests = manifests
        self._runner = runner
        self._fs = fs
        self._on_interrupt = on_interrupt
        self._warn = warn

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
        """Run ``command`` in each selected repo of one workspace (see :meth:`run_many`)."""
        return self.run_many(
            [workspace],
            command=command,
            parallel=parallel,
            continue_on_error=continue_on_error,
            only=only,
            timeout=timeout,
            on_result=on_result,
        )

    def run_many(
        self,
        workspaces: Sequence[Workspace],
        *,
        command: str,
        parallel: int = 1,
        continue_on_error: bool = False,
        only: Sequence[str] | None = None,
        timeout: float = DEFAULT_FOREACH_TIMEOUT,
        on_result: Callable[[ForeachOutcome], None] | None = None,
        strict_only: bool = True,
        skip_manifest_errors: bool = False,
    ) -> list[ForeachOutcome]:
        """Run ``command`` in each selected repo, workspace by workspace.

        Returns outcomes in workspace order, then manifest order. Every
        manifest is read and every ``only`` identifier checked before any
        command runs: with ``strict_only`` an identifier missing from any
        workspace raises; without it (``--all``) ``only`` filters per
        workspace and raises only for identifiers no workspace declares.
        ``skip_manifest_errors`` skips (and ``warn``s about) a workspace
        whose manifest cannot be read. Fail-fast stops later workspaces too.

        ``on_result`` is called on the calling thread as each repo
        finishes (completion order when ``parallel > 1``), so callers can
        stream output. Fail-fast (no ``continue_on_error``) stops queued
        repos from starting; in-flight commands finish and are reported.
        Anything escaping — including ``KeyboardInterrupt`` — cancels
        queued work instead of draining it and calls ``on_interrupt`` to
        stop in-flight commands.
        """
        plan = self._plan(
            workspaces, only, strict_only=strict_only, skip_manifest_errors=skip_manifest_errors
        )
        stop = threading.Event()
        outcomes: list[ForeachOutcome] = []
        for workspace, repos in plan:
            ran: list[ForeachOutcome] = []

            def _run(repo: Repo, ws: Workspace = workspace) -> ForeachOutcome | None:
                if stop.is_set():
                    return None
                return self._run_one(ws, repo, command, timeout)

            def _collect(
                _repo: Repo, outcome: ForeachOutcome | None, ran: list[ForeachOutcome] = ran
            ) -> None:
                if outcome is None:
                    return
                ran.append(outcome)
                if outcome.returncode != 0 and not continue_on_error:
                    stop.set()
                if on_result is not None:
                    on_result(outcome)

            bounded_map(
                _run,
                repos,
                concurrency=max(parallel, 1),
                on_each=_collect,
                on_abort=self._on_interrupt,
            )
            order = {repo.name: i for i, repo in enumerate(repos)}
            outcomes.extend(sorted(ran, key=lambda o: order.get(o.repo, len(order))))
            if stop.is_set():
                break
        return outcomes

    def _plan(
        self,
        workspaces: Sequence[Workspace],
        only: Sequence[str] | None,
        *,
        strict_only: bool,
        skip_manifest_errors: bool,
    ) -> list[tuple[Workspace, list[Repo]]]:
        plan: list[tuple[Workspace, list[Repo]]] = []
        nowhere: set[str] | None = None
        for workspace in workspaces:
            try:
                manifest = self._manifests.read(workspace.path)
            except ManifestError as exc:
                if not skip_manifest_errors:
                    raise
                self._warn(f"skipped workspace {q(workspace.name)}: {exc}")
                continue
            repos, unmatched = select_repos(manifest, only)
            if unmatched and strict_only:
                raise UnmatchedRepoFilterError(unmatched)
            nowhere = set(unmatched) if nowhere is None else nowhere & set(unmatched)
            plan.append((workspace, repos))
        if nowhere:
            raise UnmatchedRepoFilterError(tuple(sorted(nowhere)))
        return plan

    def _run_one(
        self, workspace: Workspace, repo: Repo, command: str, timeout: float
    ) -> ForeachOutcome:
        local = workspace.path / repo.name
        if not self._fs.is_dir(local):
            return ForeachOutcome(
                workspace=workspace.name,
                repo=repo.name,
                target_path=local,
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
                target_path=local,
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(exc),
                duration_s=time.perf_counter() - start,
            )
        return ForeachOutcome(
            workspace=workspace.name,
            repo=repo.name,
            target_path=local,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=time.perf_counter() - start,
        )
