"""Stores: data a plugin keeps on disk in a layout an older release may not open.

A plugin declares one :class:`Store` per such file or directory
(``Store("ansible", "index", base=…, layout=4)``), lists it on
``PluginSpec.stores`` and opens it through :meth:`Store.open`. Every layout
lives under its own name beside the declared base: ``index.sqlite3`` is the
base, ``index.v4.sqlite3`` layout 4 and ``index.v5.sqlite3`` layout 5, so two
installs of untaped at different layouts never open each other's file. A
layout is bumped for a change an older reader cannot open, never for a
change of content (that is ``Cache(version=)``). A store holds recomputable
data only: the worst that can happen to one is a rebuild.

:meth:`Store.open` holds the layout's in-use lock, ``<layout path>.inuse``,
for the rest of the process: shared by every process reading the layout,
exclusive only for whoever moves it. A store from before 11.1 declares what
its bare file holds (``bare=4``); the first :meth:`~Store.open` at that
layout renames the bare file to the layout's name, same bytes, no rebuild.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from untaped.errors import ConfigError
from untaped.stability import experimental

if TYPE_CHECKING:
    from filelock import FileLock, ReadWriteLock

_LOG = logging.getLogger("untaped.store")

_NAME = re.compile(r"[a-z][a-z0-9_]*")
#: A SQLite file's journals: beside a bare file, a writer is (or was) mid-transaction.
SQLITE_SIDECARS = ("-journal", "-wal", "-shm")
#: Seconds :meth:`Store.open` waits while another process moves the layout.
_WAIT = 60.0


@experimental
class Store:
    """A file or directory plugin ``plugin`` keeps, kept apart per ``layout``.

    ``name`` is one settings segment (``[a-z][a-z0-9_]*``). ``base`` returns
    the path the layouts are named beside (``~`` is expanded); it is read
    each time it is needed, never at import. ``layout`` is the oldest layout
    this code's reader still opens (an integer from 1). ``bare`` is set only
    by a store that predates 11.1: the layout a file at ``base`` itself, with
    no layout in its name, was written at. Two declarations with the same
    values (the same ``base`` callable) are equal.
    """

    __slots__ = ("_caches", "bare", "base", "layout", "name", "plugin")

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        base: Callable[[], Path],
        layout: int = 1,
        bare: int | None = None,
    ) -> None:
        from untaped.plugins.registry import check_plugin_name  # noqa: PLC0415 - no cycle

        check_plugin_name(plugin)
        if not _NAME.fullmatch(name):
            raise ConfigError(
                f"store name {name!r} must be one settings segment (pattern {_NAME.pattern})"
            )
        if not callable(base):
            raise ConfigError(f"store {plugin}.{name}: base must be a function returning a path")
        if not _positive(layout):
            raise ConfigError(f"store {plugin}.{name}: layout must be an integer from 1")
        if bare is not None and not _positive(bare):
            raise ConfigError(f"store {plugin}.{name}: bare must be an integer from 1")
        self.plugin = plugin
        self.name = name
        self.base = base
        self.layout = layout
        self.bare = bare
        self._caches: set[str] = set()

    def _follow(self, cache: str) -> None:
        """Record that cache ``cache`` (of this plugin) follows the store (``Cache(store=)``)."""
        self._caches.add(cache)

    def _cache_folders(self) -> list[tuple[Path, Path]]:
        """Each following cache's bare folder and its folder at this layout."""
        from untaped.plugins.registry import named_plugin_dir  # noqa: PLC0415 - no cycle

        caches = named_plugin_dir(self.plugin) / "caches"
        return [(caches / name, caches / f"{name}.v{self.layout}") for name in sorted(self._caches)]

    def _identity(self) -> tuple[str, str, Callable[[], Path], int, int | None]:
        return (self.plugin, self.name, self.base, self.layout, self.bare)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Store):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"Store({self.plugin!r}, {self.name!r}, layout={self.layout})"

    @property
    def qualified(self) -> str:
        """``<plugin>.<name>``: how a row or a message names the store."""
        return f"{self.plugin}.{self.name}"

    @property
    def adopts(self) -> bool:
        """Whether a bare file at the base is this code's own layout, renamed on open."""
        return self.bare == self.layout

    def _base_path(self) -> Path:
        """The declared base, ``~`` expanded."""
        return Path(self.base()).expanduser()

    def path(self) -> Path:
        """This layout's path: ``<stem>.v<layout><suffix>`` beside the base. No side effect."""
        return layout_path(self._base_path(), self.layout)

    def other_paths(self) -> list[tuple[int, Path]]:
        """Every other layout on disk beside the base, with its number, oldest first.

        A bare file at the base counts when the store declares ``bare``,
        numbered by it. Reads the directory, writes nothing.
        """
        base = self._base_path()
        pattern = re.compile(rf"{re.escape(_stem(base))}\.v([1-9][0-9]*){re.escape(base.suffix)}")
        found: list[tuple[int, Path]] = []
        if self.bare is not None and _there(base):
            found.append((self.bare, base))
        try:
            entries = list(base.parent.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            matched = pattern.fullmatch(entry.name)
            if matched is not None and int(matched.group(1)) != self.layout:
                found.append((int(matched.group(1)), entry))
        return sorted(found, key=lambda item: (item[0], item[1].name))

    def open(self) -> Path:
        """This layout's path, with its in-use lock held for the rest of the process.

        Creates the base's directory. When a bare file is this layout
        (``bare == layout``) and the layout's own path does not exist yet,
        the bare file is renamed to it, with the folders of the caches that
        follow the store, under the layout's exclusive lock; when another
        process holds the lock, a writer is mid-transaction or the rename
        fails, the bare file is opened as it is and its path returned, and
        the rename waits for a later open. Calling it again returns the same
        path.
        """
        target = self.path()
        with _REGISTRY_LOCK:
            opened = _OPENED.get(target)
            if opened is not None:
                return opened
            target.parent.mkdir(parents=True, exist_ok=True)
            in_use = _in_use(target)
            opened = target
            base = self._base_path()
            if self.adopts and not _there(target) and _there(base):
                opened = target if _adopt(self, in_use, base, target) else base
            in_use.hold()
            if opened == base and not _there(base):
                # another process adopted it meanwhile; with the read side held, none can now
                opened = target
            _OPENED[target] = opened
            return opened


def layout_path(base: Path, layout: int) -> Path:
    """``<stem>.v<layout><suffix>`` beside ``base``: the suffix is ``base``'s last extension."""
    return base.with_name(f"{_stem(base)}.v{layout}{base.suffix}")


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _stem(base: Path) -> str:
    return base.stem if base.suffix else base.name


def _there(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _adopt(store: Store, in_use: _InUse, base: Path, target: Path) -> bool:
    """Rename the bare file to ``target`` under the exclusive lock; whether it is there now.

    A SQLite ``-journal``, ``-wal`` or ``-shm`` beside the bare file means a writer is
    in a transaction (or crashed in one): the file is read where it is, and
    reading it settles the journal, so a later open renames it. The folders
    of the caches that follow the store move with the file, under the same
    lock and only after it.
    """
    from untaped.migrations import move_group  # noqa: PLC0415 - migrations imports the registry

    with in_use.exclusive() as owned:
        if not owned:
            return False
        if _there(target) or not _there(base):
            return True  # another process adopted it before this one got the lock
        if any(_there(Path(f"{base}{end}")) for end in SQLITE_SIDECARS):
            return False
        try:
            move_group(base, target, rename=os.rename)
        except OSError:
            # Open elsewhere (Windows), or on another filesystem: read it where it is.
            _LOG.debug("could not rename %s to %s", base, target, exc_info=True)
            return False
        for bare, folder in store._cache_folders():
            if bare.is_dir() and not bare.is_symlink() and not _there(folder):
                try:
                    move_group(bare, folder, rename=os.rename)
                except OSError:  # the cache refills its keys: time, never data
                    _LOG.debug("could not rename %s to %s", bare, folder, exc_info=True)
        return True


# --------------------------------------------------------------------------- #
# The in-use lock
# --------------------------------------------------------------------------- #


class _InUse(Protocol):
    def hold(self) -> None:
        """Take the shared side for the rest of the process."""
        ...

    def exclusive(self) -> AbstractContextManager[bool]:
        """The exclusive side without waiting: yields whether it is held."""
        ...

    def release(self) -> None:
        """Let go of the shared side (the end of a test; a process just exits)."""
        ...


class _SqliteInUse:
    """``filelock.ReadWriteLock`` on ``<layout path>.inuse``: shared readers, one writer."""

    def __init__(self, path: Path) -> None:
        self._path = path.with_name(path.name + ".inuse")
        self._made: ReadWriteLock | None = None
        self._held = False

    def _lock(self) -> ReadWriteLock:
        """The lock, made on first use: making it opens the ``.inuse`` database."""
        if self._made is None:
            from filelock import ReadWriteLock  # noqa: PLC0415 - loads sqlite3

            assert ReadWriteLock is not None  # chosen only where sqlite3 imports
            self._made = ReadWriteLock(str(self._path), is_singleton=True)
        return self._made

    def hold(self) -> None:
        import sqlite3  # noqa: PLC0415 - ReadWriteLock loaded it

        if self._held:
            return
        try:
            self._lock().acquire_read(timeout=_WAIT)
        except (TimeoutError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            # A filesystem or a damaged .inuse that refuses the lock: the layout runs unprotected.
            _LOG.debug("could not hold the in-use lock %s: %s", self._path, exc)
            return
        self._held = True

    def release(self) -> None:
        if self._held and self._made is not None:
            self._made.release(force=True)
            self._held = False

    @contextmanager
    def exclusive(self) -> Iterator[bool]:
        import sqlite3  # noqa: PLC0415

        try:
            lock = self._lock()
            lock.acquire_write(blocking=False)
        # RuntimeError: this process reads it; a lock that cannot be taken counts as busy
        except TimeoutError, OSError, RuntimeError, sqlite3.Error:
            yield False
            return
        try:
            yield True
        finally:
            lock.release()


class _FileInUse:
    """The fallback: one ``FileLock`` per reading process under ``<layout path>.inuse.d/``.

    A reader registers under ``.gate`` and holds its own file; the exclusive
    side holds ``.gate`` and finds every other file free (a free file is a
    reader that is gone, and is removed).
    """

    def __init__(self, path: Path) -> None:
        self._dir = path.with_name(path.name + ".inuse.d")
        self._own: FileLock | None = None

    def _gate(self) -> FileLock:
        from filelock import FileLock  # noqa: PLC0415

        return FileLock(str(self._dir / ".gate"))

    def hold(self) -> None:
        from filelock import FileLock  # noqa: PLC0415

        if self._own is not None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._gate().acquire(timeout=_WAIT):
                own = FileLock(str(self._dir / f"{os.getpid()}-{uuid.uuid4().hex[:8]}"))
                own.acquire(timeout=0)
        except (TimeoutError, OSError) as exc:
            _LOG.debug("could not hold the in-use lock in %s: %s", self._dir, exc)
            return
        self._own = own

    def release(self) -> None:
        if self._own is not None:
            self._own.release(force=True)
            self._own = None

    @contextmanager
    def exclusive(self) -> Iterator[bool]:
        from filelock import FileLock  # noqa: PLC0415

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            gate = self._gate()
            gate.acquire(blocking=False)
        except TimeoutError, OSError:
            yield False
            return
        try:
            for entry in sorted(self._dir.iterdir()):
                if entry.name.startswith("."):
                    continue
                probe = FileLock(str(entry))
                try:
                    probe.acquire(blocking=False)
                except TimeoutError, OSError:
                    yield False  # a live reader
                    return
                probe.release()
                entry.unlink(missing_ok=True)  # a reader that is gone
            yield True
        finally:
            gate.release()


def _sqlite_available() -> bool:
    """Whether ``filelock`` has ``ReadWriteLock`` (it is ``None`` where Python lacks sqlite3)."""
    try:
        from filelock import ReadWriteLock  # noqa: PLC0415
    except ImportError:
        return False
    return ReadWriteLock is not None


#: Which in-use lock this process uses (tests run both).
_FACTORY: Callable[[Path], _InUse] = _SqliteInUse
_REGISTRY_LOCK = threading.RLock()
#: Every layout this process opened → the path it opened; the locks live on in ``_LOCKS``.
_OPENED: dict[Path, Path] = {}
_LOCKS: dict[Path, _InUse] = {}


def _in_use(target: Path) -> _InUse:
    """The one in-use lock of ``target`` in this process (a collected handle would release it)."""
    lock = _LOCKS.get(target)
    if lock is None:
        factory = _FACTORY if _FACTORY is not _SqliteInUse or _sqlite_available() else _FileInUse
        lock = _LOCKS[target] = factory(target)
    return lock


def _forget_opened() -> None:
    """Release every layout this process holds and forget it (``untaped.testing.plugin``)."""
    with _REGISTRY_LOCK:
        for lock in _LOCKS.values():
            lock.release()
        _LOCKS.clear()
        _OPENED.clear()


__all__ = ["Store"]
