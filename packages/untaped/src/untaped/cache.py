"""Caches: data a plugin keeps from an upstream it can ask again, checked past its max age.

A plugin declares one :class:`Cache` per thing it keeps
(``Cache("ansible", "index", label="source data", max_age=timedelta(days=1))``)
and reads it through :meth:`Cache.serve`, which decides per key whether to
refresh (the *directive*), runs the plugin's ``fill`` when it must, reads the
command's answer once over every key, and says on stderr when that answer
comes from stale data (one ``untaped.stale`` warning per key), so no caller
words or forgets it.

The directive is ``refresh``: ``None`` (*auto*) refreshes a key past its max
age, failing fast when upstream is unreachable (:func:`untaped.upstream.fail_fast`);
``True`` checks upstream now with full retries; ``False`` reads what is cached
and never checks. :func:`refresh_option` is the ``--refresh``/``--cached``
option that sets it; ``gather`` opens one for every provider call it makes
(:func:`call_state`), which ``serve()`` reads when its own is ``None``.

Layout: what the cache knows about a key (:class:`Freshness`) lives in a
sidecar, ``<plugin dir>/caches/<name>/<profile>/<segment>.json``, written
atomically; ``<segment>.lock`` beside it is held while the key is filled, and
only then. Readers never take it. The lock is not re-entrant: a fill never
serves its own cache. Nested caches lock outer, then inner; the repo store's
own lock is always the innermost.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final

from cyclopts import Parameter
from pydantic import BaseModel, ConfigDict, ValidationError

from untaped.errors import ConfigError, ErrorCategory, UntapedError
from untaped.fs import atomic_write, file_lock
from untaped.git import safe_path_segment
from untaped.messages import duration_text
from untaped.output.diagnostic import NOT_COUNTED, Failure, StaleData, format_utc
from untaped.output.progress import NO_PROGRESS, ProgressHandle
from untaped.output.sink import current_sink
from untaped.records import Record, UtcTimestamp
from untaped.settings import load_settings_section, registered_profile_model
from untaped.upstream import fail_fast, full_retries

_LOG = logging.getLogger("untaped.cache")

#: How long auto waits after a failed or interrupted refresh when upstream gave no time.
RETRY_AFTER: Final = timedelta(minutes=5)
#: Seconds a refresh waits for another process refreshing the same key.
_LOCK_TIMEOUT = 600.0
_FORMAT = "1"
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_WAITING = "waiting for another untaped process"
#: Longest key segment kept in a sidecar's name, under any filesystem's limit.
_SEGMENT = 100


# --------------------------------------------------------------------------- #
# The directive gather opens
# --------------------------------------------------------------------------- #


@dataclass
class CallState:
    """One provider call's cache directive and what the call served."""

    refresh: bool | None
    stale: BaseException | None = None
    refreshed_at: datetime | None = None

    def served(self, at: datetime) -> None:
        if self.refreshed_at is None or at < self.refreshed_at:
            self.refreshed_at = at


_CALL: ContextVar[CallState | None] = ContextVar("untaped_contract_call", default=None)


@contextmanager
def call_state(refresh: bool | None) -> Iterator[CallState]:
    """Run a provider call under ``refresh``; yields what the call served."""
    state = CallState(refresh)
    token = _CALL.set(state)
    try:
        yield state
    finally:
        _CALL.reset(token)


def current_call() -> CallState | None:
    """The provider call an enclosing ``gather`` opened, else ``None``."""
    return _CALL.get()


def refresh_scope() -> bool | None:
    """The directive an enclosing ``gather`` opened, else ``None`` (refresh when stale)."""
    state = _CALL.get()
    return None if state is None else state.refresh


# --------------------------------------------------------------------------- #
# The wall clock (``untaped.testing.cache_clock`` installs its own)
# --------------------------------------------------------------------------- #


def _system_now() -> datetime:
    return datetime.now(UTC)


