"""The folder layer: the settings a project's own ``.untaped/config.yml`` gives a run.

A run inside a project (:mod:`untaped.sdk.projects`) reads the project's
file, beside ``project.json``, beneath the run's own picks. It may hold
``profile:`` (a name or a list: the home's profiles it picks) and sections
of values. Lowest first, a run layers the model's defaults, the home's base,
the folder's picks, the folder's own values, ``UNTAPED_PROFILE``'s picks,
``--profile``'s picks, ``UNTAPED_<SECTION>__<FIELD>`` variables, then
flags (:mod:`untaped.runtime.profile_resolver`).

``untaped project init`` is the consent step: a cloned repository's
committed ``config.yml`` is never read until its folder is made a project.
Even then the file is read only when it and its ``.untaped/`` folder are
yours and nobody else can write them (group write is accepted from your
private group: the one named after you, with no other members); otherwise
it is ignored with one warning.

A name the folder picks that the home doesn't define is skipped with one
warning, and the rest applies. What a folder file may not set (secret and
command settings, settings marked ``HomeOnly()``) is refused where the
layout reads it against the section models
(:mod:`untaped.runtime.settings_layout`).
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from untaped.runtime.profile_resolver import DEFAULT_PROFILE, PROFILE_NAME, pick_names
from untaped.runtime.settings import (
    PROJECT_FILE_KEYS,
    load_config_yaml,
    load_project_config,
    resolve_config_path,
)
from untaped.runtime.sink import Diagnostics
from untaped.sdk.errors import ConfigError
from untaped.sdk.network.credentials import FOLDER_SOURCE_PREFIX
from untaped.sdk.projects import DIRECTORY, find_root
from untaped.sdk.words import shown_path

#: A project's settings file, in its ``.untaped/`` folder.
FILE_NAME = "config.yml"

_diag = Diagnostics()

#: A file's state, so an edit is read again: ``(st_mtime_ns, st_size, st_ino)``.
type Stamp = tuple[int, int, int]


@dataclass(frozen=True)
class FolderLayer:
    """What a project's ``config.yml`` gives this run: its picks and its own values."""

    name: str
    """The project's name (its folder's)."""
    root: Path
    """The project's folder: a relative path setting is read from it."""
    file: Path
    """The project's ``.untaped/config.yml``."""
    picks: tuple[str, ...]
    """The home's profiles ``profile:`` picks that the home defines, in order."""
    skipped: tuple[str, ...]
    """The names ``profile:`` picks that the home doesn't define."""
    sections: Mapping[str, Any] = field(hash=False, compare=False)
    """The file's sections: every key but ``format_version`` and ``profile``."""
    stamp: tuple[Stamp, Stamp | None]
    """The project file's and the home file's state, which this was read from."""

    @property
    def source(self) -> str:
        """How provenance names a value this file set: ``project:<name>``."""
        return f"{FOLDER_SOURCE_PREFIX}{self.name}"


def project_config_path(root: Path) -> Path:
    """The settings file of the project at ``root``."""
    return root / DIRECTORY / FILE_NAME


def folder_layer() -> FolderLayer | None:
    """The folder layer of the project the working directory is in; ``None`` without one.

    ``None`` too outside a project, when the project has no ``config.yml``,
    and when the file is ignored for being writable by others. Raises
    :class:`ConfigError` (exit 4) for a file that can't be read.
    """
    try:
        root = find_root(Path.cwd())
    except OSError:  # the working directory is gone
        return None
    if root is None:
        return None
    path = project_config_path(root)
    own = _stamp(path)
    if own is None:
        return None
    return _read(path, root, own, _stamp(resolve_config_path()))


def project_picks(path: Path) -> tuple[str, ...]:
    """The names a project file's ``profile:`` picks, as written (``()`` without one).

    Reads the file as the layer does; raises :class:`ConfigError` when it
    can't be read.
    """
    return _picks(load_project_config(path).get("profile"), path)


