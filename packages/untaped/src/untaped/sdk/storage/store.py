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
exclusive only for whoever moves it.

:meth:`Store.prune`, called after a complete fill, removes every other layout
no registered install holds (:mod:`untaped.sdk.storage._refs`) and no running process has
open: renamed aside under the layout's exclusive lock, deleted outside it.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from untaped.runtime.settings import env_var_name
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.health.deprecated_keys import retired_values
from untaped.sdk.marks import experimental
from untaped.sdk.run import NoRunError
from untaped.sdk.storage._fs import dir_bytes

if TYPE_CHECKING:
    from filelock import FileLock, ReadWriteLock

    from untaped.sdk.storage._refs import Reference, RefTarget
    from untaped.sdk.storage.cache import Cache

_LOG = logging.getLogger("untaped.store")

_NAME = re.compile(r"[a-z][a-z0-9_]*")
#: A SQLite file's journals, which travel with its layout when it is removed.
SQLITE_SIDECARS = ("-journal", "-wal", "-shm")
#: Seconds :meth:`Store.open` waits while another process moves the layout.
_WAIT = 60.0
#: How often :meth:`Store.open` retakes a lock whose file was moved aside while it waited.
_RETRIES = 10
#: The first bytes of a SQLite database: an ``.inuse`` file without them is damaged.
_SQLITE_HEADER = b"SQLite format 3\x00"