_now: Callable[[], datetime] = _system_now


def _utcnow() -> datetime:
    return _now()


@contextmanager
def installed_utcnow(clock: Callable[[], datetime]) -> Iterator[None]:
    """Read the time caches compare ages against from ``clock`` inside the block."""
    global _now
    previous, _now = _now, clock
    try:
        yield
    finally:
        _now = previous


# --------------------------------------------------------------------------- #
# What a cache knows, what a fill returns, what serve() answers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Freshness:
    """What a cache knows about one key; kept in its sidecar.

    ``refreshed_at`` is the last completed fill (``None``: never);
    ``attempted_at`` the last one that did not complete (failed or
    interrupted), and ``error`` why. After a completed fill that left items
    out, ``partial`` is set and ``error`` names them. ``retry_at`` is when
    auto may try again: upstream's own time, else ``attempted_at`` plus
    :data:`RETRY_AFTER`.
    """

    refreshed_at: datetime | None = None
    attempted_at: datetime | None = None
    error: Failure | None = None
    partial: bool = False
    retry_at: datetime | None = None

    def past_max_age(self, max_age: timedelta, *, now: datetime) -> bool:
        """Never refreshed, or refreshed ``max_age`` or longer ago."""
        return self.refreshed_at is None or now - self.refreshed_at >= max_age

    def due(self, refresh: bool | None, *, max_age: timedelta, now: datetime) -> bool:
        """Whether ``refresh`` asks for a fill now: ``True`` always, ``False`` never.

        Auto (``None``) fills a never-refreshed key always, and one past its
        max age once ``retry_at`` passed: the retry window is a fallback, and
        with nothing on disk there is nothing to fall back to.
        """
        if refresh is False:
            return False
        if refresh or self.refreshed_at is None:
            return True
        return self.past_max_age(max_age, now=now) and (
            self.retry_at is None or now >= self.retry_at
        )


def _is_stale(freshness: Freshness, max_age: timedelta, now: datetime) -> bool:
    """Past its max age, or with a remembered error (a failed refresh, a partial fill)."""
    return freshness.past_max_age(max_age, now=now) or freshness.error is not None


class Filled(BaseModel):
    """What a ``fill`` returns; an owner subclasses it to keep a result of its own.

    ``partial``: the fill completed but left items out (one ``Failure``
    naming them); every read until the next whole refresh warns about it.
    ``retry_at``: when upstream said to come back (a rate-limit reset).
    ``summary``: the refresh task's last line (``780 repos, 12 changed``).
    """

    model_config = ConfigDict(frozen=True)

    partial: Failure | None = None
    retry_at: UtcTimestamp | None = None
    summary: str | None = None


@dataclass(frozen=True)
class Served[T, F: Filled = Filled]:
    """What :meth:`Cache.serve` answered: the value, and how fresh each key was.

    ``value`` is ``read()``'s answer over every key; ``freshness`` each
    key's state after this run; ``filled`` what the fills of this run
    returned, by key. ``stale``: some key was served past its max age or with
    a remembered error.
    """

    value: T
    freshness: Mapping[str, Freshness]
    filled: Mapping[str, F] = field(default_factory=dict)
    stale: bool = False


class NeverRefreshed(UntapedError):
    """``--cached`` asked for a key the cache never filled: nothing to serve (exit 1)."""

    category = ErrorCategory.NOT_FOUND

    def __init__(self, cache: Cache, key: str, label: str) -> None:
        super().__init__(
            f"no cached {cache.label} for {label}; drop --cached to refresh it",
            system=cache.plugin,
            details={"cache": cache.qualified, "key": key},
        )


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


