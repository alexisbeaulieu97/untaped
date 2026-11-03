"""Dotfiles value objects: what state keeps, and the machine a package is read for."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, JsonValue

from untaped.sdk import Key, UtcTimestamp
from untaped_dotfiles.package import MergeFormat, Mode, Step
from untaped_dotfiles.settings import Os

KeyPath = tuple[str, ...]
"""A path of mapping keys into a merged document."""


class CloneRecord(BaseModel):
    """The clone untaped made for a git source: a detached worktree of the git plugin's store."""

    model_config = ConfigDict(frozen=True)

    source: Annotated[str, Key]
    given: str
    """The source's ``url`` setting as subscribed: a URL, or a repo name; a change re-clones."""
    url: str
    """The URL cloned (a repo name resolved to its URL when subscribed)."""
    ref: str
    """The branch the clone follows."""
    folder: str
    subscribed_at: UtcTimestamp


class ItemRecord(BaseModel):
    """An item applied here at least once, so an entry its author adds later is ``new``."""

    model_config = ConfigDict(frozen=True)

    item: Annotated[str, Key]
    """``SOURCE/PKG/ITEM``."""
    first_applied_at: UtcTimestamp
    waited: tuple[tuple[str, str], ...] = ()
    """``(entry, declared target)`` of each entry that waited on an input when the item was
    applied: asked for with the item, so ``pending`` once answered, not ``new``."""


class PlacedRecord(BaseModel):
    """One placed path: what untaped wrote there and from which entry of which package."""

    model_config = ConfigDict(frozen=True)

    target: Annotated[str, Key]
    """The absolute placed path: the row's key."""
    source: str
    package: str
    item: str
    path: str
    """The source-relative path placed (a folder entry's child, or the entry's path)."""
    entry: str
    """The package file entry's ``path`` (a folder for its children)."""
    declared: str = ""
    """The entry's target as the package wrote it (``{{ input }}`` unrendered): the same
    declaration placing elsewhere means an input moved it."""
    placed_as: Mode
    fmt: MergeFormat | None = None
    """``merge`` only: the format untaped wrote, so taking it back writes the file the same way."""
    source_commit: str | None = None
    source_hash: str
    target_hash: str
    managed: tuple[KeyPath, ...] = ()
    """``merge`` only: the key paths untaped wrote into the target document."""
    applied_at: UtcTimestamp


class StepRecord(BaseModel):
    """One step as last applied here: what ``status`` checks, and what a changed step differs
    from. It holds no secret, not even hashed."""

    model_config = ConfigDict(frozen=True)

    step: Annotated[str, Key]
    """``SOURCE/PKG/ITEM/STEP``."""
    declared: Step
    """The step as the package wrote it."""
    script_hash: str | None = None
    """A ``script`` step: the script's content hash, part of what was approved."""
    digest: str
    """Over ``declared`` and ``script_hash``: a step whose digest differs is ``behind``."""
    values: dict[str, JsonValue] = {}
    """The plain top-level input values it used: a changed one counts as asking again."""
    applied_at: UtcTimestamp
    checked_at: UtcTimestamp


@dataclass(frozen=True)
class Machine:
    """What a package is read for: this machine's OS and tags."""

    os: Os
    tags: frozenset[str]


@dataclass(frozen=True)
class TreeFile:
    """One file in a source tree."""

    path: str
    executable: bool = False