@experimental
class Store:
    """A file or directory plugin ``plugin`` keeps, kept apart per ``layout``.

    ``name`` is one settings segment (``[a-z][a-z0-9_]*``). ``base`` returns
    the path the layouts are named beside (``~`` is expanded); it is read
    each time it is needed, never at import. ``layout`` is the oldest layout
    this code's reader still opens (an integer from 1). ``keep`` says why
    another layout's path must stay whatever holds it (a setting that points
    there, such as :func:`setting_guard`), or ``None``; the cleanup and
    ``doctor`` both ask it. Two declarations with the same values (the same
    callables) are equal.
    """

    __slots__ = ("_caches", "base", "keep", "layout", "name", "plugin")

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        base: Callable[[], Path],
        layout: int = 1,
        keep: Callable[[Path], str | None] | None = None,
    ) -> None:
        from untaped.runtime.registry import check_plugin_name  # noqa: PLC0415 - no cycle

        check_plugin_name(plugin)
        if not _NAME.fullmatch(name):
            raise ConfigError(
                f"store name {name!r} must be one settings segment (pattern {_NAME.pattern})"
            )
        if not callable(base):
            raise ConfigError(f"store {plugin}.{name}: base must be a function returning a path")
        if not _positive(layout):
            raise ConfigError(f"store {plugin}.{name}: layout must be an integer from 1")
        if keep is not None and not callable(keep):
            raise ConfigError(f"store {plugin}.{name}: keep must be a function of a path")
        self.plugin = plugin
        self.name = name
        self.base = base
        self.layout = layout
        self.keep = keep
        self._caches: set[Cache[Any]] = set()

    def _follow(self, cache: Cache[Any]) -> None:
        """Record that ``cache`` (of this plugin) follows the store (``Cache(store=)``)."""
        self._caches.add(cache)

    def _layout_folders(self, layout: int) -> list[Path]:
        """Every following cache's folder at ``layout``.

        The caches say where their folders are; the store only moves them.
        """
        caches = sorted(self._caches, key=lambda cache: cache.name)
        return [cache._root(layout) for cache in caches]

    def _identity(self) -> tuple[object, ...]:
        return (self.plugin, self.name, self.base, self.layout, self.keep)

    def kept(self, path: Path) -> str | None:
        """Why the layout at ``path`` stays whatever holds it (``keep``), or ``None``.

        A ``keep`` that raises keeps the path: a check that cannot answer
        never lets the cleanup remove anything.
        """
        return self._keep_check(path)[0]

    def _keep_check(self, path: Path) -> tuple[str | None, bool]:
        """:meth:`kept`'s answer, and whether it is the check's failure rather than its answer."""
        if self.keep is None:
            return None, False
        try:
            return self.keep(path), False
        except Exception as exc:  # a plugin's check that fails keeps, never removes
            return f"its keep check failed ({type(exc).__name__}: {exc})", True

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
    def target(self) -> RefTarget:
        """This code's layout of the store, as a reference names it."""
        from untaped.sdk.storage._refs import RefTarget  # noqa: PLC0415 - refs imports the registry

        return RefTarget(plugin=self.plugin, store=self.name, layout=self.layout)

    def _base_path(self) -> Path:
        """The declared base, ``~`` expanded."""
        return Path(self.base()).expanduser()

    def path(self) -> Path:
        """This layout's path: ``<stem>.v<layout><suffix>`` beside the base. No side effect."""
        return layout_path(self._base_path(), self.layout)

    def other_paths(self) -> list[tuple[int, Path]]:
        """Every other layout on disk beside the base, with its number, oldest first.

        Reads the directory, writes nothing.
        """
        base = self._base_path()
        pattern = re.compile(rf"{re.escape(_stem(base))}\.v([1-9][0-9]*){re.escape(base.suffix)}")
        found: list[tuple[int, Path]] = []
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

        Creates the base's directory. Calling it again returns the same path.
        """
        target = self.path()
        with _REGISTRY_LOCK:
            if target in _OPENED:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            _in_use(target).hold()
            _OPENED.add(target)
        return target

    def prune(self) -> None:
        """Remove every other layout nothing holds: call it after a complete fill or write.

        Acts only in a registered install (``untaped setup register``) and
        only after this process opened this layout through :meth:`open`, so
        its own data is in place first. Under the references' lock it reads
        every reference (one written by a newer untaped, or unreadable, stops
        it) and, for each other layout on disk that no live reference names,
        takes that layout's exclusive lock without waiting: a busy or refused
        lock, or a rename that fails, skips the layout until the next fill.
        The layout, its SQLite sidecars, its caches' folders and its
        ``.inuse`` are renamed aside under that lock and deleted outside it. Each
        removal prints one ``untaped.pruned`` line, even under ``--quiet``.
        Never raises.
        """
        from untaped.sdk.storage import _refs as refs  # noqa: PLC0415 - refs imports the registry

        with _REGISTRY_LOCK:
            if self.path() not in _OPENED:
                return
        try:
            if refs.this_install() is None:
                return  # an install nobody registered never prunes
            with refs.locked():
                found = refs.read_references()
                if any(ref.opaque is not None for ref in found):
                    return
                held = _held(self, found)
                removed = [
                    aside
                    for layout, path in self._candidates(held)
                    if (aside := self._set_aside(layout, path)) is not None
                ]
        except (OSError, UntapedError, NoRunError) as exc:  # a busy refs lock, no run
            _LOG.debug("could not prune %s: %s", self.qualified, exc)
            return
        for aside in removed:
            aside.finish()

    def _candidates(self, held: set[int]) -> list[tuple[int, Path]]:
        """The other layouts on disk no live holder names and ``keep`` does not, oldest first."""
        return [
            (layout, path)
            for layout, path in self.other_paths()
            if layout not in held and self.kept(path) is None
        ]

    def _set_aside(self, layout: int, path: Path) -> _Aside | None:
        """Rename the layout at ``path`` and what travels with it aside; ``None`` when it stays.

        It stays while its in-use lock is busy or refused, or when the rename fails.
        """
        lock = _in_use(path)
        with lock.exclusive() as owned:
            if not owned:
                return None
            companions = [
                *(Path(f"{path}{end}") for end in SQLITE_SIDECARS),
                *self._layout_folders(layout),
                _inuse_file(path),
                _inuse_dir(path),
            ]
            aside = _Aside(self, layout, path)
            if not aside.move(path):
                return None
            for companion in companions:
                if _there(companion):
                    aside.move(companion)
        with _REGISTRY_LOCK:
            _LOCKS.pop(path, None)
        return aside


LockState = Literal["free", "busy", "refused", "damaged"]
#: Why the cleanup keeps a layout nobody holds, in the order it asks: a ``refs/``
#: folder or a reference it cannot read, the store's ``keep`` (or its failure), a
#: damaged in-use lock, no registered install, a refused or busy lock.
KeptCode = Literal[
    "unknown", "opaque", "kept", "keep-failed", "damaged", "unregistered", "refused", "busy"
]


@dataclass(frozen=True)
class Layout:
    """One layout of a store on disk, as ``doctor`` describes it.

    ``own``: this code's layout. ``holders``: the live references that name
    it. ``lock``: its in-use lock's state now.
    """

    layout: int
    path: Path
    own: bool
    holders: tuple[Reference, ...]
    lock: LockState


@dataclass(frozen=True)
class Kept:
    """Why the cleanup has not removed a layout and will not on its own.

    ``code`` is for code to branch on; ``text`` is the sentence a row shows.
    ``warns``: whether it is a problem, not a choice (the store's ``keep``).
    ``unheld``: whether nothing holds it for sure; while a reference cannot be
    read, who holds it is unknown.
    """

    code: KeptCode
    text: str

    @property
    def warns(self) -> bool:
        return self.code != "kept"

    @property
    def unheld(self) -> bool:
        return self.code not in ("unknown", "opaque")


def survey(store: Store, references: Sequence[Reference]) -> list[Layout]:
    """This store's layouts on disk, its own first: who holds each, and its lock.

    Reads only: a lock is tried without waiting and only when its file
    exists (a missing one is free).
    """
    own = store.path()
    found: list[Layout] = []
    if _there(own):
        found.append(_layout_state(store, store.layout, own, references, own=True))
    for layout, path in store.other_paths():
        found.append(_layout_state(store, layout, path, references, own=False))
    return found


def _layout_state(
    store: Store, layout: int, path: Path, references: Sequence[Reference], *, own: bool
) -> Layout:
    holders = tuple(
        ref
        for ref in references
        if ref.opaque is None and ref.live and layout in ref.layouts(store)
    )
    return Layout(layout=layout, path=path, own=own, holders=holders, lock=_probe(path))


def kept_reason(store: Store, layout: Layout, references: Sequence[Reference]) -> Kept | None:
    """Why the cleanup has not removed ``layout`` and will not on its own, or ``None``.

    A held layout or this code's own returns only its damaged lock, if it
    has one; ``None`` also for an unheld one the next complete fill of a
    registered install removes.
    """
    from untaped.sdk.words import shown_path  # noqa: PLC0415 - no cycle

    damaged = _damaged(layout)
    if layout.own or layout.holders:
        return damaged
    from untaped.sdk.storage._refs import REFS  # noqa: PLC0415 - refs imports the registry

    # Who holds it first: while that is unknown, nothing else may call it unheld.
    folder = REFS.place
    if any(ref.opaque is not None and ref.file == folder for ref in references):
        return Kept(
            "unknown",
            f"the install references in {shown_path(folder)} cannot be read, so who holds it "
            "is unknown and cleanup pauses; check that folder's permissions",
        )
    if any(ref.opaque is not None for ref in references):
        return Kept("opaque", "a reference this untaped cannot read pauses cleanup")
    kept, failed = store._keep_check(layout.path)
    if kept is not None:  # before the damaged lock: what keep names is never removed
        return Kept("keep-failed" if failed else "kept", kept)
    if damaged is not None:
        return damaged
    if not any(
        ref.opaque is None and ref.live and ref.kind == "install" and ref.layouts(store)
        for ref in references
    ):
        return Kept("unregistered", "no registered install keeps this store, so nothing cleans up")
    if layout.lock == "refused":
        return Kept(
            "refused",
            "this filesystem refuses its in-use lock, so cleanup skips it; "
            "delete it and its in-use lock by hand while no untaped runs",
        )
    if layout.lock == "busy":
        return Kept("busy", "a running untaped has it open")
    return None


def _damaged(layout: Layout) -> Kept | None:
    """``layout``'s damaged in-use lock as a reason, or ``None``."""
    from untaped.sdk.words import shown_path  # noqa: PLC0415 - no cycle

    if layout.lock != "damaged":
        return None
    lock = _inuse_file(layout.path)
    return Kept(
        "damaged",
        f"its in-use lock {shown_path(lock)} is damaged, so the layout is neither protected "
        "nor ever removed; delete that file while no untaped runs",
    )


def _probe(path: Path) -> LockState:
    """The in-use lock of the layout at ``path``, tried without waiting and without creating it."""
    if damaged_lock(path) is not None:
        return "damaged"
    if not _there(_inuse_file(path)) and not _there(_inuse_dir(path)):
        return "free"
    lock = _in_use(path)
    with lock.exclusive() as owned:
        if owned:
            return "free"
    return "refused" if lock.refused else "busy"


def _held(store: Store, references: Sequence[Reference]) -> set[int]:
    """The layouts of ``store`` some live reference names."""
    held: set[int] = set()
    for ref in references:
        if ref.opaque is None and ref.live:
            held |= ref.layouts(store)
    return held


class _Aside:
    """One layout being removed: its paths renamed aside, deleted once every lock is released."""

    def __init__(self, store: Store, layout: int, path: Path) -> None:
        self.store, self.layout, self.path = store, layout, path
        self.bytes = 0
        self.moved: list[Path] = []

    def move(self, path: Path) -> bool:
        """Rename ``path`` to ``<name>.removing-<random>`` beside it; whether it moved."""
        aside = path.with_name(f"{path.name}.removing-{uuid.uuid4().hex[:8]}")
        try:
            os.rename(path, aside)
        except OSError:
            _LOG.debug("could not move %s aside", path, exc_info=True)
            return False
        self.moved.append(aside)
        return True

    def delete(self) -> list[str]:
        """Measure, then delete the renamed paths, outside every lock; why each that stayed."""
        from untaped.sdk.words import shown_path  # noqa: PLC0415 - no cycle

        failed: list[str] = []
        for path in self.moved:
            self.bytes += dir_bytes(path)
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                failed.append(f"could not delete {shown_path(path)}: {exc.strerror or exc}")
        return failed

    def finish(self) -> None:
        """Delete the renamed paths and say so; a path that will not go is a warning."""
        from untaped.runtime.sink import Diagnostics  # noqa: PLC0415 - no cycle

        for text in self.delete():
            Diagnostics().warning(f"{text}; delete it by hand, nothing uses it")
        _report_pruned(self)


def _report_pruned(aside: _Aside) -> None:
    from untaped.runtime.sink import current_sink  # noqa: PLC0415 - no cycle
    from untaped.sdk.diagnostic import Pruned  # noqa: PLC0415 - no cycle
    from untaped.sdk.words import shown_path, size_text  # noqa: PLC0415 - no cycle

    message = (
        f"removed layout {aside.layout} of store {aside.store.qualified} at "
        f"{shown_path(aside.path)} ({size_text(aside.bytes)}): nothing holds it"
    )
    line = Pruned(
        message=message,
        plugin=aside.store.plugin,
        store=aside.store.name,
        layout=aside.layout,
        path=str(aside.path),
        bytes=aside.bytes,
    )
    current_sink().emit(line, unmuted=True)


def valid_store_name(name: str) -> bool:
    """Whether ``name`` can name a store: one settings segment, ``[a-z][a-z0-9_]*``."""
    return _NAME.fullmatch(name) is not None


def layout_path(base: Path, layout: int) -> Path:
    """``<stem>.v<layout><suffix>`` beside ``base``: the suffix is ``base``'s last extension."""
    return base.with_name(f"{_stem(base)}.v{layout}{base.suffix}")


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _stem(base: Path) -> str:
    return base.stem if base.suffix else base.name


def _there(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def setting_guard(section: str, *keys: str) -> Callable[[Path], str | None]:
    """A store's ``keep`` for a path any profile still sets one of ``<section>.<keys>`` into.

    A profile that names a layout's path (or a directory inside it, or one
    holding it) keeps using it, so it stays, whatever the active profile
    says. Each key is read from every profile in config.yml, as written, and
    from its ``UNTAPED_*`` variable (a dotted key nests:
    ``inventory.path`` is ``…__INVENTORY__PATH``); a relative path is
    skipped (it would name a different directory wherever the command runs).
    """

    def guard(path: Path) -> str | None:
        for key in keys:
            if any(_overlapping(path, used) for used in _configured_paths(section, key)):
                return f"config.yml or the environment still sets {section}.{key} there"
        return None

    return guard


def _configured_paths(section: str, key: str) -> list[Path]:
    """Every absolute path ``<section>.<key>`` is set to: config.yml's profiles, then the env."""
    env = os.environ.get(env_var_name([section, *key.split(".")]))
    found: list[Path] = []
    for value in (*retired_values(section, key), env):
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value).expanduser()
        if path.is_absolute() and path not in found:
            found.append(path)
    return found


def _overlapping(left: Path, right: Path) -> bool:
    """Whether one of the two paths is the other or lies inside it (symlinks resolved)."""
    a, b = _real(left), _real(right)
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path.expanduser()))