def _old_keys(cache: str, keys: Sequence[str]) -> tuple[str, ...]:
    """``renamed_from`` as a tuple once its shape is checked; composition checks the rest."""
    if isinstance(keys, str):
        raise ConfigError(f"cache {cache}: renamed_from takes a sequence of keys, not one string")
    old = tuple(keys)
    for key in old:
        if not isinstance(key, str) or not all(_NAME.fullmatch(part) for part in key.split(".")):
            raise ConfigError(
                f"cache {cache}: renamed_from key {key!r} must be dotted settings segments"
            )
        if key == "caches" or key.startswith("caches."):
            raise ConfigError(
                f"cache {cache}: renamed_from key {key!r} is under caches, which untaped writes"
            )
    if len(set(old)) != len(old):
        raise ConfigError(f"cache {cache}: renamed_from names a key twice")
    return old


@dataclass(frozen=True)
class _KeyPaths:
    """One key's files: its folder, its sidecar and its lock."""

    dir: Path
    sidecar: Path
    lock: Path


#: The lock paths this context holds: a fill serving its own cache fails at once.
_HELD: ContextVar[frozenset[Path]] = ContextVar("untaped_cache_held", default=frozenset())


class Cache:
    """Data plugin ``plugin`` keeps from an upstream, refreshed past ``max_age``.

    ``name`` is one settings segment (``[a-z][a-z0-9_]*``); ``label`` names
    the data for a human (``source data``). ``max_age`` is the declared
    default, in whole seconds (``0``: check every time); list the cache on
    ``PluginSpec.caches`` and its setting ``<plugin>.caches.<name>.max_age_seconds``
    overrides it. ``renamed_from`` names old keys of the plugin's section
    (dotted, relative to it) that are still read as that setting, with a
    warning. Two declarations with the same values are equal.
    """

    __slots__ = ("default_max_age", "label", "name", "plugin", "renamed_from")

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        label: str,
        max_age: timedelta,
        renamed_from: Sequence[str] = (),
    ) -> None:
        from untaped.plugins.registry import check_plugin_name  # noqa: PLC0415 - no cycle

        check_plugin_name(plugin)
        if not _NAME.fullmatch(name):
            raise ConfigError(
                f"cache name {name!r} must be one settings segment (pattern {_NAME.pattern})"
            )
        if max_age < timedelta(0) or max_age % timedelta(seconds=1):
            raise ConfigError(f"cache {plugin}.{name}: max_age must be whole seconds, not negative")
        self.plugin = plugin
        self.name = name
        self.label = label
        self.default_max_age = max_age
        self.renamed_from = _old_keys(f"{plugin}.{name}", renamed_from)

    def _identity(self) -> tuple[str, str, str, timedelta, tuple[str, ...]]:
        return (self.plugin, self.name, self.label, self.default_max_age, self.renamed_from)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Cache):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"Cache({self.plugin!r}, {self.name!r}, label={self.label!r})"

    @property
    def qualified(self) -> str:
        """``<plugin>.<name>``: what a stale line's ``cache`` says."""
        return f"{self.plugin}.{self.name}"

    @property
    def setting(self) -> str:
        """The setting that tunes the max age: ``<plugin>.caches.<name>.max_age_seconds``."""
        return f"{self.plugin}.caches.{self.name}.max_age_seconds"

    @property
    def default_max_age_text(self) -> str:
        """The declared max age for a human: ``24 h``."""
        return duration_text(self.default_max_age.total_seconds())

    def max_age(self) -> timedelta:
        """The max age in force now: the setting, read at call time, else the declared default.

        The default holds when the plugin's composed section has no such
        cache (the plugin is used uncomposed, or does not list the cache on
        ``PluginSpec.caches``). An invalid setting raises the settings
        :class:`ConfigError`.
        """
        model = registered_profile_model(self.plugin)
        held = None if model is None else model.model_fields.get("caches")
        kept = None if held is None else held.annotation
        if not (isinstance(kept, type) and issubclass(kept, BaseModel)) or (
            self.name not in kept.model_fields
        ):
            return self.default_max_age
        caches = load_settings_section(self.plugin).caches
        return timedelta(seconds=getattr(caches, self.name).max_age_seconds)

    # ---- reading and filling ------------------------------------------------

    def serve[T, F: Filled](
        self,
        keys: Mapping[str, str],
        *,
        refresh: bool | None,
        fill: Callable[[str, ProgressHandle], F],
        read: Callable[[], T],
        empty: Callable[[T], bool] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> Served[T, F]:
        """Refresh what ``refresh`` asks for, then answer ``read()`` over every key.

        ``keys`` maps each key to its label for a human (``source prod``).
        ``fill(key, handle)`` refreshes one key from upstream and reports
        through ``handle``; it raises an :class:`~untaped.errors.UntapedError`
        when the refresh fails, with upstream's own retry time, when it knows
        one, in ``details["retry_at"]`` (ISO 8601). ``empty(value)`` says
        whether the answer found nothing: a stale empty answer fails with the
        refresh's error instead of passing for an absence. Under a ``progress``
        handle no task is opened: the refresh is a phase of the caller's.

        A failed refresh with data cached serves that data with a warning; with
        none, the error is raised. ``--cached`` on a key never filled raises
        :class:`NeverRefreshed`. A Ctrl-C during a fill is remembered like a
        failed attempt and said as one ``error:`` line before it propagates.
        """
        max_age, started = self.max_age(), _utcnow()
        if refresh is None:
            refresh = refresh_scope()
        folder = self._folder()
        paths = {key: self._key_paths(folder, key) for key in keys}
        freshness = {key: self._load(paths[key], key) for key in keys}
        filled: dict[str, F] = {}
        for key, label in keys.items():
            if freshness[key].due(refresh, max_age=max_age, now=started):
                freshness[key] = self._refresh(
                    key,
                    label,
                    paths[key],
                    freshness[key],
                    filled,
                    fill=fill,
                    refresh=refresh,
                    started=started,
                    max_age=max_age,
                    progress=progress,
                )
        return self._serve(
            keys,
            read,
            freshness,
            filled,
            refresh=refresh,
            started=started,
            empty=empty,
            max_age=max_age,
        )

    def _refresh[F: Filled](
        self,
        key: str,
        label: str,
        paths: _KeyPaths,
        freshness: Freshness,
        filled: dict[str, F],
        *,
        fill: Callable[[str, ProgressHandle], F],
        refresh: bool | None,
        started: datetime,
        max_age: timedelta,
        progress: ProgressHandle,
    ) -> Freshness:
        """Fill ``key`` under its task and lock; what the cache knows of it afterwards.

        Raises when the fill fails with nothing cached to fall back to, when
        the lock never comes, and on Ctrl-C.
        """
        failure: BaseException | None = None
        try:
            text = self._task_text(label, freshness, max_age)
            with (
                self._task(text, progress) as task,
                # back to the parent's phase after a wait; an own task has none
                self._lock(paths, label, task, resume=None if progress is NO_PROGRESS else text),
            ):
                current = freshness = self._load(paths, key)
                if current.refreshed_at is not None and current.refreshed_at >= started:
                    return current  # refreshed while this run waited: serve it, under --refresh too
                if refresh is None and not current.due(None, max_age=max_age, now=_utcnow()):
                    return current  # auto: no longer due (another run failed meanwhile)
                try:
                    # the budget is a fallback too: a first fill keeps its full retries
                    budget = refresh is None and current.refreshed_at is not None
                    with fail_fast() if budget else full_retries():
                        result = fill(key, task)
                except UntapedError as exc:
                    failure = exc
                    freshness = self._save(paths, key, _failed(current, exc, started))
                    raise
                except KeyboardInterrupt as exc:
                    failure = exc
                    freshness = self._save(paths, key, _interrupted(current, exc, label, started))
                    raise
                filled[key] = result
                if progress is NO_PROGRESS:
                    task.update(result.summary)  # the done line carries it
                return self._save(
                    paths,
                    key,
                    Freshness(
                        refreshed_at=_utcnow(),
                        error=result.partial,
                        partial=result.partial is not None,
                        retry_at=result.retry_at,
                    ),
                )
        except UntapedError as exc:
            # the task closed `failed` on the way out, before the warning _serve emits
            if exc is not failure or freshness.refreshed_at is None:
                raise  # a lock that never came, or nothing cached to fall back to
            return freshness
        except KeyboardInterrupt as exc:
            if exc is failure and freshness.error is not None:
                # nothing else prints it: the CLI exits 130 bare
                error = freshness.error
                if freshness.refreshed_at is not None:  # else the next run fills again
                    hint = "pass --cached to read the cached data without checking"
                    error = error.model_copy(update={"hint": hint})
                current_sink().emit(error)
            raise

    def _serve[T, F: Filled](
        self,
        keys: Mapping[str, str],
        read: Callable[[], T],
        freshness: dict[str, Freshness],
        filled: dict[str, F],
        *,
        refresh: bool | None,
        started: datetime,
        empty: Callable[[T], bool] | None,
        max_age: timedelta,
    ) -> Served[T, F]:
        for key, state in freshness.items():
            if state.refreshed_at is None:  # only under --cached: anything else filled it
                raise NeverRefreshed(self, key, keys[key])
        value = read()  # once, over every key, outside any lock
        now = _utcnow()
        remembered = [
            (key, state)
            for key, state in freshness.items()
            if state.error is not None and (key not in filled or state.partial)
        ]
        # never under --cached: an error an earlier attempt stored is not this run's to raise
        if refresh is not False and remembered and empty is not None and empty(value):
            key, state = remembered[0]
            hint = (
                "pass --refresh to retry them"
                if state.partial
                else "retry later, or pass --cached to read the cached data"
            )
            raise self._raised(key, state, hint=hint)
        for key, state in freshness.items():
            if key in filled and not state.partial:
                continue  # whole and fresh from this run
            if (
                state.past_max_age(max_age, now=now)
                or state.partial
                or state.attempted_at == started
            ):
                line = self._stale(
                    key,
                    keys[key],
                    state,
                    now=now,
                    started=started,
                    cached=refresh is False,
                    max_age=max_age,
                )
                self._emit_stale(line)
        return Served(
            value=value,
            freshness=MappingProxyType(dict(freshness)),
            filled=MappingProxyType(dict(filled)),
            stale=any(_is_stale(state, max_age, now) for state in freshness.values()),
        )

    def cached[T](self, key: str, read: Callable[[], T]) -> Served[T] | None:
        """What is cached for ``key``, without a directive, a lock, a warning or a raise.

        ``None`` when the key was never refreshed. For in-process peeks (a
        screen opening on what is cached), never a command's answer: a
        command asked for ``--cached`` on a key never filled still fails.
        """
        state = self.freshness(key)
        if state.refreshed_at is None:
            return None
        stale = _is_stale(state, self.max_age(), _utcnow())
        return Served(value=read(), freshness=MappingProxyType({key: state}), stale=stale)

    def freshness(self, key: str) -> Freshness:
        """What the cache knows about ``key`` now, without a lock (a status row)."""
        return self._load(self._paths(key), key)

    def seed(self, key: str, *, refreshed_at: datetime) -> bool:
        """Record ``key`` as refreshed at ``refreshed_at`` if the cache knows nothing of it.

        For an owner whose data predates the cache (upgrade day). Writes only
        when no sidecar exists and no other process is filling the key;
        returns whether it wrote.
        """
        paths = self._paths(key)
        if paths.sidecar.exists():
            return False
        try:
            with self._held(paths, timeout=0.0, label=key):
                if paths.sidecar.exists():
                    return False
                return self._write(paths, key, Freshness(refreshed_at=_parsed(refreshed_at)))
        except _LockError as exc:
            if exc.busy:
                return False  # another process is filling it: it will write the sidecar
            raise

    def forget(self, key: str) -> None:
        """Forget what the cache knows about ``key``: its next read refreshes it.

        For an owner whose data under ``key`` changed or went away (a source
        redefined or removed). Waits for a fill of the key in progress; a key
        the cache knows nothing of is left as it is.
        """
        paths = self._paths(key)
        with self._lock(paths, key, NO_PROGRESS):
            paths.sidecar.unlink(missing_ok=True)

    # ---- the seams ----------------------------------------------------------

    def _folder(self) -> Path:
        from untaped.plugins.registry import named_plugin_dir  # noqa: PLC0415 - no cycle
        from untaped.profile_resolver import selected_profile  # noqa: PLC0415 - no cycle

        return named_plugin_dir(self.plugin) / "caches" / self.name / _segment(selected_profile())

    @staticmethod
    def _key_paths(folder: Path, key: str) -> _KeyPaths:
        segment = _segment(key)
        return _KeyPaths(folder, folder / f"{segment}.json", folder / f"{segment}.lock")

    def _paths(self, key: str) -> _KeyPaths:
        """``key``'s folder, sidecar and lock in the active profile."""
        return self._key_paths(self._folder(), key)

    def _load(self, paths: _KeyPaths, key: str) -> Freshness:
        """The sidecar's freshness: never refreshed when missing, unreadable or another key's."""
        try:
            raw = json.loads(paths.sidecar.read_text(encoding="utf-8"))
            if raw.get("untaped") != _FORMAT or raw.get("key") != key:
                return Freshness()
            refreshed_at = _parsed(raw.get("refreshed_at"))
            error = _failure(raw.get("error"))
            if error is None and raw.get("error") is not None:
                # a newer untaped's error: the attempt it describes cannot be told, the data can
                return Freshness(refreshed_at=refreshed_at)
            return Freshness(
                refreshed_at=refreshed_at,
                attempted_at=_parsed(raw.get("attempted_at")),
                error=error,
                partial=raw.get("partial") is True,
                retry_at=_parsed(raw.get("retry_at")),
            )
        except OSError, ValueError, AttributeError, TypeError, ValidationError:
            return Freshness()

    def _write(self, paths: _KeyPaths, key: str, freshness: Freshness) -> bool:
        error = freshness.error
        text = json.dumps(
            {
                "untaped": _FORMAT,
                "key": key,
                "refreshed_at": _iso(freshness.refreshed_at),
                "attempted_at": _iso(freshness.attempted_at),
                "error": None if error is None else error.model_dump(mode="json"),
                "partial": freshness.partial,
                "retry_at": _iso(freshness.retry_at),
            },
            ensure_ascii=False,
        )
        try:
            atomic_write(paths.sidecar, text)
        except OSError:
            # The next run refreshes again; the data the fill wrote is the owner's.
            _LOG.debug("could not write the cache sidecar %s", paths.sidecar, exc_info=True)
            return False
        return True

    def _save(self, paths: _KeyPaths, key: str, freshness: Freshness) -> Freshness:
        self._write(paths, key, freshness)
        return freshness

    @contextmanager
    def _task(self, text: str, parent: ProgressHandle) -> Iterator[ProgressHandle]:
        """The refresh's task; a phase of ``parent`` when one is passed."""
        if parent is not NO_PROGRESS:
            parent.update(phase=text)
            yield parent
            return
        with current_sink().progress(text) as task:
            yield task

    @staticmethod
    def _task_text(label: str, freshness: Freshness, max_age: timedelta) -> str:
        """The refresh's task, worded from the key's age."""
        if freshness.refreshed_at is None:
            return f"Refreshing {label} (first use)"
        age = duration_text((_utcnow() - freshness.refreshed_at).total_seconds())
        limit = duration_text(max_age.total_seconds())
        return f"Refreshing {label} ({age} old, max age {limit})"

    @contextmanager
    def _lock(
        self, paths: _KeyPaths, label: str, task: ProgressHandle, *, resume: str | None = None
    ) -> Iterator[None]:
        """Hold ``paths``' lock: at once when free, else after a visible wait (at most 600 s).

        The wait shows as ``task``'s text, then is cleared; on a parent's
        handle (``resume`` set) as its phase, then back to ``resume``.
        """
        with ExitStack() as stack:
            try:
                stack.enter_context(self._held(paths, timeout=0.0, label=label))
            except _LockError as exc:
                if not exc.busy:
                    raise
                if resume is None:
                    task.update(_WAITING)
                else:
                    task.update(phase=_WAITING)
                stack.enter_context(self._held(paths, timeout=_LOCK_TIMEOUT, label=label))
                if resume is None:
                    task.update()
                else:
                    task.update(phase=resume)
            yield

    @contextmanager
    def _held(self, paths: _KeyPaths, *, timeout: float, label: str) -> Iterator[None]:
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
                f"could not create {paths.dir}: {exc.strerror or exc}", system="local"
            ) from exc
        busy = f"another untaped process is still refreshing {label} after {duration_text(timeout)}"
        with file_lock(
            paths.lock,
            timeout=timeout,
            error=_LockError,
            busy=busy,
            failed=f"could not lock {paths.lock}",
        ):
            token = _HELD.set(_HELD.get() | {paths.lock})
            try:
                yield
            finally:
                _HELD.reset(token)

    def _stale(
        self,
        key: str,
        label: str,
        freshness: Freshness,
        *,
        now: datetime,
        started: datetime | None = None,
        cached: bool = False,
        plugin: str | None = None,
        max_age: timedelta | None = None,
    ) -> StaleData:
        """The ``untaped.stale`` line for ``key`` served from ``freshness``."""
        assert freshness.refreshed_at is not None
        hint = None
        if freshness.partial:
            hint = "pass --refresh to retry them"
        elif freshness.error is not None and freshness.attempted_at == started:
            hint = "retry later, or pass --cached to read it without checking"
        line = StaleData.describe(
            plugin=self.plugin if plugin is None else plugin,
            subject=label,
            cache=self.qualified,
            key=key,
            refreshed_at=freshness.refreshed_at,
            now=now,
            max_age=self.max_age() if max_age is None else max_age,
            attempted_at=freshness.attempted_at,
            retry_at=freshness.retry_at,
            error=freshness.error,
            partial=freshness.partial,
            hint=hint,
            cached=cached,
        )
        remembered = freshness.error is not None and freshness.attempted_at != started
        if remembered and not cached and not freshness.partial and freshness.retry_at:
            # an earlier run's failed attempt, inside its retry window
            line = line.model_copy(update={"message": f"{line.message}, or pass --refresh"})
        return line

    def _emit_stale(self, line: StaleData) -> None:
        """The one place a stale line leaves :meth:`serve`."""
        current_sink().emit(line)

    def _raised(self, key: str, freshness: Freshness, *, hint: str | None) -> UntapedError:
        """``freshness.error`` as the error to raise, naming the cache and the key."""
        assert freshness.error is not None
        error = freshness.error.to_exception()
        details: dict[str, object] = {**error.details, "cache": self.qualified, "key": key}
        if freshness.refreshed_at is not None:
            details["refreshed_at"] = format_utc(freshness.refreshed_at)
        error.details = details
        if error.hint is None:
            error.hint = hint
        return error


