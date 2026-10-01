"""Rows the workspace commands emit (record kinds ``workspace.*``)."""

from __future__ import annotations

from typing import ClassVar, Literal

from untaped.capability_api import OutcomeRecord, TargetRecord, UtcTimestamp


class WorkspaceRow(TargetRecord):
    """``workspace.workspace``: one active or archived workspace."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "repos", "created_at", "archived_at")

    name: str
    repos: int
    created_at: UtcTimestamp
    archived_at: UtcTimestamp | None = None


RepoAction = Literal["created", "checked_out", "unchanged", "failed"]


class RepoOutcome(OutcomeRecord, TargetRecord):
    """``workspace.repo_outcome``: what ``create``/``add`` did for one repo."""

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


class StatusRow(TargetRecord):
    """``workspace.status``: live git state of one repo, and what blocks archiving it."""

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


class ArchiveOutcome(OutcomeRecord, TargetRecord):
    """``workspace.archive_outcome``: what ``archive`` did (or would do) for one repo."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    workspace: str
    repo: str
    action: ArchiveAction
    detail: str = ""