# --------------------------------------------------------------------------- #
# The in-use lock
# --------------------------------------------------------------------------- #


class _InUse(Protocol):
    #: Whether the last :meth:`exclusive` was refused (a filesystem without locks), not busy.
    refused: bool

    def hold(self) -> None:
        """Take the shared side for the rest of the process."""
        ...

    def exclusive(self) -> AbstractContextManager[bool]:
        """The exclusive side without waiting: yields whether it is held."""
        ...

    def release(self) -> None:
        """Let go of the shared side (the end of a test; a process just exits)."""
        ...


def _inuse_file(path: Path) -> Path:
    """``<layout path>.inuse``: the ``ReadWriteLock``'s file."""
    return path.with_name(path.name + ".inuse")


def _inuse_dir(path: Path) -> Path:
    """``<layout path>.inuse.d/``: the fallback's per-process files."""
    return path.with_name(path.name + ".inuse.d")


def damaged_lock(path: Path) -> Path | None:
    """The layout at ``path``'s ``.inuse`` file when it is not a SQLite database, else ``None``.

    Such a lock is refused to every process, so the layout is neither
    protected while it runs nor ever removed.
    """
    lock = _inuse_file(path)
    try:
        with lock.open("rb") as handle:
            head = handle.read(len(_SQLITE_HEADER))
    except OSError:
        return None
    return lock if head and head != _SQLITE_HEADER else None


