"""Rows the dotfiles commands emit (record kinds ``dotfiles.*``)."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from untaped.sdk import OutcomeRecord, TargetRecord, UtcTimestamp
from untaped_dotfiles.domain.models import Mode, Policy
from untaped_dotfiles.domain.status import FileState


class RepoRow(TargetRecord):
    """``dotfiles.repo``: one subscribed repo and its fetch state."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "ref", "managed", "behind", "items")

    name: str
    url: str
    ref: str
    managed: bool
    head: str | None = None
    behind: int | None = None
    """Commits the checkout is behind ``origin/<ref>`` as last fetched; ``None`` when unknown."""
    items: int = 0


class ItemRow(BaseModel):
    """``dotfiles.item``: one manifest item and the machine's choice about it."""

    model_config = ConfigDict(frozen=True, extra="forbid")
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
    policy: Policy | None = None
    """The machine's policy; ``None`` while the item is not enabled."""
    enabled: bool = False
    files: int
    skip: tuple[str, ...] = ()
    excluded: str | None = None
    """Why the item does not apply on this machine, or ``None``."""
    description: str = ""


class StatusRow(TargetRecord):
    """``dotfiles.status``: the state of one placed path."""

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
    state: FileState
    detail: str = ""


class StatusSummary(BaseModel):
    """``dotfiles.status.summary``: counts over the status rows, also written to ``status.json``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked_at: UtcTimestamp
    total: int
    attention: int
    """Rows that need the user: what makes ``status --check`` and ``sync`` exit 3."""
    pending: int = 0
    foreign: int = 0
    applied: int = 0
    behind: int = 0
    modified: int = 0
    conflict: int = 0
    missing: int = 0
    orphan: int = 0
    repos_behind: int = 0


PlaceAction = Literal[
    "planned", "created", "updated", "unchanged", "skipped", "deleted", "conflict", "failed"
]


class PlaceOutcome(OutcomeRecord, TargetRecord):
    """``dotfiles.apply_outcome`` / ``dotfiles.sync_outcome``: what was done for one placed path.

    ``state`` is the path's state before the action, so a script can tell a
    ``skipped`` row that was ``behind`` under ``manual`` from one that was
    ``applied``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("item", "source", "action", "state", "detail")

    repo: str
    item: str
    source: str
    mode: Mode
    action: PlaceAction
    state: FileState | None = None
    detail: str = ""


ItemAction = Literal["created", "updated", "deleted", "planned"]


class ItemOutcome(OutcomeRecord):
    """``dotfiles.item_outcome``: what ``enable`` or ``disable`` recorded for one item."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "name", "action", "policy", "detail")

    repo: str
    name: str
    action: ItemAction
    policy: Policy | None = None
    detail: str = ""


RepoAction = Literal["planned", "created", "updated", "deleted", "unchanged", "failed", "skipped"]


class RepoOutcome(OutcomeRecord):
    """``dotfiles.repo_outcome``: what ``subscribe``, ``unsubscribe`` or ``sync`` did for a repo."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "action", "detail")

    name: str
    action: RepoAction
    detail: str = ""
