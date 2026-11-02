"""One cache's files for one call: each key's file, its lock, and the sweeps after a fill.

A :class:`CacheFiles` holds what the cache resolved for the call (its folder,
the value's typing, the version of the code filling it and how long other
folders' files of a key stay) and never reaches back into the
:class:`~untaped.sdk.storage.cache.Cache`. Each key is one file per partition
(``<segment>.json``, or ``<segment>.v<hash>.json`` per version of a typed
value) on core's data file (:mod:`untaped.sdk.storage.data_file`): its first
line is the key's :class:`Freshness`, the second a typed cache's value.
``<stem>.lock`` beside them is held while the key is filled; readers never
take it.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from untaped.sdk._run import run_clock
from untaped.sdk.diagnostic import Failure, format_utc, parse_utc
from untaped.sdk.errors import ConfigError, ErrorCategory, System, UntapedError
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.storage._fs import file_lock, safe_path_segment
from untaped.sdk.words import duration_text

_LOG = logging.getLogger("untaped.cache")

#: Seconds a refresh waits for another process refreshing the same key.
_LOCK_TIMEOUT = 600.0
_WAITING = "waiting for another untaped process"
#: Longest key segment kept in a sidecar's name, under any filesystem's limit.
_SEGMENT = 100
#: The lock paths this context holds: a fill serving its own cache fails at once.
_HELD: ContextVar[frozenset[Path]] = ContextVar("untaped_cache_held", default=frozenset())
#: How long another partition's file of a key outlives its last refresh, at least.
PARTITION_HORIZON = timedelta(days=30)
#: A version's file of a key: ``<segment>[.p<hash>].v<hash>.json``.
VERSIONED = re.compile(r"(?P<stem>.+)\.v[0-9a-f]{8}\.json")
#: An untyped cache's one file per key and partition.
_PLAIN = re.compile(r"(?P<stem>.+)\.json")


def utcnow() -> datetime:
    """The time caches compare ages against: the run's clock (``untaped.sdk._run.run_clock``)."""
    return run_clock().now()


# --------------------------------------------------------------------------- #
# What a cache knows about a key
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Freshness:
    """What a cache knows about one key; kept in the first line of its file.

    ``refreshed_at`` is the last completed fill (``None``: never);
    ``attempted_at`` the last one that did not complete (failed or
    interrupted), and ``error`` why. After a completed fill that left items
    out, ``partial`` is set and ``error`` names them. ``retry_at`` is when
    auto may try again: upstream's own time, else ``attempted_at`` plus
    :data:`~untaped.sdk.storage.cache.RETRY_AFTER`. ``version`` is the cache's
    version at the last completed fill (``None``: unversioned, seeded, or never
    filled); ``outdated``: the cache has a version and the key was filled at
    another one, so it is due and stale whatever its age.
    """

    refreshed_at: datetime | None = None
    attempted_at: datetime | None = None
    error: Failure | None = None
    partial: bool = False
    retry_at: datetime | None = None
    version: str | None = None
    outdated: bool = False

    def past_max_age(self, max_age: timedelta, *, now: datetime) -> bool:
        """Never refreshed, or refreshed ``max_age`` or longer ago."""
        return self.refreshed_at is None or now - self.refreshed_at >= max_age

    def due(self, refresh: bool | None, *, max_age: timedelta, now: datetime) -> bool:
        """Whether ``refresh`` asks for a fill now: ``True`` always, ``False`` never.

        Auto (``None``) fills a never-refreshed key always, and one past its
        max age or outdated once ``retry_at`` passed: the retry window is a
        fallback, and with nothing on disk there is nothing to fall back to.
        """
        if refresh is False:
            return False
        if refresh or self.refreshed_at is None:
            return True
        return (self.outdated or self.past_max_age(max_age, now=now)) and (
            self.retry_at is None or now >= self.retry_at
        )


def is_stale(freshness: Freshness, max_age: timedelta, now: datetime) -> bool:
    """Past its max age, outdated, or with a remembered error (a failed refresh, a partial fill)."""
    return (
        freshness.past_max_age(max_age, now=now)
        or freshness.outdated
        or freshness.error is not None
    )


