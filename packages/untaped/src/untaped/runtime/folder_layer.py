"""The folder layer: the settings a project's own ``.untaped/config.yml`` gives a run.

A run inside a project (:mod:`untaped.sdk.projects`) reads the project's
file, beside ``project.json``, beneath the run's own picks. It may hold
``profile:`` (a name or a list: the home's profiles it picks) and sections
of values. Lowest first, a run layers the model's defaults, the home's base,
the folder's picks, the folder's own values, ``UNTAPED_PROFILE``'s picks,
``--profile``'s picks, ``UNTAPED_<SECTION>__<FIELD>`` variables, then
flags (:mod:`untaped.runtime.profile_resolver`).

``untaped project init`` is the consent step: a folder's ``config.yml`` is
read only when this home records the folder as a project (a cloned
repository's committed ``.untaped/`` is never read until ``project init``
runs there). Even then the file is read only when it and its ``.untaped/`` folder are
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

from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    check_names,
    named_profiles,
    pick_names,
)
from untaped.runtime.settings import (
    PROJECT_FILE_KEYS,
    load_config_yaml,
    load_project_config,
    resolve_config_path,
)
from untaped.runtime.sink import Diagnostics
from untaped.sdk.errors import ConfigError
from untaped.sdk.network.credentials import FOLDER_SOURCE_PREFIX
from untaped.sdk.projects import DIRECTORY, MARKER, find_root, recorded_here
from untaped.sdk.words import shown_path

#: A project's settings file, in its ``.untaped/`` folder.
FILE_NAME = "config.yml"

_diag = Diagnostics()

#: A file's state, so an edit (or a ``chmod``) is read again:
#: ``(st_mtime_ns, st_ctime_ns, st_size, st_ino)``.
type Stamp = tuple[int, int, int, int]


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

    def warn_skipped(self) -> None:
        """Warn, once per process, of each name ``profile:`` picks that the home doesn't define.

        Called while the run reads the folder's picks (not when ``default``
        sets them aside).
        """
        for name in self.skipped:
            _warn_once(
                self.file,
                f"{shown_path(self.file)} picks profile {name!r}, which the home's config.yml "
                "doesn't define; the rest of the file applies",
                hint=f"create it with `untaped profile create {name}`, or drop it from `profile:`",
            )

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
    marker = root / DIRECTORY / MARKER
    reason = _consent(root, _stamp(marker), _refs_stamp())
    if reason is not None:
        _warn_once(
            path,
            f"ignoring {shown_path(path)}: {reason}",
            hint=f"read it by making the folder this home's project: `untaped project init {root}`",
        )
        return None
    return _read(path, root, own, _stamp(path.parent), _stamp(resolve_config_path()))


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
    _consent.cache_clear()
    _home_profiles.cache_clear()
    _WARNED.clear()


def _stamp(path: Path) -> Stamp | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc.strerror or exc}") from exc
    return (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)


@lru_cache(maxsize=8)
def _consent(root: Path, _marker: Stamp | None, _refs: Stamp | None) -> str | None:
    """Why ``root``'s file isn't read (this home doesn't record it); the stamps key the cache."""
    return recorded_here(root)


def _refs_stamp() -> Stamp | None:
    """The home's ``refs`` folder's state: a project recorded or removed changes it."""
    from untaped.sdk.storage._refs import REFS  # noqa: PLC0415 - the refs block reads the home

    try:
        return _stamp(REFS.place)
    except ConfigError:
        return None


#: The warnings already given in this process, by file and text: each is said once.
_WARNED: set[tuple[Path, str]] = set()


def _warn_once(path: Path, message: str, *, hint: str) -> None:
    if (path, message) in _WARNED:
        return
    _WARNED.add((path, message))
    _diag.warning(message, hint=hint)


@lru_cache(maxsize=8)
def _read(
    path: Path, root: Path, own: Stamp, _folder: Stamp | None, home: Stamp | None
) -> FolderLayer | None:
    """The layer ``path`` gives, read once per state of it, its folder and the home's file."""
    reason = untrusted(path)
    if reason is not None:
        _warn_once(
            path,
            f"ignoring {shown_path(path)}: {reason}, so it could choose your settings",
            hint=f"make it yours and writable by you alone: `chmod go-w {path} {path.parent}`",
        )
        return None
    return layer_from(root, load_project_config(path), stamp=(own, home))


def layer_from(
    root: Path, raw: Mapping[str, Any], *, stamp: tuple[Stamp, Stamp | None] | None = None
) -> FolderLayer:
    """The layer the project at ``root`` gives when its file holds ``raw``.

    Its trust and the home's record aside: what a write to the file is
    checked against before it lands.
    """
    path = project_config_path(root)
    home = _stamp(resolve_config_path())
    named = _picks(raw.get("profile"), path)
    known = _home_profiles(home)
    return FolderLayer(
        name=root.name,
        root=root,
        file=path,
        picks=tuple(name for name in named if name in known),
        skipped=tuple(name for name in named if name not in known),
        sections={key: value for key, value in raw.items() if key not in PROJECT_FILE_KEYS},
        stamp=stamp if stamp is not None else ((0, 0, 0, 0), home),
    )


@lru_cache(maxsize=4)
def _home_profiles(_home: Stamp | None) -> frozenset[str]:
    """The profiles the home defines (``_home`` is its file's state, the cache key)."""
    try:
        return frozenset(named_profiles(load_config_yaml(resolve_config_path())))
    except ConfigError:
        return frozenset()  # reading the home reports its own error


def _picks(value: object, path: Path) -> tuple[str, ...]:
    """``profile:``'s names in order; ``default`` alone picks none. Exit 4 when malformed."""
    if value is None:
        return ()
    names = pick_names(value) if isinstance(value, str | list) else None
    if names is None or not all(isinstance(name, str) for name in names):
        raise ConfigError(
            f"`profile:` in {path} must be a profile name or a list of them, got {value!r}"
        )
    check_names(names, origin=f"{path}'s `profile:`", category="config")
    if names == (DEFAULT_PROFILE,):
        return ()
    return tuple(dict.fromkeys(names))


def _private_group(gid: int, uid: int) -> bool:
    """Whether group ``gid`` is ``uid``'s private group: named after them, nobody else in it.

    Nobody else: no other member listed, and no other user whose primary
    group it is (primary groups aren't listed as members).
    """
    try:
        import grp  # noqa: PLC0415 - POSIX only, and only for a group-writable file
        import pwd  # noqa: PLC0415

        group = grp.getgrgid(gid)
        user = pwd.getpwuid(uid)
        others = [entry for entry in pwd.getpwall() if entry.pw_gid == gid and entry.pw_uid != uid]
    except ImportError, KeyError:
        return False
    members = [m for m in group.gr_mem if m != user.pw_name]
    return group.gr_name == user.pw_name and not members and not others


__all__ = [
    "FILE_NAME",
    "FolderLayer",
    "folder_layer",
    "forget_folder_layer",
    "layer_from",
    "project_config_path",
    "project_picks",
    "untrusted",
]