class _LockError(ConfigError):
    """A cache key's lock: busy (``unavailable``, exit 5), or its file could not be opened."""

    system = "local"

    @property
    def busy(self) -> bool:
        """Another process holds it (``file_lock`` marks that ``unavailable``)."""
        return self.category is ErrorCategory.UNAVAILABLE


def _failed(current: Freshness, error: UntapedError, started: datetime) -> Freshness:
    """``current`` after a fill that raised ``error``: the attempt and when to try again."""
    return replace(
        current,
        attempted_at=started,
        error=Failure.from_exception(error),
        partial=False,  # `error` is now the failure, no longer what a fill left out
        retry_at=_parsed(error.details.get("retry_at")) or started + RETRY_AFTER,
    )


def _interrupted(
    current: Freshness, error: KeyboardInterrupt, label: str, started: datetime
) -> Freshness:
    """``current`` after a fill stopped by Ctrl-C: remembered like a failed attempt."""
    message = f"interrupted while refreshing {label}"
    return replace(
        current,
        attempted_at=started,
        error=Failure.from_exception(error, message=message),
        partial=False,
        retry_at=started + RETRY_AFTER,
    )


def _segment(value: str) -> str:
    """``value`` as one file name, apart from every other value's on any filesystem.

    Sanitizing is lossy (``a/b`` and ``a_b``), some filesystems fold case
    (``A`` and ``a``) or Unicode, and all limit a name's bytes: such a value
    keeps an ASCII prefix of at most :data:`_SEGMENT` bytes and a hash.
    """
    segment = safe_path_segment(value)
    if segment == value and value.isascii() and value == value.lower() and len(value) <= _SEGMENT:
        return segment
    prefix = segment.encode("ascii", "ignore").decode("ascii")[:_SEGMENT] or "_"
    return f"{prefix}~{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def _failure(raw: object) -> Failure | None:
    """A sidecar's ``error``; ``None`` when absent or unreadable (a newer untaped's)."""
    if raw is None:
        return None
    try:
        return Failure.model_validate(raw)
    except ValidationError:
        return None