@dataclass(frozen=True)
class ValueTyping:
    """How a typed cache's values are checked and told apart: an adapter and its shape's hash.

    ``model`` is the class ``model=`` named (``None`` for the answer cache's
    per-call type), which a fill's value must be an instance of.
    """

    adapter: TypeAdapter[Any]
    shape: str
    model: type[BaseModel] | None = None


def value_typing(hint: Any, *, model: type[BaseModel] | None = None) -> ValueTyping:
    """How a cache keeps values of type ``hint``: its adapter and its shape's hash."""
    from untaped.sdk.storage.data_file import type_hash  # noqa: PLC0415 - first use

    adapter: TypeAdapter[Any] = TypeAdapter(hint)
    return ValueTyping(adapter, type_hash(adapter.json_schema()), model)


def versioned(typing: ValueTyping, version: str | None) -> str:
    """The hash naming one version's file of a value: its type's shape and ``version``."""
    return _digest(f"{typing.shape}:{version or ''}")


@dataclass(frozen=True)
class KeyPaths:
    """One key's files in one partition: its folder, the name they start with, and its lock.

    ``stem`` is ``<segment>`` or ``<segment>.p<partition hash>``; the lock is
    one per key and partition, shared by every version of the value.
    """

    dir: Path
    stem: str
    lock: Path

    def file(self, versioned: str | None = None) -> Path:
        """The key's file: ``<stem>.json``, or ``<stem>.v<hash>.json`` for a value's version."""
        name = f"{self.stem}.json" if versioned is None else f"{self.stem}.v{versioned}.json"
        return self.dir / name


@dataclass(frozen=True)
class Kept:
    """What a key's file holds: its freshness and, for a typed cache, the value and its bytes.

    ``damaged``: this version's file was there but could not be used.
    """

    freshness: Freshness
    value: Any = None
    raw: bytes | None = None
    damaged: bool = False


class LockError(ConfigError):
    """A cache key's lock: busy (``unavailable``, exit 5), or its file could not be opened."""

    system = System.LOCAL

    @property
    def busy(self) -> bool:
        """Another process holds it (``file_lock`` marks that ``unavailable``)."""
        return self.category is ErrorCategory.UNAVAILABLE


