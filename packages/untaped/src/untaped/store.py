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
from contextlib import AbstractContextManager, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from untaped.errors import ConfigError
from untaped.stability import experimental

if TYPE_CHECKING:
    from filelock import FileLock

_LOG = logging.getLogger("untaped.store")

_NAME = re.compile(r"[a-z][a-z0-9_]*")
#: Files that travel with a SQLite store's file when it is renamed.
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

    __slots__ = ("bare", "base", "layout", "name", "plugin")

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
        for label, value in (("layout", layout), ("bare", bare)):
            if label == "bare" and value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigError(f"store {plugin}.{name}: {label} must be an integer from 1")
        self.plugin = plugin
        self.name = name
        self.base = base
        self.layout = layout
        self.bare = bare

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
        pattern = re.compile(rf"{re.escape(_stem(base))}\.v([1-9][0-9]*){re.escape(_suffix(base))}")
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
        the bare file is renamed to it (with its SQLite sidecars) under the
        layout's exclusive lock; when another process holds the lock or the
        rename fails, the bare file is opened as it is and its path returned,
        and the rename waits for a later open. Calling it again returns the
        same path.
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
                opened = target if _adopt(in_use, base, target) else base
            in_use.hold()
            _OPENED[target] = opened
            return opened


def layout_path(base: Path, layout: int) -> Path:
    """``<stem>.v<layout><suffix>`` beside ``base``: the suffix is ``base``'s last extension."""
    return base.with_name(f"{_stem(base)}.v{layout}{_suffix(base)}")


def _stem(base: Path) -> str:
    return base.stem if base.suffix else base.name


def _suffix(base: Path) -> str:
    return base.suffix


def _there(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _adopt(in_use: _InUse, base: Path, target: Path) -> bool:
    """Rename the bare file to ``target`` under the exclusive lock; whether it moved."""
    from untaped.migrations import move_group  # noqa: PLC0415 - migrations imports the registry

    with in_use.exclusive() as owned:
        if not owned:
            return False
        if _there(target) or not _there(base):
            return True  # another process adopted it before this one got the lock
        try:
            move_group(base, target, SQLITE_SIDECARS, rename=os.rename)
        except OSError:
            # Open elsewhere (Windows), or on another filesystem: read it where it is,
            # with any sidecar already moved back beside it (a journal belongs to its file).
            _LOG.debug("could not rename %s to %s", base, target, exc_info=True)
            for end in SQLITE_SIDECARS:
                moved, home = Path(f"{target}{end}"), Path(f"{base}{end}")
                if _there(moved) and not _there(home):
                    with suppress(OSError):
                        os.rename(moved, home)
            return False
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
        from filelock import ReadWriteLock  # noqa: PLC0415 - loads sqlite3

        self._lock = ReadWriteLock(str(path.with_name(path.name + ".inuse")), is_singleton=True)
        self._held = False

    def hold(self) -> None:
        if self._held:
            return
        try:
            self._lock.acquire_read(timeout=_WAIT)
        except (TimeoutError, OSError) as exc:
            # A filesystem that refuses the lock: the layout runs unprotected.
            _LOG.debug("could not hold the in-use lock of %s: %s", self._lock, exc)
            return
        self._held = True

    def release(self) -> None:
        if self._held:
            self._lock.release(force=True)
            self._held = False

    @contextmanager
    def exclusive(self) -> Iterator[bool]:
        try:
            self._lock.acquire_write(blocking=False)
        except TimeoutError, OSError, RuntimeError:  # RuntimeError: this process reads it
            yield False
            return
        try:
            yield True
        finally:
            self._lock.release()


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
    try:
        from filelock import ReadWriteLock  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


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


__all__ = ["SQLITE_SIDECARS", "Store", "layout_path"]