def _parsed(value: object) -> datetime | None:
    """An ISO 8601 time (naive taken as UTC), or ``None`` for anything else."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else format_utc(value)


# --------------------------------------------------------------------------- #
# The option and the status row
# --------------------------------------------------------------------------- #


def refresh_option(cache: Cache) -> Parameter:
    """The ``--refresh``/``--cached`` option for ``cache``'s directive; its help names the setting.

    Declare it as ``refresh: Annotated[bool | None, refresh_option(CACHE)] = None``
    and pass the value to :meth:`Cache.serve`.
    """
    return Parameter(
        name="--refresh",
        negative="--cached",
        help=(
            f"--refresh checks upstream for {cache.label} now; --cached reads what is cached "
            f"and never checks. Default: check when the cached data is older than "
            f"{cache.setting} ({cache.default_max_age_text})."
        ),
    )


class FreshnessRecord(Record):
    """The freshness fields of a cache's status row: subclass it, add the row's own.

    ``stale``: past its max age or with a remembered error.
    ``refresh_error`` is the last refresh's failure, or what a partial one
    left out; reporting it never fails the row (``error`` stays a failed
    row's).
    """

    refreshed_at: UtcTimestamp | None
    stale: bool
    max_age_seconds: int
    partial: bool = False
    attempted_at: UtcTimestamp | None = None
    retry_at: UtcTimestamp | None = None
    refresh_error: Annotated[Failure | None, NOT_COUNTED] = None

    @classmethod
    def fields(cls, freshness: Freshness, *, max_age: timedelta, now: datetime) -> dict[str, Any]:
        """The fields above for ``freshness``, to pass on to a subclass's constructor."""
        return {
            "refreshed_at": freshness.refreshed_at,
            "stale": _is_stale(freshness, max_age, now),
            "max_age_seconds": int(max_age.total_seconds()),
            "partial": freshness.partial,
            "attempted_at": freshness.attempted_at,
            "retry_at": freshness.retry_at,
            "refresh_error": freshness.error,
        }


__all__ = [
    "RETRY_AFTER",
    "Cache",
    "CallState",
    "Filled",
    "Freshness",
    "FreshnessRecord",
    "NeverRefreshed",
    "Served",
    "call_state",
    "current_call",
    "installed_utcnow",
    "refresh_option",
    "refresh_scope",
]
