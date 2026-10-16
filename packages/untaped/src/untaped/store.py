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

:meth:`Store.prune`, called after a complete fill, removes every other layout
no registered install holds (:mod:`untaped.refs`) and no running process has
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
from typing import TYPE_CHECKING, Literal, Protocol

from untaped.errors import ConfigError
from untaped.stability import experimental

if TYPE_CHECKING:
    from filelock import FileLock, ReadWriteLock

    from untaped.refs import Reference

_LOG = logging.getLogger("untaped.store")

_NAME = re.compile(r"[a-z][a-z0-9_]*")
#: A SQLite file's journals: beside a bare file, a writer is (or was) mid-transaction.
SQLITE_SIDECARS = ("-journal", "-wal", "-shm")
#: Seconds :meth:`Store.open` waits while another process moves the layout.
_WAIT = 60.0
#: How often :meth:`Store.open` retakes a lock whose file was moved aside while it waited.
_RETRIES = 10
_REFUSED = "has an in-use lock this filesystem refuses"
#: The first bytes of a SQLite database: an ``.inuse`` file without them is damaged.
_SQLITE_HEADER = b"SQLite format 3\x00"


@experimental
class Store:
    """A file or directory plugin ``plugin`` keeps, kept apart per ``layout``.

    ``name`` is one settings segment (``[a-z][a-z0-9_]*``). ``base`` returns
    the path the layouts are named beside (``~`` is expanded); it is read
    each time it is needed, never at import. ``layout`` is the oldest layout
    this code's reader still opens (an integer from 1). ``bare`` is set only
    by a store that predates 11.1: the layout a file at ``base`` itself, with
    no layout in its name, was written at. ``keep`` says why another
    layout's path must stay whatever holds it (a setting that points there,
    such as :func:`~untaped.migrations.setting_guard`), or ``None``; the
    cleanup, ``doctor`` and ``setup migrate-dirs`` all ask it. Two
    declarations with the same values (the same callables) are equal.
    """

    __slots__ = ("_caches", "bare", "base", "keep", "layout", "name", "plugin")

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        base: Callable[[], Path],
        layout: int = 1,
        bare: int | None = None,
        keep: Callable[[Path], str | None] | None = None,
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
        if keep is not None and not callable(keep):
            raise ConfigError(f"store {plugin}.{name}: keep must be a function of a path")
        self.plugin = plugin
        self.name = name
        self.base = base
        self.layout = layout
        self.bare = bare
        self.keep = keep
        self._caches: set[str] = set()

    def _follow(self, cache: str) -> None:
        """Record that cache ``cache`` (of this plugin) follows the store (``Cache(store=)``)."""
        self._caches.add(cache)

    def _cache_folders(self) -> list[tuple[Path, Path]]:
        """Each following cache's bare folder and its folder at this layout."""
        return list(zip(self._layout_folders(None), self._layout_folders(self.layout), strict=True))

    def _layout_folders(self, layout: int | None) -> list[Path]:
        """The sidecar folder of every following cache at ``layout`` (``None``: the bare one)."""
        from untaped.plugins.registry import named_plugin_dir  # noqa: PLC0415 - no cycle

        caches = named_plugin_dir(self.plugin) / "caches"
        end = "" if layout is None else f".v{layout}"
        return [caches / f"{name}{end}" for name in sorted(self._caches)]

    def _identity(self) -> tuple[object, ...]:
        return (self.plugin, self.name, self.base, self.layout, self.bare, self.keep)

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
        from untaped.refs import refresh  # noqa: PLC0415 - refs imports the registry

        refresh()  # a registered install's reference follows its code: after the lock, once
        return opened

    def prune(self) -> None:
        """Remove every other layout nothing holds: call it after a complete fill or write.

        Acts only in a registered install (``untaped setup register``) and
        only after this process opened this layout through :meth:`open`, so
        its own data is in place first. Under the references' lock it reads
        every reference (one written by a newer untaped, or unreadable, stops
        it) and, for each other layout on disk that no live reference names
        (a bare file too, once its layout's named file exists), takes that
        layout's exclusive lock without waiting: a busy or refused lock, or a
        rename that fails, skips the layout until the next fill. The layout,
        its SQLite sidecars, its caches' sidecar folders and its ``.inuse``
        are renamed aside under that lock and deleted outside it. Each
        removal prints one ``untaped.pruned`` line, even under ``--quiet``.
        Never raises.
        """
        from untaped import refs  # noqa: PLC0415 - refs imports the registry

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
                    if isinstance(aside := self._set_aside(layout, path), _Aside)
                ]
        except (OSError, ConfigError) as exc:
            _LOG.debug("could not prune %s: %s", self.qualified, exc)
            return
        for aside in removed:
            aside.finish()

    def _candidates(self, held: set[int]) -> list[tuple[int, Path]]:
        """The other layouts on disk no live holder names, oldest first.

        A bare file is one once its layout's named file exists (a pre-11.1
        run rebuilt it after the adoption) or when nothing holds its layout;
        while it awaits this code's own adoption, never. A path ``keep``
        names never is.
        """
        base = self._base_path()
        found: list[tuple[int, Path]] = []
        for layout, path in self.other_paths():
            if self.kept(path) is not None:
                continue
            if path == base:
                adopted = _there(layout_path(base, layout))
                if adopted or (layout != self.layout and layout not in held):
                    found.append((layout, path))
            elif layout not in held:
                found.append((layout, path))
        return found

    def _set_aside(self, layout: int, path: Path, *, unlocked: bool = False) -> _Aside | str:
        """Rename the layout at ``path`` and what travels with it aside, or say why it stays.

        ``unlocked``: a person chose to remove it after a preview, so a lock
        this filesystem refuses (or a damaged one) does not stop it; a busy
        lock still does.
        """
        bare = path == self._base_path()
        lock_path = _lock_path(path, layout, bare=bare)
        lock = _in_use(lock_path)
        with lock.exclusive() as owned:
            if not owned and not (unlocked and (lock.refused or damaged_lock(lock_path))):
                return "is open in a running untaped" if not lock.refused else _REFUSED
            companions = [
                *(Path(f"{path}{end}") for end in SQLITE_SIDECARS),
                *self._layout_folders(None if bare else layout),
                _inuse_file(lock_path),
                _inuse_dir(lock_path),
            ]
            aside = _Aside(self.qualified, layout, path, bare=bare)
            if not aside.move(path):
                return "could not be moved aside (open elsewhere, or not writable)"
            for companion in companions:
                if _there(companion):
                    aside.move(companion)
        with _REGISTRY_LOCK:
            _LOCKS.pop(lock_path, None)
        return aside


