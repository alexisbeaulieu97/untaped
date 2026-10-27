"""The records the workspace commands print; a kind is its class name (``workspace.workspace``).

It holds models only, so importing it never imports a command. The
``workspace.repo`` record a ``RepoSource`` issues lives in ``domain/repo.py``.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from untaped.sdk import OutcomeRecord, TargetRecord, UtcTimestamp


class Workspace(TargetRecord):
    """One active or archived workspace."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "repos", "created_at", "archived_at")

    name: str
    repos: int = Field(description="How many repos the workspace holds.")
    created_at: UtcTimestamp
    archived_at: UtcTimestamp | None = Field(
        default=None, description="When it was archived; null while active."
    )


RepoAction = Literal["created", "checked_out", "unchanged", "failed"]


class RepoOutcome(OutcomeRecord, TargetRecord):
    """What ``create``/``add`` did for one repo."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "branch", "base", "detail")

    workspace: str
    repo: str
    dir: str
    action: RepoAction = Field(
        description=(
            "`created`: a new branch from the base; `checked_out`: an existing branch "
            "resumed, or a read-only checkout; `unchanged`: already in the workspace."
        )
    )
    branch: str | None = None
    base: str = ""
    read_only: bool = False
    detail: str = ""


StatusState = Literal["ok", "missing", "cache_missing", "error"]


class RepoStatus(TargetRecord):
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
    state: StatusState = Field(
        description=(
            "`missing`: the repo directory is gone; `cache_missing`: the repo is missing "
            "from the repo store; `error`: its git state can't be read."
        )
    )
    upstream: str | None = Field(default=None, description="Null until the branch is on origin.")
    ahead: int = 0
    behind: int = 0
    modified: int = 0
    untracked: int = 0
    stashed: int = 0
    unpushed: int = 0
    blockers: tuple[str, ...] = Field(
        default=(), description="Why the repo blocks `archive`; empty when it doesn't."
    )
    detail: str = Field(
        default="",
        description=(
            "Why the repo's git state can't be read; with `--fetch`, why the fetch "
            "failed, or that the history backfill stopped again (no error then)."
        ),
    )


ArchiveAction = Literal["removed", "planned", "skipped", "failed"]


class ArchiveOutcome(OutcomeRecord, TargetRecord):
    """What ``archive`` did (or would do) for one repo."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    workspace: str
    repo: str = Field(description="Empty on the last row, the workspace directory itself.")
    action: ArchiveAction = Field(
        description=(
            "`removed` or `failed` when it runs; `planned`, or `skipped` for a blocked "
            "repo, on a preview or a refusal. The workspace row is `skipped` when other "
            "files stay in its directory."
        )
    )
    detail: str = ""


RemoveAction = Literal["planned", "released", "removed", "kept", "skipped", "failed"]


class RemoveOutcome(OutcomeRecord, TargetRecord):
    """What ``remove`` did (or would do) for one repo.

    ``released``: the repo store kept the repo for someone else (``detail``
    names who); ``removed``: it went (``freed_bytes``); ``kept``: another
    workspace still uses it, or a workspace worktree was added to it
    meanwhile; ``skipped``: it is not in the repo store. On a preview or a
    refusal: ``planned``, or ``skipped`` for a blocked repo. A last row with
    an empty ``repo`` is the workspace itself.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    workspace: str
    repo: str
    action: RemoveAction
    detail: str = ""
    freed_bytes: int = Field(default=0, description="The space a `removed` repo took.")


ResolveAction = Literal["updated", "unchanged", "skipped", "failed"]


class ResolveOutcome(OutcomeRecord, TargetRecord):
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


class RunOutcome(OutcomeRecord, TargetRecord):
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
    returncode: int | None = Field(
        default=None,
        description="Null for a timeout or a repo not run; negative for a signal.",
    )
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    detail: str = ""