def _inode(path: Path) -> tuple[int, int] | None:
    try:
        found = path.stat()
    except OSError:
        return None
    return (found.st_dev, found.st_ino)


class _SqliteInUse:
    """``filelock.ReadWriteLock`` on ``<layout path>.inuse``: shared readers, one writer."""

    def __init__(self, path: Path) -> None:
        self._path = _inuse_file(path)
        self._made: ReadWriteLock | None = None
        self._held = False
        #: Set when the last :meth:`exclusive` was refused rather than busy.
        self.refused = False

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
        for _attempt in range(_RETRIES):
            try:
                lock = self._lock()
                self._path.touch()  # there before the stat (an empty file is a database to SQLite)
                before = _inode(self._path)
                lock.acquire_read(timeout=_WAIT)
            except (TimeoutError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                # A filesystem or damaged .inuse that refuses the lock: the layout runs unprotected.
                _LOG.debug("could not hold the in-use lock %s: %s", self._path, exc)
                return
            if before is not None and _inode(self._path) == before:
                self._held = True
                return
            # moved aside by a pruner while this waited: each acquire connects to the path anew
            lock.release(force=True)
        _LOG.debug("the in-use lock %s kept moving; the layout runs unprotected", self._path)

    def release(self) -> None:
        if self._held and self._made is not None:
            self._made.release(force=True)
            self._held = False

    @contextmanager
    def exclusive(self) -> Iterator[bool]:
        import sqlite3  # noqa: PLC0415

        self.refused = False
        try:
            lock = self._lock()
            lock.acquire_write(blocking=False)
        # RuntimeError: this process reads it; a lock that cannot be taken counts as busy
        except TimeoutError, RuntimeError:
            yield False
            return
        except OSError, ValueError, sqlite3.Error:
            self.refused = True
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
        self._dir = _inuse_dir(path)
        self._own: FileLock | None = None
        self.refused = False

    def _gate(self) -> FileLock:
        from filelock import FileLock  # noqa: PLC0415

        return FileLock(str(self._dir / ".gate"))

    def hold(self) -> None:
        from filelock import FileLock  # noqa: PLC0415

        if self._own is not None:
            return
        for _attempt in range(_RETRIES):
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                before = _inode(self._dir)
                with self._gate().acquire(timeout=_WAIT):
                    own = FileLock(str(self._dir / f"{os.getpid()}-{uuid.uuid4().hex[:8]}"))
                    own.acquire(timeout=0)
            except (TimeoutError, OSError) as exc:
                _LOG.debug("could not hold the in-use lock in %s: %s", self._dir, exc)
                return
            if before is not None and _inode(self._dir) == before:
                self._own = own
                return
            own.release(force=True)  # a pruner moved the folder aside while this waited
        _LOG.debug("the in-use lock %s kept moving; the layout runs unprotected", self._dir)

    def release(self) -> None:
        if self._own is not None:
            self._own.release(force=True)
            self._own = None

    @contextmanager
    def exclusive(self) -> Iterator[bool]:
        from filelock import FileLock  # noqa: PLC0415

        self.refused = False
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            gate = self._gate()
            gate.acquire(blocking=False)
        except TimeoutError:
            yield False
            return
        except OSError:
            self.refused = True
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
#: Every layout this process opened; the locks live on in ``_LOCKS``.
_OPENED: set[Path] = set()
_LOCKS: dict[Path, _InUse] = {}


def _in_use(target: Path) -> _InUse:
    """The one in-use lock of ``target`` in this process (a collected handle would release it)."""
    lock = _LOCKS.get(target)
    if lock is None:
        factory = _FACTORY if _FACTORY is not _SqliteInUse or _sqlite_available() else _FileInUse
        lock = _LOCKS[target] = factory(target)
    return lock


def _forget_opened() -> None:
    """Release every layout this process holds and forget it (``untaped.sdk.testing.plugin``)."""
    with _REGISTRY_LOCK:
        for lock in _LOCKS.values():
            lock.release()
        _LOCKS.clear()
        _OPENED.clear()


__all__ = ["Store", "setting_guard"]