# --------------------------------------------------------------------------- #
# One cache's files for one call
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CacheFiles:
    """One cache's folder for one call, and what it resolved to read and write it.

    ``qualified`` names the cache (``<plugin>.<name>``) in an error;
    ``typing`` is how its values are kept (``None``: the cache keeps none);
    ``version`` the version of the code filling it now (``None``:
    unversioned). ``horizon`` is how long a key's files in another partition
    or settings folder stay (``None``: a version-only cache, never swept);
    ``split`` says whether other settings' folders sit beside ``folder``.
    """

    qualified: str
    folder: Path
    typing: ValueTyping | None
    version: str | None
    horizon: timedelta | None
    split: Literal["settings", "none"]

    def paths(self, key: str, partition: str | None = None) -> KeyPaths:
        """``key``'s folder, file stem and lock in ``partition``."""
        stem = segment(key) if partition is None else f"{segment(key)}.p{_digest(partition)}"
        return KeyPaths(self.folder, stem, self.folder / f"{stem}.lock")

    def own(self, paths: KeyPaths) -> Path:
        """``paths``' file for this call: the key's one file, or this version's of its value."""
        return paths.file(None if self.typing is None else versioned(self.typing, self.version))

    # ---- reading ------------------------------------------------------------

    def load(self, paths: KeyPaths, key: str, *, check: bool = True) -> Kept:
        """What ``key``'s file holds: never refreshed when missing, unreadable or another key's.

        A key filled at another version than :attr:`version` is ``outdated``.
        A typed cache reads this version's file, else the newest file of the
        same type another version filled (outdated); ``check`` validates the
        value (``False``: the first line only).
        """
        from untaped.sdk.storage.data_file import (  # noqa: PLC0415 - first use
            Lines,
            Unreadable,
            read_lines,
        )

        typing, version = self.typing, self.version
        if typing is None:
            found = read_lines(paths.file(), value=False)
            if not isinstance(found, Lines) or found.stamp.get("key") != key:
                return Kept(Freshness())
            return Kept(_freshness_of(found.stamp, version, typed=False))
        own = self.own(paths)
        found = read_lines(own, value=check)
        kept = _read_kept(found, key, version, typing, check=check)
        if kept is not None and kept.freshness.refreshed_at is not None:
            return kept
        fallback = self._fallback(paths, key, own, check=check)
        if fallback is None:
            return kept or Kept(Freshness(), damaged=isinstance(found, (Lines, Unreadable)))
        if kept is None:
            return fallback
        # this version failed before it ever filled: its attempt, over the other's value
        mine = kept.freshness
        attempt = replace(
            fallback.freshness,
            attempted_at=mine.attempted_at,
            error=mine.error,
            retry_at=mine.retry_at,
        )
        return replace(fallback, freshness=attempt)

    def _fallback(self, paths: KeyPaths, key: str, own: Path, *, check: bool) -> Kept | None:
        """The newest value of the same type another version filled for ``key``, outdated.

        Stamps are read first; only the newest value is validated, and the
        next newest when it doesn't.
        """
        from untaped.sdk.storage.data_file import read_lines  # noqa: PLC0415 - first use

        typing, version = self.typing, self.version
        assert typing is not None  # only a typed cache keeps versions of a value
        candidates: list[tuple[datetime, Path, Kept]] = []
        for entry in listed(paths.dir):
            matched = VERSIONED.fullmatch(entry.name)
            if matched is None or matched["stem"] != paths.stem or entry == own:
                continue
            kept = _read_kept(read_lines(entry, value=False), key, version, typing, check=False)
            if kept is None or kept.freshness.refreshed_at is None:
                continue
            candidates.append((kept.freshness.refreshed_at, entry, kept))
        for _, entry, kept in sorted(candidates, key=lambda each: each[0], reverse=True):
            if check:
                found = _read_kept(read_lines(entry), key, version, typing, check=True)
                if found is None or found.freshness.refreshed_at is None:
                    continue
                kept = found
            return replace(kept, freshness=replace(kept.freshness, outdated=True))
        return None

    def own_version(self, paths: KeyPaths, key: str) -> Kept | None:
        """This version's value of ``key``; ``None`` when it was never kept or no longer reads."""
        from untaped.sdk.storage.data_file import read_lines  # noqa: PLC0415 - first use

        assert self.typing is not None  # a version-only cache is declared with model=
        kept = _read_kept(read_lines(self.own(paths)), key, self.version, self.typing, check=True)
        if kept is None or kept.freshness.refreshed_at is None or kept.freshness.partial:
            return None
        return kept

    # ---- writing ------------------------------------------------------------

    def write(self, path: Path, key: str, freshness: Freshness, value: bytes | None) -> bool:
        """Write ``key``'s file; whether it was written (a failure is logged, never raised)."""
        try:
            self.write_file(path, key, freshness, value)
        except OSError:
            # The next run refreshes again; the data the fill wrote is the owner's.
            _LOG.debug("could not write the cache file %s", path, exc_info=True)
            return False
        return True

    def write_file(self, path: Path, key: str, freshness: Freshness, value: bytes | None) -> None:
        """Write ``key``'s file: its stamp, then the value's bytes when the cache keeps one."""
        from untaped.sdk.storage.data_file import write_lines  # noqa: PLC0415 - first use

        error = freshness.error
        stamp: dict[str, Any] = {"key": key}
        if self.typing is not None:
            stamp["type"] = self.typing.shape
        stamp |= {
            "version": freshness.version,
            "refreshed_at": _iso(freshness.refreshed_at),
            "attempted_at": _iso(freshness.attempted_at),
            "error": None if error is None else error.model_dump(mode="json"),
            "partial": freshness.partial,
            "retry_at": _iso(freshness.retry_at),
        }
        write_lines(path, stamp, value)

    def remember(self, own: Path, key: str, current: Kept, attempt: Freshness) -> Kept:
        """Keep a failed attempt in this version's file, with the value it falls back on, if any.

        The value's bytes are copied unparsed: from this version's file, or
        from another version's, which this version's file then serves,
        outdated, until a fill succeeds.
        """
        self.write(own, key, attempt, current.raw)
        return replace(current, freshness=attempt)

    def drop_other_versions(self, paths: KeyPaths, key: str, own: Path) -> None:
        """Remove this key and partition's files of other versions (a version-only cache's)."""
        for entry, stem, _ in key_files(paths.dir, key, VERSIONED):
            if stem == paths.stem and entry != own:
                with contextlib.suppress(OSError):
                    entry.unlink(missing_ok=True)

    def forget(self, paths: KeyPaths, key: str) -> None:
        """Remove ``key``'s file and every version's of its value, under the key's lock."""
        from untaped.sdk.storage.data_file import Lines, read_lines  # noqa: PLC0415 - first use

        with self.locked(paths, key, NO_PROGRESS):
            paths.file().unlink(missing_ok=True)
            for entry in listed(paths.dir):
                matched = VERSIONED.fullmatch(entry.name)
                if matched is None or matched["stem"] != paths.stem:
                    continue
                found = read_lines(entry, value=False)
                if isinstance(found, Lines) and found.stamp.get("key") != key:
                    continue  # another key whose name reads like a version of this one
                entry.unlink(missing_ok=True)

    # ---- after a complete fill ----------------------------------------------

    def sweep(self, paths: KeyPaths, key: str, own: Path, max_age: timedelta) -> None:
        """After a typed cache's complete fill, under the key's lock: drop what no reader serves.

        This key and partition's files of other versions go once past
        ``max_age``: whoever reads one refills it anyway. Another
        partition's files of the key (a rotated credential) go only past
        :attr:`horizon`, under their own lock taken without waiting: a
        folder shared by profiles may serve one whose max age is longer
        than this one's.
        """
        assert self.horizon is not None  # a version-only cache is never swept
        now = utcnow()
        for entry, stem, refreshed in key_files(paths.dir, key, VERSIONED):
            if entry == own:
                continue
            if stem == paths.stem:
                if refreshed is None or now - refreshed >= max_age:
                    entry.unlink(missing_ok=True)
                continue
            self._sweep_other(paths.dir, entry, stem, now - self.horizon, key, refreshed)

    def sweep_siblings(self, paths: KeyPaths, key: str) -> None:
        """After a complete fill, under the key's lock: the key in other settings' folders.

        Its files there (settings since changed, every partition and
        version) go past :attr:`horizon`, under their own lock taken
        without waiting; a folder left with nothing but free locks is
        removed. Nothing under ``split="none"``, which has one folder.
        """
        if self.split == "none":
            return
        assert self.horizon is not None  # a version-only cache is split="none"
        cutoff = utcnow() - self.horizon
        for sibling in listed(paths.dir.parent):
            if sibling == paths.dir or not sibling.is_dir():
                continue
            for entry, stem, refreshed in key_files(sibling, key, VERSIONED, _PLAIN):
                self._sweep_other(sibling, entry, stem, cutoff, key, refreshed)
            self._remove_if_unused(sibling)

    def _sweep_other(
        self,
        folder: Path,
        entry: Path,
        stem: str,
        cutoff: datetime,
        key: str,
        refreshed: datetime | None,
    ) -> None:
        """Delete ``entry`` (listed as ``refreshed``) refreshed before ``cutoff``, its lock free.

        The stamp is read again under the lock: another process may have
        refilled the file since it was listed.
        """
        if refreshed is not None and refreshed > cutoff:
            return
        other = KeyPaths(folder, stem, folder / f"{stem}.lock")
        try:
            with self.held(other, timeout=0.0, label=key):
                if _refreshed_before(entry, key, cutoff):
                    entry.unlink(missing_ok=True)
        except LockError:
            pass  # busy, or its lock won't open: the next complete fill tries again

    def _remove_if_unused(self, folder: Path) -> None:
        """Remove ``folder`` when it holds only locks, each free; else leave it as it is."""
        entries = listed(folder)
        if not entries or any(entry.suffix != ".lock" for entry in entries):
            return
        for lock in entries:
            stem = lock.name.removesuffix(".lock")
            try:
                with self.held(KeyPaths(folder, stem, lock), timeout=0.0, label=stem):
                    lock.unlink(missing_ok=True)
            except LockError:
                return  # a fill in progress: it keeps the folder
        # Only when empty: another process may have just written to it. One
        # creating its first lock here in the same instant fails that fill
        # ("could not lock"); the folder has seen no fill for the horizon.
        with contextlib.suppress(OSError):
            folder.rmdir()

    # ---- locks ----------------------------------------------------------------

    @contextmanager
    def locked(
        self, paths: KeyPaths, label: str, task: ProgressHandle, *, resume: str | None = None
    ) -> Iterator[None]:
        """Hold ``paths``' lock: at once when free, else after a visible wait (at most 600 s).

        The wait shows as ``task``'s text, then is cleared; on a parent's
        handle (``resume`` set) as its phase, then back to ``resume``.
        """
        with ExitStack() as stack:
            try:
                stack.enter_context(self.held(paths, timeout=0.0, label=label))
            except LockError as exc:
                if not exc.busy:
                    raise
                if resume is None:
                    task.update(_WAITING)
                else:
                    task.update(phase=_WAITING)
                stack.enter_context(self.held(paths, timeout=_LOCK_TIMEOUT, label=label))
                if resume is None:
                    task.update()
                else:
                    task.update(phase=resume)
            yield

    @contextmanager
    def held(self, paths: KeyPaths, *, timeout: float, label: str) -> Iterator[None]:
        """Hold ``paths``' lock, waiting up to ``timeout``; never one this context holds."""
        if paths.lock in _HELD.get():
            raise UntapedError(
                f"a fill of {self.qualified} served its own cache for {label}: "
                "a fill never serves its own cache",
                details={"cache": self.qualified},
            )
        try:
            paths.dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"could not create {paths.dir}: {exc.strerror or exc}", system=System.LOCAL
            ) from exc
        busy = f"another untaped process is still refreshing {label} after {duration_text(timeout)}"
        with file_lock(
            paths.lock,
            timeout=timeout,
            error=LockError,
            busy=busy,
            failed=f"could not lock {paths.lock}",
        ):
            token = _HELD.set(_HELD.get() | {paths.lock})
            try:
                yield
            finally:
                _HELD.reset(token)


