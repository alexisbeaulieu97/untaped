"""Dotfiles value objects: what state keeps, and the machine a package is read for."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict

from untaped.sdk import Key, UtcTimestamp
from untaped_dotfiles.package import MergeFormat, Mode
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
    placed_as: Mode
    fmt: MergeFormat | None = None
    """``merge`` only: the format untaped wrote, so taking it back writes the file the same way."""
    source_commit: str | None = None
    source_hash: str
    target_hash: str
    managed: tuple[KeyPath, ...] = ()
    """``merge`` only: the key paths untaped wrote into the target document."""
    applied_at: UtcTimestamp


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
