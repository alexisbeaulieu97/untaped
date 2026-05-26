"""Use case: reconcile a sequence of workspaces against their manifests.

Wraps the singular :class:`SyncWorkspace` (per-workspace primitive) with
dispatch + ordering + a progress-notification seam. The singular still
serves ``add --sync`` / ``import --sync`` — this use case is for callers
that hold a ``Sequence[Workspace]`` (today, ``workspace sync --all``).

Parallelism is opt-in: ``parallel<=1`` or a length-1 ``workspaces``
sequence both run on the calling thread without spinning up a pool.
Above that, the body dispatches via :class:`ThreadPoolExecutor`, drains
via ``as_completed``, then re-sorts outcomes by
``(workspace_input_order, repo)`` so table/JSON output stays
predictable across runs.

The CLI clamps ``parallel`` through :func:`untaped_core.clamp_parallel`
at the surface and trusts the value here, matching how
:class:`Foreach` is wired. The session-scoped ``BareFetchTracker`` is
allocated here unless the caller supplies one — shifting the
"composition root" from the CLI module to the plural use case for a
sweep, while the singular still allows per-call trackers for the
non-sweep callers.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from untaped_workspace.application.sync_workspace import BareFetchTracker, SyncWorkspace
from untaped_workspace.domain import SyncOutcome, Workspace


class SyncWorkspaces:
    def __init__(
        self,
        sync: SyncWorkspace,
        *,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self._sync = sync
        self._notify = notify

    def __call__(
        self,
        workspaces: Sequence[Workspace],
        *,
        only: Sequence[str] | None = None,
        prune: bool = False,
        strict_only: bool = True,
        parallel: int = 1,
        bare_tracker: BareFetchTracker | None = None,
    ) -> list[SyncOutcome]:
        tracker = bare_tracker if bare_tracker is not None else BareFetchTracker()
        if parallel <= 1 or len(workspaces) <= 1:
            return self._run_serial(workspaces, only, prune, strict_only, tracker)
        if self._notify is not None:
            self._notify(f"syncing {len(workspaces)} workspaces with up to {parallel} workers")
        return self._run_parallel(workspaces, only, prune, strict_only, tracker, parallel)

    def _run_serial(
        self,
        workspaces: Sequence[Workspace],
        only: Sequence[str] | None,
        prune: bool,
        strict_only: bool,
        tracker: BareFetchTracker,
    ) -> list[SyncOutcome]:
        outcomes: list[SyncOutcome] = []
        for ws in workspaces:
            outcomes.extend(
                self._sync(
                    ws, only=only, prune=prune, strict_only=strict_only, bare_tracker=tracker
                )
            )
        return outcomes

    def _run_parallel(
        self,
        workspaces: Sequence[Workspace],
        only: Sequence[str] | None,
        prune: bool,
        strict_only: bool,
        tracker: BareFetchTracker,
        workers: int,
    ) -> list[SyncOutcome]:
        outcomes: list[SyncOutcome] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    self._sync,
                    ws,
                    only=only,
                    prune=prune,
                    strict_only=strict_only,
                    bare_tracker=tracker,
                )
                for ws in workspaces
            ]
            for fut in as_completed(futures):
                outcomes.extend(fut.result())
        order = {ws.name: i for i, ws in enumerate(workspaces)}
        outcomes.sort(key=lambda o: (order.get(o.workspace, len(order)), o.repo))
        return outcomes
