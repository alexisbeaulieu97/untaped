"""The records the dotfiles commands emit, each declaring its ``dotfiles.*`` kind.

The app factory imports this module (through its commands), so every kind
registers before any command runs (``plugin schema`` finds each one). It holds models only, to
keep the plugin's load cheap.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from untaped.sdk import OutcomeRecord, Record, TargetRecord, UtcTimestamp
from untaped_dotfiles.domain.models import Mode, Policy
from untaped_dotfiles.domain.status import FileState


class RepoRow(TargetRecord, kind="dotfiles.repo"):
    """One subscribed repo and its fetch state."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "ref", "managed", "behind", "items")

    name: str
    url: str
    ref: str
    managed: bool
    head: str | None = None
    behind: int | None = Field(
        default=None,
        description="Commits the checkout is behind `origin/<ref>` as last fetched; "
        "null when unknown.",
    )
    items: int = 0


class ItemRow(Record, kind="dotfiles.item"):
    """One manifest item and the machine's choice about it."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "repo",
        "name",
        "suggested",
        "policy",
        "files",
        "excluded",
        "description",
    )

    repo: str
    name: str
    suggested: Policy
    policy: Policy | None = Field(
        default=None, description="The machine's policy; null while the item is not enabled."
    )
    enabled: bool = False
    files: int
    skip: tuple[str, ...] = ()
    excluded: str | None = Field(
        default=None, description="Why the item does not apply on this machine, or null."
    )
    description: str = ""


class StatusRow(TargetRecord, kind="dotfiles.status"):
    """The state of one placed path."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "item",
        "source",
        "mode",
        "policy",
        "state",
        "detail",
    )

    repo: str
    item: str
    file: str
    source: str
    mode: Mode
    policy: Policy
    state: FileState = Field(
        description="The path's state; `untaped agents dotfiles policies` explains each one."
    )
    detail: str = ""


class StatusSummary(Record, kind="dotfiles.status.summary"):
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
    """What was done for one placed path; kind-less, so the command picks the kind.

    The applier builds these for ``apply``, ``sync`` and ``remove`` alike;
    each command emits them as its own subclass (:class:`ApplyOutcome`,
    :class:`SyncOutcome`, :class:`RemoveOutcome`).
    """

    table_columns: ClassVar[tuple[str, ...]] = ("item", "source", "action", "state", "detail")

    repo: str
    item: str
    source: str
    mode: Mode
    action: PlaceAction
    state: FileState | None = Field(
        default=None,
        description="The path's state before the action, so a `skipped` row that was "
        "`behind` under `manual` differs from one that was `applied`; `untaped agents "
        "dotfiles policies` explains each state and what apply, sync and remove do in it.",
    )
    detail: str = ""


class ApplyOutcome(PlaceOutcome, kind="dotfiles.apply_outcome"):
    """What ``apply`` did, or would do, for one placed path."""


class SyncOutcome(PlaceOutcome, kind="dotfiles.sync_outcome"):
    """What ``sync`` did, or would do, for one placed path."""


class RemoveOutcome(PlaceOutcome, kind="dotfiles.remove_outcome"):
    """What ``remove`` did, or would do, for one placed path."""


ItemAction = Literal["created", "updated", "deleted", "planned"]


class ItemOutcome(OutcomeRecord, kind="dotfiles.item_outcome"):
    """What ``enable`` or ``disable`` recorded for one item."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "name", "action", "policy", "detail")

    repo: str
    name: str
    action: ItemAction
    policy: Policy | None = None
    detail: str = ""


RepoAction = Literal["planned", "updated", "deleted", "unchanged", "failed", "skipped"]


class RepoOutcome(OutcomeRecord, kind="dotfiles.repo_outcome"):
    """What ``unsubscribe`` did for a repo.

    ``apply`` and ``sync`` build the same rows for the fetches and fast-forwards
    they run, and report them on stderr rather than emitting them.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("name", "action", "detail")

    name: str
    action: RepoAction
    detail: str = ""