def untrusted(path: Path) -> str | None:
    """Why a project file at ``path`` is not read, or ``None`` when it and its folder are yours.

    A file or folder that is someone else's, or that anyone but you can
    write, is not read: whoever can write it would choose your settings.
    Group write is yours when the group is your private one. Systems without
    file owners (Windows) read every project's file.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return None
    uid = getuid()
    for each in (path, path.parent):
        try:
            info = each.stat()
        except OSError as exc:
            return f"{shown_path(each)} can't be read ({exc.strerror or exc})"
        if info.st_uid != uid:
            return f"{shown_path(each)} belongs to another user"
        if info.st_mode & stat.S_IWOTH:
            return f"others can write {shown_path(each)}"
        if info.st_mode & stat.S_IWGRP and not _private_group(info.st_gid, uid):
            return f"its group can write {shown_path(each)}"
    return None


def forget_folder_layer() -> None:
    """Read every project file afresh (tests, and a process that changes folder)."""
    _read.cache_clear()
    _home_profiles.cache_clear()


def _stamp(path: Path) -> Stamp | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc.strerror or exc}") from exc
    return (info.st_mtime_ns, info.st_size, info.st_ino)


@lru_cache(maxsize=8)
def _read(path: Path, root: Path, own: Stamp, home: Stamp | None) -> FolderLayer | None:
    """The layer ``path`` gives, read once per state of it and of the home's file."""
    reason = untrusted(path)
    if reason is not None:
        _diag.warning(
            f"ignoring {shown_path(path)}: {reason}, so it could choose your settings",
            hint=f"make it yours and writable by you alone: `chmod go-w {path} {path.parent}`",
        )
        return None
    raw = load_project_config(path)
    named = _picks(raw.get("profile"), path)
    known = _home_profiles(home)
    skipped = tuple(name for name in named if name not in known)
    for name in skipped:
        _diag.warning(
            f"{shown_path(path)} picks profile {name!r}, which the home's config.yml doesn't "
            "define; the rest of the file applies",
            hint=f"create it with `untaped profile create {name}`, or drop it from `profile:`",
        )
    return FolderLayer(
        name=root.name,
        root=root,
        file=path,
        picks=tuple(name for name in named if name in known),
        skipped=skipped,
        sections={key: value for key, value in raw.items() if key not in PROJECT_FILE_KEYS},
        stamp=(own, home),
    )


@lru_cache(maxsize=4)
def _home_profiles(_home: Stamp | None) -> frozenset[str]:
    """The profiles the home defines (``_home`` is its file's state, the cache key)."""
    try:
        profiles = load_config_yaml(resolve_config_path()).get("profiles")
    except ConfigError:
        return frozenset()  # reading the home reports its own error
    return frozenset(str(name) for name in profiles) if isinstance(profiles, dict) else frozenset()


def _picks(value: object, path: Path) -> tuple[str, ...]:
    """``profile:``'s names in order; ``default`` alone picks none. Exit 4 when malformed."""
    if value is None:
        return ()
    names = pick_names(value) if isinstance(value, str | list) else None
    if names is None or not all(isinstance(name, str) for name in names):
        raise ConfigError(
            f"`profile:` in {path} must be a profile name or a list of them, got {value!r}"
        )
    for name in names:
        if not PROFILE_NAME.fullmatch(name):
            raise ConfigError(
                f"invalid profile name {name!r} in {path}'s `profile:`",
                hint="a name is letters, digits, `_`, `.` and `-`, and does not start with "
                "`.` or `-`",
            )
    if DEFAULT_PROFILE in names:
        if len(names) > 1:
            raise ConfigError(
                f"{path}'s `profile:` stacks `default` with other profiles; `default` means "
                "the base alone",
                hint="leave `default` out: the base is always read beneath the picked profiles",
            )
        return ()
    return tuple(dict.fromkeys(names))


def _private_group(gid: int, uid: int) -> bool:
    """Whether group ``gid`` is ``uid``'s private group: named after them, nobody else in it."""
    try:
        import grp  # noqa: PLC0415 - POSIX only, and only for a group-writable file
        import pwd  # noqa: PLC0415

        group = grp.getgrgid(gid)
        user = pwd.getpwuid(uid)
    except ImportError, KeyError:
        return False
    return group.gr_name == user.pw_name and not [m for m in group.gr_mem if m != user.pw_name]


__all__ = [
    "FILE_NAME",
    "FolderLayer",
    "folder_layer",
    "forget_folder_layer",
    "project_config_path",
    "project_picks",
    "untrusted",
]
