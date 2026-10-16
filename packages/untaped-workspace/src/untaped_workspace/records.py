"""The rows the workspace commands emit, each declaring its ``workspace.*`` kind.

The plugin's spec imports this module, so ``untaped plugin schema`` finds
every kind before any command runs. Everything else names the model
(``accepts=(StatusRow, ...)``, ``kind_of(StatusRow)``). It holds models
only: it is on the startup path. The ``workspace.repo`` record a
``RepoSource`` issues lives in ``domain/repo.py``, which ``state.yml``'s
model also loads.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from untaped.sdk import OutcomeRecord, TargetRecord, UtcTimestamp


class WorkspaceRow(TargetRecord, kind="workspace.workspace"):
    """One active or archived workspace."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "repos", "created_at", "archived_at")

    name: str
    repos: int
    created_at: UtcTimestamp
    archived_at: UtcTimestamp | None = None


RepoAction = Literal["created", "checked_out", "unchanged", "failed"]


class RepoOutcome(OutcomeRecord, TargetRecord, kind="workspace.repo_outcome"):
    """What ``create``/``add`` did for one repo."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "branch", "base", "detail")

    workspace: str
    repo: str
    dir: str
    action: RepoAction
    branch: str | None = None
    base: str = ""
    read_only: bool = False
    detail: str = ""


StatusState = Literal["ok", "missing", "cache_missing", "error"]


class StatusRow(TargetRecord, kind="workspace.status"):
    """Live git state of one repo, and what blocks archiving it."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "repo",
        "branch",
        "state",
        "ahead",
        "behind",
        "modified",
        "untracked",
        "blockers",
    )

    workspace: str
    repo: str
    dir: str
    branch: str | None
    base: str
    read_only: bool
    state: StatusState
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    modified: int = 0
    untracked: int = 0
    stashed: int = 0
    unpushed: int = 0
    blockers: tuple[str, ...] = ()
    detail: str = ""


ArchiveAction = Literal["removed", "planned", "skipped", "failed"]


class ArchiveOutcome(OutcomeRecord, TargetRecord, kind="workspace.archive_outcome"):
    """What ``archive`` did (or would do) for one repo."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    workspace: str
    repo: str
    action: ArchiveAction
    detail: str = ""


RemoveAction = Literal["planned", "released", "removed", "kept", "skipped", "failed"]


class RemoveOutcome(OutcomeRecord, TargetRecord, kind="workspace.remove_outcome"):
    """What ``remove`` did (or would do) for one repo.

    ``released``: the repo store kept the repo for someone else (``detail``
    names who); ``removed``: it went (``freed_bytes``); ``kept``: another
    workspace still uses it. A last row with an empty ``repo`` is the
    workspace itself.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    workspace: str
    repo: str
    action: RemoveAction
    detail: str = ""
    freed_bytes: int = 0


ResolveAction = Literal["updated", "unchanged", "skipped", "failed"]


class ResolveOutcome(OutcomeRecord, TargetRecord, kind="workspace.resolve_outcome"):
    """What ``repos resolve`` did for one repo.

    ``updated``: its source lists it under a new URL for the same store repo,
    now saved; ``skipped``: nobody to ask (a typed URL, a source plugin that
    is gone or not ready).
    """

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "url", "detail")

    workspace: str
    repo: str
    dir: str
    action: ResolveAction
    url: str
    detail: str = ""


RunAction = Literal["ran", "failed", "skipped"]


class RunOutcome(OutcomeRecord, TargetRecord, kind="workspace.run_outcome"):
    """What ``run`` did for one repo."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "repo",
        "action",
        "returncode",
        "duration_s",
        "detail",
    )

    workspace: str
    repo: str
    dir: str
    action: RunAction
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    detail: str = ""