# --------------------------------------------------------------------------- #
# Every key under a cache's root
# --------------------------------------------------------------------------- #


def outdated_keys(root: Path, typing: ValueTyping | None, version: str | None) -> int:
    """How many keys under ``root`` hold only another version's value: each refills when due.

    ``root`` is a cache's folder of every settings folder, ``typing`` and
    ``version`` what its values are kept as now. Read from each file's stamp
    (a key counts once per partition and settings folder). A key with this
    version's value isn't counted, though another install's version of it may
    sit beside it; a file that isn't a cache file is skipped.
    """
    from untaped.sdk.storage.data_file import Lines, read_lines  # noqa: PLC0415 - doctor only

    current: set[tuple[Path, str]] = set()
    other: set[tuple[Path, str]] = set()
    try:
        entries = sorted(root.rglob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        lines = read_lines(entry, value=False)
        if not isinstance(lines, Lines) or not isinstance(lines.stamp.get("key"), str):
            continue
        named = VERSIONED.fullmatch(entry.name) or _PLAIN.fullmatch(entry.name)
        assert named is not None  # every ``*.json`` name reads as a stem
        other_type = typing is not None and lines.stamp.get("type") != typing.shape
        freshness = _freshness_of(lines.stamp, version, typed=typing is not None)
        key = (entry.parent, named["stem"])
        if freshness.refreshed_at is None:
            continue  # a failed attempt only: no value of any version
        if other_type or freshness.outdated:
            other.add(key)
        else:
            current.add(key)
    return len(other - current)


# --------------------------------------------------------------------------- #
# File names and stamps
# --------------------------------------------------------------------------- #


def segment(value: str) -> str:
    """``value`` as one file name, apart from every other value's on any filesystem.

    Sanitizing is lossy (``a/b`` and ``a_b``), some filesystems fold case
    (``A`` and ``a``) or Unicode, and all limit a name's bytes: such a value
    keeps an ASCII prefix of at most :data:`_SEGMENT` bytes and a hash.
    """
    safe = safe_path_segment(value)
    if safe == value and value.isascii() and value == value.lower() and len(value) <= _SEGMENT:
        return safe
    prefix = safe.encode("ascii", "ignore").decode("ascii")[:_SEGMENT] or "_"
    return f"{prefix}~{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def key_files(
    folder: Path, key: str, *names: re.Pattern[str]
) -> Iterator[tuple[Path, str, datetime | None]]:
    """``key``'s files in ``folder``: path, stem, refreshed.

    A name is read by the first of ``names`` whose stem is the key's
    (``x.vdeadbeef.json`` is key ``x``'s version, or key ``x.vdeadbeef``'s
    one file); the stamp says which key it holds.
    """
    own = segment(key)
    for entry in listed(folder):
        stems = (each["stem"] for name in names if (each := name.fullmatch(entry.name)))
        stem = next(
            (each for each in stems if each == own or each.startswith(f"{own}.p")),
            None,
        )
        if stem is None:
            continue
        refreshed = _stamped(entry, key)
        if refreshed is not False:
            yield entry, stem, refreshed


