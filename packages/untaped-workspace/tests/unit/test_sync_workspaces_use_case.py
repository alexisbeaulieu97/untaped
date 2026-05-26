"""Unit tests for the plural ``SyncWorkspaces`` use case.

These tests pin the dispatch / ordering / progress-notification shape
of the orchestrator that wraps the singular ``SyncWorkspace`` for
``workspace sync --all -j N``. The singular use case's behaviour
(per-workspace reconciliation, bare-cache lock contract,
``unmatched`` row synthesis) is covered by ``test_sync_use_case.py``
and is not re-exercised here — the plural is driven against a stub
``SyncWorkspace`` that records the kwargs it was called with and
returns a canned ``list[SyncOutcome]``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from untaped_workspace.application import BareFetchTracker, SyncWorkspaces
from untaped_workspace.domain import SyncOutcome, Workspace


class _StubSync:
    """Stub satisfying ``SyncWorkspace.__call__``'s shape for the plural use case.

    Records every call's positional + keyword args and returns a
    pre-seeded ``list[SyncOutcome]`` keyed by workspace name. Per-call
    ``sleep_s`` lets tests scramble parallel completion order so the
    plural's tail re-sort is observable.
    """

    def __init__(
        self,
        *,
        outcomes_by_ws: dict[str, list[SyncOutcome]],
        sleep_by_ws: dict[str, float] | None = None,
    ) -> None:
        self._outcomes = outcomes_by_ws
        self._sleep = sleep_by_ws or {}
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(
        self,
        workspace: Workspace,
        *,
        only: Sequence[str] | None = None,
        prune: bool = False,
        strict_only: bool = True,
        bare_tracker: BareFetchTracker | None = None,
    ) -> list[SyncOutcome]:
        with self._lock:
            self.calls.append(
                {
                    "workspace": workspace,
                    "only": only,
                    "prune": prune,
                    "strict_only": strict_only,
                    "bare_tracker": bare_tracker,
                }
            )
        time.sleep(self._sleep.get(workspace.name, 0.0))
        return list(self._outcomes.get(workspace.name, []))


def _ws(name: str) -> Workspace:
    return Workspace(name=name, path=Path(f"/tmp/{name}"))


def _outcome(workspace: str, repo: str, action: str = "up-to-date") -> SyncOutcome:
    return SyncOutcome(workspace=workspace, repo=repo, action=action, detail="")  # type: ignore[arg-type]


# ── serial dispatch ─────────────────────────────────────────────────────────


def test_serial_when_parallel_is_one() -> None:
    """``parallel=1`` → singular called in input order, no notify."""
    stub = _StubSync(outcomes_by_ws={"a": [_outcome("a", "r")], "b": [_outcome("b", "r")]})
    notifications: list[str] = []
    use_case = SyncWorkspaces(stub, notify=notifications.append)  # type: ignore[arg-type]

    outcomes = use_case([_ws("a"), _ws("b")], parallel=1)

    assert [c["workspace"].name for c in stub.calls] == ["a", "b"]
    assert notifications == []
    assert [o.workspace for o in outcomes] == ["a", "b"]


def test_serial_when_only_one_workspace_even_at_high_parallel() -> None:
    """``len(workspaces)==1`` → never spin up the pool, never notify."""
    stub = _StubSync(outcomes_by_ws={"solo": [_outcome("solo", "r")]})
    notifications: list[str] = []
    use_case = SyncWorkspaces(stub, notify=notifications.append)  # type: ignore[arg-type]

    use_case([_ws("solo")], parallel=8)

    assert [c["workspace"].name for c in stub.calls] == ["solo"]
    assert notifications == []


def test_default_notify_is_silent_noop() -> None:
    """Use case constructible without a notify callable; parallel path stays silent."""
    stub = _StubSync(outcomes_by_ws={"a": [_outcome("a", "r")], "b": [_outcome("b", "r")]})
    use_case = SyncWorkspaces(stub)  # type: ignore[arg-type]
    # Just must not raise — no header is emitted but the dispatch still works.
    outcomes = use_case([_ws("a"), _ws("b")], parallel=2)
    assert {o.workspace for o in outcomes} == {"a", "b"}


# ── parallel dispatch + ordering ────────────────────────────────────────────


def test_parallel_dispatch_emits_one_header() -> None:
    """``parallel>1`` and >1 workspace → notify fires once with the
    canonical ``"syncing N workspaces with up to M workers"`` text."""
    stub = _StubSync(outcomes_by_ws={"a": [_outcome("a", "r")], "b": [_outcome("b", "r")]})
    notifications: list[str] = []
    use_case = SyncWorkspaces(stub, notify=notifications.append)  # type: ignore[arg-type]

    use_case([_ws("a"), _ws("b")], parallel=4)

    assert notifications == ["syncing 2 workspaces with up to 4 workers"]


def test_parallel_outcomes_sort_by_input_order_then_repo() -> None:
    """``as_completed`` order is non-deterministic; the plural re-sorts
    by ``(workspace_input_order, repo)`` so JSON/table consumers see
    stable rows."""
    # Make 'first' slow + 'second' fast so completion order is
    # second-then-first, opposite of input order. The plural must
    # still emit first's rows before second's.
    stub = _StubSync(
        outcomes_by_ws={
            "first": [_outcome("first", "z-repo"), _outcome("first", "a-repo")],
            "second": [_outcome("second", "m-repo")],
        },
        sleep_by_ws={"first": 0.05},
    )
    use_case = SyncWorkspaces(stub)  # type: ignore[arg-type]

    outcomes = use_case([_ws("first"), _ws("second")], parallel=2)

    # Sort key is (workspace_input_order, repo); within "first", the two
    # repos sort alphabetically: a-repo < z-repo.
    assert [(o.workspace, o.repo) for o in outcomes] == [
        ("first", "a-repo"),
        ("first", "z-repo"),
        ("second", "m-repo"),
    ]


# ── BareFetchTracker threading ──────────────────────────────────────────────


def test_allocates_one_tracker_and_threads_it_into_every_call() -> None:
    """When the caller doesn't supply one, the plural allocates a
    single ``BareFetchTracker`` and passes the same instance to every
    singular ``__call__`` — that's how bare-cache dedup works across
    workspaces."""
    stub = _StubSync(outcomes_by_ws={"a": [], "b": [], "c": []})
    use_case = SyncWorkspaces(stub)  # type: ignore[arg-type]

    use_case([_ws("a"), _ws("b"), _ws("c")], parallel=1)

    trackers = [c["bare_tracker"] for c in stub.calls]
    assert all(isinstance(t, BareFetchTracker) for t in trackers)
    assert len({id(t) for t in trackers}) == 1, "expected one shared tracker, got distinct"


def test_caller_supplied_tracker_is_passed_through() -> None:
    """The caller may pass an explicit tracker (e.g. for cross-call
    dedup in a custom orchestration); the plural must thread *that*
    instance, not allocate a fresh one."""
    stub = _StubSync(outcomes_by_ws={"a": [], "b": []})
    use_case = SyncWorkspaces(stub)  # type: ignore[arg-type]
    explicit = BareFetchTracker()

    use_case([_ws("a"), _ws("b")], parallel=1, bare_tracker=explicit)

    for call in stub.calls:
        assert call["bare_tracker"] is explicit


# ── kwarg pass-through ──────────────────────────────────────────────────────


def test_passes_only_prune_strict_only_to_every_call() -> None:
    """The plural is pure dispatch — every singular call receives the
    same ``only`` / ``prune`` / ``strict_only`` the caller asked for."""
    stub = _StubSync(outcomes_by_ws={"a": [], "b": []})
    use_case = SyncWorkspaces(stub)  # type: ignore[arg-type]

    use_case(
        [_ws("a"), _ws("b")],
        only=["repo-x"],
        prune=True,
        strict_only=False,
        parallel=1,
    )

    for call in stub.calls:
        assert call["only"] == ["repo-x"]
        assert call["prune"] is True
        assert call["strict_only"] is False


# ── aggregation ─────────────────────────────────────────────────────────────


def test_outcomes_aggregated_from_every_workspace_parallel_path() -> None:
    """No outcomes are dropped on the parallel path — every singular
    row from every workspace appears in the final list."""
    stub = _StubSync(
        outcomes_by_ws={
            "a": [_outcome("a", "r1"), _outcome("a", "r2")],
            "b": [_outcome("b", "r3")],
            "c": [_outcome("c", "r4"), _outcome("c", "r5"), _outcome("c", "r6")],
        }
    )
    use_case = SyncWorkspaces(stub)  # type: ignore[arg-type]

    outcomes = use_case([_ws("a"), _ws("b"), _ws("c")], parallel=4)

    assert len(outcomes) == 6
    assert {(o.workspace, o.repo) for o in outcomes} == {
        ("a", "r1"),
        ("a", "r2"),
        ("b", "r3"),
        ("c", "r4"),
        ("c", "r5"),
        ("c", "r6"),
    }


def test_empty_workspace_list_is_a_noop() -> None:
    """Zero workspaces → zero singular calls, zero outcomes, no header."""
    stub = _StubSync(outcomes_by_ws={})
    notifications: list[str] = []
    use_case = SyncWorkspaces(stub, notify=notifications.append)  # type: ignore[arg-type]

    outcomes = use_case([], parallel=4)

    assert stub.calls == []
    assert outcomes == []
    assert notifications == []
