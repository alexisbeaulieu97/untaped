"""The records the dotfiles commands print; each kind is its class name (``dotfiles.path_status``).

It holds models only, so importing it never imports a command.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from untaped.sdk import Column, OutcomeRecord, Record, TargetRecord, UtcTimestamp
from untaped_dotfiles.models import Mode
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.states import FILE_STATE_ROLES, FileState


class Repo(TargetRecord):
    """One subscribed repo and its fetch state."""

    name: Annotated[str, Column]
    url: str
    ref: Annotated[str, Column]
    managed: Annotated[bool, Column]
    head: str | None = None
    behind: Annotated[int | None, Column] = Field(
        default=None,
        description="Commits the checkout is behind `origin/<ref>` as last fetched; "
        "null when unknown.",
    )
    items: Annotated[int, Column] = 0


class Item(Record):
    """One manifest item and the machine's choice about it."""

    repo: Annotated[str, Column]
    name: Annotated[str, Column]
    suggested: Annotated[Policy, Column]
    policy: Annotated[Policy | None, Column] = Field(
        default=None, description="The machine's policy; null while the item is not enabled."
    )
    enabled: bool = False
    files: Annotated[int, Column]
    skip: tuple[str, ...] = ()
    excluded: Annotated[str | None, Column] = Field(
        default=None, description="Why the item does not apply on this machine, or null."
    )
    description: Annotated[str, Column] = ""


class PathStatus(TargetRecord):
    """The state of one placed path."""

    repo: str
    item: Annotated[str, Column]
    file: str
    source: Annotated[str, Column]
    mode: Annotated[Mode, Column]
    policy: Annotated[Policy, Column]
    state: Annotated[FileState, Column, FILE_STATE_ROLES] = Field(
        description="The path's state; the policy guide explains each one."
    )
    detail: Annotated[str, Column] = ""


class StatusSummary(Record):
    """Counts over the status rows, also written to ``status.json``."""

    checked_at: UtcTimestamp
    total: int
    attention: int = Field(
        description="Rows that need the user, `error` rows included. They make "
        "`status --check` and `sync` exit 3, or 1 when one is an `error` row."
    )
    pending: int = 0
    foreign: int = 0
    applied: int = 0
    behind: int = 0
    modified: int = 0
    conflict: int = 0
    missing: int = 0
    orphan: int = 0
    error: int = 0
    repos_behind: int = 0


PlaceAction = Literal[
    "planned", "created", "updated", "unchanged", "skipped", "deleted", "conflict", "failed"
]


class PlaceOutcome(OutcomeRecord, TargetRecord):
    """What was done for one placed path; no command prints it, so each prints its own subclass.

    The applier builds these for ``apply``, ``sync`` and ``remove`` alike;
    each command emits them as its own subclass (:class:`ApplyOutcome`,
    :class:`SyncOutcome`, :class:`RemoveOutcome`).
    """

    repo: str
    item: Annotated[str, Column]
    source: Annotated[str, Column]
    mode: Mode
    action: Annotated[PlaceAction, Column]
    state: Annotated[Annotated[FileState, FILE_STATE_ROLES] | None, Column] = Field(
        default=None,
        description="The path's state before the action, so a `skipped` row that was "
        "`behind` under `manual` differs from one that was `applied`; the policy guide "
        "explains each state and what apply, sync and remove do in it.",
    )
    detail: Annotated[str, Column] = ""


class ApplyOutcome(PlaceOutcome):
    """What ``apply`` did, or would do, for one placed path."""


class SyncOutcome(PlaceOutcome):
    """What ``sync`` did, or would do, for one placed path."""


class RemoveOutcome(PlaceOutcome):
    """What ``remove`` did, or would do, for one placed path."""


ItemAction = Literal["created", "updated", "deleted", "planned"]


class ItemOutcome(OutcomeRecord):
    """What ``enable`` or ``disable`` recorded for one item."""

    repo: Annotated[str, Column]
    name: Annotated[str, Column]
    action: Annotated[ItemAction, Column]
    policy: Annotated[Policy | None, Column] = None
    detail: Annotated[str, Column] = ""


RepoAction = Literal["planned", "updated", "deleted", "unchanged", "failed", "skipped"]


class RepoOutcome(OutcomeRecord):
    """What ``unsubscribe`` did for a repo.

    ``apply`` and ``sync`` build the same rows for the fetches and fast-forwards
    they run, and report them on stderr rather than emitting them.
    """

    name: Annotated[str, Column]
    action: Annotated[RepoAction, Column]
    detail: Annotated[str, Column] = ""