def listed(folder: Path) -> list[Path]:
    """``folder``'s entries; none when it is missing or can't be listed."""
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _failure(raw: object) -> Failure | None:
    """A sidecar's ``error``; ``None`` when absent or unreadable (a newer untaped's)."""
    if raw is None:
        return None
    try:
        return Failure.model_validate(raw)
    except ValidationError:
        return None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else format_utc(value)


def _stamped(entry: Path, key: str) -> datetime | None | Literal[False]:
    """When ``entry``'s stamp says ``key`` was refreshed; ``False`` when it isn't ``key``'s file.

    An unreadable file, or another key's whose name begins alike, is not.
    """
    from untaped.sdk.storage.data_file import Lines, read_lines  # noqa: PLC0415 - first use

    found = read_lines(entry, value=False)
    if not isinstance(found, Lines) or found.stamp.get("key") != key:
        return False
    return parse_utc(found.stamp.get("refreshed_at"))


def _refreshed_before(entry: Path, key: str, cutoff: datetime) -> bool:
    """Whether ``entry`` is ``key``'s file, never refreshed or refreshed before ``cutoff``."""
    refreshed = _stamped(entry, key)
    return refreshed is not False and (refreshed is None or refreshed <= cutoff)


def _read_kept(
    found: object, key: str, version: str | None, typing: ValueTyping, *, check: bool
) -> Kept | None:
    """What a typed file read as ``found`` keeps for ``key``; ``None`` when it can't serve it.

    Another key's, another type's, a newer format's or an unreadable file
    can't; nor can one that says it was refreshed but has no value that
    validates (``check=False`` only asks that one is there: the file may
    have been read without it).
    """
    from untaped.sdk.storage.data_file import Lines  # noqa: PLC0415 - first use

    if not isinstance(found, Lines):
        return None
    stamp = found.stamp
    if stamp.get("key") != key or stamp.get("type") != typing.shape:
        return None
    freshness = _freshness_of(stamp, version, typed=True)
    if freshness.refreshed_at is None:
        return Kept(freshness)  # attempts only: no value yet
    if not found.holds:
        return None
    if not check:
        return Kept(freshness, raw=found.value)
    if found.value is None:
        return None  # read without its value: nothing to check
    try:
        value = typing.adapter.validate_json(found.value, strict=True)
    except ValidationError:
        return None
    return Kept(freshness, value=value, raw=found.value)


def _freshness_of(stamp: Mapping[str, Any], version: str | None, *, typed: bool) -> Freshness:
    """A file's stamp as :class:`Freshness`, ``outdated`` against ``version``.

    A typed cache's value filled at any other version is outdated; an
    untyped one's only when the cache has a version.
    """
    refreshed_at = parse_utc(stamp.get("refreshed_at"))
    filled_at = stamp.get("version")
    filled_at = filled_at if isinstance(filled_at, str) else None
    outdated = refreshed_at is not None and filled_at != version and (typed or version is not None)
    error = _failure(stamp.get("error"))
    if error is None and stamp.get("error") is not None:
        # a newer untaped's error: the attempt it describes cannot be told, the data can
        return Freshness(refreshed_at=refreshed_at, version=filled_at, outdated=outdated)
    return Freshness(
        refreshed_at=refreshed_at,
        attempted_at=parse_utc(stamp.get("attempted_at")),
        error=error,
        partial=stamp.get("partial") is True,
        retry_at=parse_utc(stamp.get("retry_at")),
        version=filled_at,
        outdated=outdated,
    )
