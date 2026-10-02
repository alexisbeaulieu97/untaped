"""Dotfiles value objects: what state keeps, and the machine a manifest is read for."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from untaped.sdk import UtcTimestamp

Policy = Literal["sync", "once", "manual"]
"""How eagerly an enabled item follows its repo."""

Mode = Literal["link", "copy", "merge"]
"""How a file is placed."""

OsName = Literal["macos", "linux", "windows"]

MergeFormat = Literal["json", "yaml"]

KeyPath = tuple[str, ...]
"""A path of mapping keys into a merged document."""


class RepoRecord(BaseModel):
    """A subscribed repo as stored in ``state.yml``.

    ``managed`` is a clone the tool made under ``dotfiles.repos_dir``; a
    registered checkout (``subscribe PATH``) is not managed: it is fetched
    but never pulled.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    path: str
    managed: bool
    manifest: str = "dotfiles.yml"
    ref: str
    subscribed_at: UtcTimestamp


class ItemChoice(BaseModel):
    """The machine's decision about one item: its policy and the files it skips."""

    model_config = ConfigDict(frozen=True)

    id: str
    """``<repo>/<item>``: the state key."""
    repo: str
    name: str
    policy: Policy
    skip: tuple[str, ...] = ()
    enabled_at: UtcTimestamp


class AppliedRecord(BaseModel):
    """One placed path: what the tool wrote there and from which source."""

    model_config = ConfigDict(frozen=True)

    target: str
    """The absolute placed path: the state key."""
    repo: str
    item: str
    file: str
    """The manifest file entry's name."""
    source: str
    """The repo-relative path placed (a directory entry's child, or the entry's source)."""
    mode: Mode
    source_commit: str | None = None
    source_hash: str
    target_hash: str
    managed: tuple[KeyPath, ...] = ()
    """``merge`` only: the key paths the tool wrote into the target document."""
    applied_at: UtcTimestamp


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
