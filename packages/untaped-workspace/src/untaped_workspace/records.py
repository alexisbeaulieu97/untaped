"""The records the workspace commands print; a kind is its class name (``workspace.workspace``).

It holds models only, so importing it never imports a command. The
``workspace.repo`` record a ``RepoSource`` issues lives in ``domain/repo.py``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from untaped.sdk import Column, Duration, OutcomeRecord, Status, TargetRecord, UtcTimestamp


class Workspace(TargetRecord):
    """One active or archived workspace."""

    name: Annotated[str, Column]
    repos: Annotated[int, Column] = Field(description="How many repos the workspace holds.")
    created_at: Annotated[UtcTimestamp, Column]
    archived_at: Annotated[UtcTimestamp | None, Column] = Field(
        default=None, description="When it was archived; null while active."
    )


RepoAction = Literal["created", "checked_out", "unchanged", "failed"]


class RepoOutcome(OutcomeRecord, TargetRecord):
    """What ``create``/``add`` did for one repo."""

    workspace: str
    repo: Annotated[str, Column]
    dir: str
    action: Annotated[RepoAction, Column] = Field(
        description=(
            "`created`: a new branch from the base; `checked_out`: an existing branch "
            "resumed, or a read-only checkout; `unchanged`: already in the workspace."
        )
    )
    branch: Annotated[str | None, Column] = None
    base: Annotated[str, Column] = ""
    read_only: bool = False
    detail: Annotated[str, Column] = ""


StatusState = Literal["ok", "missing", "cache_missing", "error"]


class RepoStatus(TargetRecord):
    """Live git state of one repo, and what blocks archiving it."""

    workspace: str
    repo: Annotated[str, Column]
    dir: str
    branch: Annotated[str | None, Column]
    base: str
    read_only: bool
    state: Annotated[StatusState, Column, Status(success=("ok",), error=("error",))] = Field(
        description=(
            "`missing`: the repo directory is gone; `cache_missing`: the repo is missing "
            "from the repo store; `error`: its git state can't be read."
        )
    )
    upstream: str | None = Field(default=None, description="Null until the branch is on origin.")
    ahead: Annotated[int, Column] = 0
    behind: Annotated[int, Column] = 0
    modified: Annotated[int, Column] = 0
    untracked: Annotated[int, Column] = 0
    stashed: int = 0
    unpushed: int = 0
    blockers: Annotated[tuple[str, ...], Column] = Field(
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

    workspace: str
    repo: Annotated[str, Column] = Field(
        description="Empty on the last row, the workspace directory itself."
    )
    action: Annotated[ArchiveAction, Column, Status(success=("removed",))] = Field(
        description=(
            "`removed` or `failed` when it runs; `planned`, or `skipped` for a blocked "
            "repo, on a preview or a refusal. The workspace row is `skipped` when other "
            "files stay in its directory."
        )
    )
    detail: Annotated[str, Column] = ""


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

    workspace: str
    repo: Annotated[str, Column]
    action: Annotated[RemoveAction, Column, Status(success=("removed",))]
    detail: Annotated[str, Column] = ""
    freed_bytes: int = Field(default=0, description="The space a `removed` repo took.")


ResolveAction = Literal["updated", "unchanged", "skipped", "failed"]


class ResolveOutcome(OutcomeRecord, TargetRecord):
    """What ``repos resolve`` did for one repo.

    ``updated``: its source lists it under a new URL for the same store repo,
    now saved; ``skipped``: nobody to ask (a typed URL, a source plugin that
    is gone or not ready).
    """

    workspace: str
    repo: Annotated[str, Column]
    dir: str
    action: Annotated[ResolveAction, Column]
    url: Annotated[str, Column]
    detail: Annotated[str, Column] = ""


RunAction = Literal["ran", "failed", "skipped"]


class RunOutcome(OutcomeRecord, TargetRecord):
    """What ``run`` did for one repo."""

    workspace: str
    repo: Annotated[str, Column]
    dir: str
    action: Annotated[RunAction, Column]
    returncode: Annotated[int | None, Column] = Field(
        default=None,
        description="Null for a timeout or a repo not run; negative for a signal.",
    )
    stdout: str = ""
    stderr: str = ""
    duration_s: Annotated[float, Column, Duration] = 0.0
    detail: Annotated[str, Column] = ""