def _lock_path(path: Path, layout: int, *, bare: bool) -> Path:
    """The path whose in-use lock guards the layout at ``path``.

    An 11.1 run reads a bare file awaiting its rename under that layout's own
    lock; once renamed, only a pre-11.1 run (which takes no lock) reads the
    bare path, whose own lock nobody holds.
    """
    named = layout_path(path, layout)
    return named if bare and not _there(named) else path


LockState = Literal["free", "busy", "refused", "damaged"]
#: Why the cleanup keeps a layout nobody holds: a damaged in-use lock, the store's
#: ``keep`` (or its failure), a ``refs/`` folder or a reference it cannot read, no
#: registered install, a refused or busy lock.
KeptCode = Literal[
    "damaged", "kept", "keep-failed", "unknown", "opaque", "unregistered", "refused", "busy"
]


@dataclass(frozen=True)
class Layout:
    """One layout of a store on disk, as ``doctor`` and ``setup migrate-dirs`` describe it.

    ``own``: this code's layout, or the bare file it adopts on its next open
    (``awaiting``). ``holders``: the live references that name it. ``lock``:
    its in-use lock's state now.
    """

    layout: int
    path: Path
    own: bool
    awaiting: bool
    bare: bool
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
    base = store._base_path()
    own = store.path()
    found: list[Layout] = []
    if _there(own):
        found.append(_layout_state(store, store.layout, own, references, own=True))
    for layout, path in store.other_paths():
        awaiting = path == base and store.adopts and layout == store.layout and not _there(own)
        found.append(
            _layout_state(store, layout, path, references, own=awaiting, awaiting=awaiting)
        )
    return found


def _layout_state(
    store: Store,
    layout: int,
    path: Path,
    references: Sequence[Reference],
    *,
    own: bool,
    awaiting: bool = False,
) -> Layout:
    bare = path == store._base_path()
    holders = tuple(
        ref
        for ref in references
        if ref.opaque is None and ref.live and layout in ref.layouts(store.qualified)
    )
    if bare and _there(layout_path(path, layout)):
        holders = ()  # a pre-11.1 rebuild beside the adopted file: no reference names this file
    return Layout(
        layout=layout,
        path=path,
        own=own,
        awaiting=awaiting,
        bare=bare,
        holders=holders,
        lock=_probe(_lock_path(path, layout, bare=bare)),
    )


