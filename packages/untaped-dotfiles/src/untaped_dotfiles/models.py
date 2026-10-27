"""Dotfiles value objects: what state keeps, and the machine a manifest is read for."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict

from untaped.sdk import Key, UtcTimestamp
from untaped_dotfiles.policy import Policy

Mode = Literal["link", "copy", "merge"]
"""How a file is placed."""

OsName = Literal["macos", "linux", "windows"]

MergeFormat = Literal["json", "yaml"]

KeyPath = tuple[str, ...]
"""A path of mapping keys into a merged document."""


class RepoRecord(BaseModel):
    """A subscribed repo: where its clone is and which branch it follows.

    ``managed`` is a clone the tool made under ``dotfiles.repos_dir``; a
    registered checkout (``subscribe PATH``) is not managed: it is fetched
    but never pulled.
    """

    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Key]
    url: str
    path: str
    managed: bool
    manifest: str = "dotfiles.yml"
    ref: str
    subscribed_at: UtcTimestamp


class ItemChoice(BaseModel):
    """The machine's decision about one item: its policy and the files it skips."""

    model_config = ConfigDict(frozen=True)

    id: Annotated[str, Key]
    """``<repo>/<item>``: the row's key."""
    repo: str
    name: str
    policy: Policy
    skip: tuple[str, ...] = ()
    enabled_at: UtcTimestamp


class AppliedRecord(BaseModel):
    """One placed path: what the tool wrote there and from which source."""

    model_config = ConfigDict(frozen=True)

    target: Annotated[str, Key]
    """The absolute placed path: the row's key."""
    repo: str
    item: str
    file: str
    """The manifest file entry's name."""
    source: str
    """The repo-relative path placed (a directory entry's child, or the entry's source)."""
    mode: Mode
    fmt: MergeFormat | None = None
    """``merge`` only: the format the tool wrote, so ``remove`` rewrites the file the same way."""
    source_commit: str | None = None
    source_hash: str
    target_hash: str
    managed: tuple[KeyPath, ...] = ()
    """``merge`` only: the key paths the tool wrote into the target document."""
    applied_at: UtcTimestamp


def item_id(repo: str, name: str) -> str:
    """The id of a manifest item on this machine: ``<repo>/<item>`` (the ``ItemChoice`` key)."""
    return f"{repo}/{name}"


@dataclass(frozen=True)
class Machine:
    """What a manifest is read for: this machine's OS and tags."""

    os: OsName
    tags: frozenset[str]


@dataclass(frozen=True)
class TreeFile:
    """One file in a source tree."""

    path: str
    executable: bool = False