def kept_reason(store: Store, layout: Layout, references: Sequence[Reference]) -> Kept | None:
    """Why the cleanup has not removed ``layout`` and will not on its own, or ``None``.

    ``None`` for a held layout, this code's own, and an unheld one the next
    complete fill of a registered install removes.
    """
    from untaped.messages import shown_path  # noqa: PLC0415 - no cycle

    damaged = _damaged(layout)
    if layout.own or layout.holders:
        return damaged
    from untaped.refs import refs_dir  # noqa: PLC0415 - refs imports the registry

    # Who holds it first: while that is unknown, nothing else may call it unheld.
    folder = refs_dir()
    if any(ref.opaque is not None and ref.file == folder for ref in references):
        return Kept(
            "unknown",
            f"the install references in {shown_path(folder)} cannot be read, so who holds it "
            "is unknown and cleanup pauses; check that folder's permissions",
        )
    if any(ref.opaque is not None for ref in references):
        return Kept("opaque", "a reference this untaped cannot read pauses cleanup")
    kept, failed = store._keep_check(layout.path)
    if kept is not None:  # before the damaged lock: what keep names is never offered for removal
        return Kept("keep-failed" if failed else "kept", kept)
    if damaged is not None:
        return damaged
    if not any(
        ref.opaque is None and ref.live and ref.kind == "install" and ref.layouts(store.qualified)
        for ref in references
    ):
        return Kept("unregistered", "no registered install keeps this store, so nothing cleans up")
    if layout.lock == "refused":
        return Kept("refused", "this filesystem refuses its in-use lock, so cleanup skips it")
    if layout.lock == "busy":
        return Kept("busy", "a running untaped has it open")
    return None


def _damaged(layout: Layout) -> Kept | None:
    """``layout``'s damaged in-use lock as a reason, or ``None``."""
    from untaped.messages import shown_path  # noqa: PLC0415 - no cycle

    if layout.lock != "damaged":
        return None
    lock = _inuse_file(_lock_path(layout.path, layout.layout, bare=layout.bare))
    return Kept(
        "damaged",
        f"its in-use lock {shown_path(lock)} is damaged, so the layout is neither protected "
        "nor ever adopted or removed; delete that file while no untaped runs",
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
            held |= ref.layouts(store.qualified)
    return held


class _Aside:
    """One layout being removed: its paths renamed aside, deleted once every lock is released."""

    def __init__(self, store: str, layout: int, path: Path, *, bare: bool) -> None:
        self.store, self.layout, self.path, self.bare = store, layout, path, bare
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
        from untaped.messages import shown_path  # noqa: PLC0415 - no cycle
        from untaped.migrations import dir_bytes  # noqa: PLC0415 - migrations imports the registry

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
        from untaped.output.sink import Diagnostics  # noqa: PLC0415 - no cycle

        for text in self.delete():
            Diagnostics().warning(f"{text}; delete it by hand, nothing uses it")
        _report_pruned(self)


def _report_pruned(aside: _Aside) -> None:
    from untaped.messages import shown_path, size_text  # noqa: PLC0415 - no cycle
    from untaped.output.diagnostic import Pruned  # noqa: PLC0415 - no cycle
    from untaped.output.sink import current_sink  # noqa: PLC0415 - no cycle

    named = " (pre-11.1 name)" if aside.bare else ""
    message = (
        f"removed layout {aside.layout}{named} of store {aside.store} at "
        f"{shown_path(aside.path)} ({size_text(aside.bytes)}): nothing holds it"
    )
    line = Pruned(
        message=message,
        store=aside.store,
        layout=aside.layout,
        path=str(aside.path),
        bytes=aside.bytes,
    )
    current_sink().emit(line, unmuted=True)


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
            if not bare.is_dir() or bare.is_symlink():
                continue
            try:
                if _there(folder):
                    # written while the bare file was busy: the bare folder's sidecars are
                    # older, and would call the file a pre-11.1 run rebuilds fresh
                    _discard(bare)
                else:
                    move_group(bare, folder, rename=os.rename)
            except OSError:  # the cache refills its keys: time, never data
                _LOG.debug("could not rename %s to %s", bare, folder, exc_info=True)
        return True


def _discard(path: Path) -> None:
    """Rename a folder aside, then delete it: no reader ever sees half of it."""
    aside = path.with_name(f"{path.name}.removing-{uuid.uuid4().hex[:8]}")
    os.rename(path, aside)
    shutil.rmtree(aside, ignore_errors=True)


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
    protected while it runs nor ever adopted or removed.
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
    from untaped.refs import _forget  # noqa: PLC0415 - refs imports the registry

    _forget()
    with _REGISTRY_LOCK:
        for lock in _LOCKS.values():
            lock.release()
        _LOCKS.clear()
        _OPENED.clear()


__all__ = ["Store"]
