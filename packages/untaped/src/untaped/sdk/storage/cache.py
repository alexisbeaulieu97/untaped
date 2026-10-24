"""Caches: data a plugin keeps from an upstream it can ask again, checked past its max age.

A plugin declares one :class:`Cache` per thing it keeps
(``Cache("ansible", "index", label="source data", max_age=timedelta(days=1))``)
and reads it through :meth:`Cache.serve`, which decides per key whether to
refresh (the *directive*), runs the plugin's ``fill`` when it must, reads the
command's answer once over every key, and says on stderr when that answer
comes from stale data (one ``untaped.stale`` warning per key), so no caller
words or forgets it. Inside a provider call ``gather`` made, the warning
waits for whoever uses the provider's answer (docs/contracts.md).

The directive is ``refresh``: ``None`` (*auto*) refreshes a key past its max
age, failing fast when upstream is unreachable (:func:`untaped.sdk.network.upstream.fail_fast`);
``True`` checks upstream now with full retries; ``False`` reads what is cached
and never checks. :func:`refresh_option` is the ``--refresh``/``--cached``
option that sets it; ``gather`` opens one for every provider call it makes
(:func:`call_state`), which ``serve()`` reads when its own is ``None``.

Layout: each key is one file under ``<plugin dir>/cached/<name>/<settings digest>/``
(``cached/<name>.v<layout>/`` when the cache follows a store; no digest
folder under ``split="none"``; :mod:`untaped.sdk.storage._digest` names it) on
core's data file (:mod:`untaped.sdk.storage.data_file`):
its first line says what the cache knows about the key (:class:`Freshness`),
and a cache declared with ``model=`` keeps the value itself on the second,
one file per version of the value. Files are written atomically and
owner-only; ``<segment>.lock`` beside them is held while the key is filled,
and only then. Readers never take it. The lock is not re-entrant: a fill
never serves its own cache. Nested caches lock outer, then inner; the repo
store's own lock is always the innermost.

When the code that fills a cache changes what it would store, ``version=``
names that code's version (:func:`code_version` hashes a module and the
distributions it relies on): each key's file keeps the version it was
filled at, and a key filled at another one is *outdated*, due and stale the
way a key past its max age is.

A cache declared with ``max_age=None`` is *version-only*: its value depends on
the code that fills it and on nothing upstream, so it is refreshed only when
that code's version changes (core's composed declaration, keyed by
``installed_code()``). It has no max-age setting, never serves another
version's value, drops the key's other versions once it fills this one, and
says nothing on stderr: a home it cannot write leaves the value in memory.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import importlib.metadata
import importlib.util
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, overload

from cyclopts import Parameter
from pydantic import BaseModel, ConfigDict, SkipValidation, TypeAdapter, ValidationError

from untaped.runtime.settings import load_settings_section, registered_profile_model
from untaped.runtime.sink import current_sink
from untaped.sdk._block import OwnedBlock
from untaped.sdk.diagnostic import NOT_COUNTED, Failure, StaleData, format_utc, parse_utc
from untaped.sdk.errors import ConfigError, ErrorCategory, UntapedError
from untaped.sdk.git import safe_path_segment
from untaped.sdk.network.upstream import fail_fast, full_retries
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.records import Record, UtcTimestamp
from untaped.sdk.storage._fs import file_lock
from untaped.sdk.storage.store import Store
from untaped.sdk.words import duration_text

_LOG = logging.getLogger("untaped.cache")

#: How long auto waits after a failed or interrupted refresh when upstream gave no time.
RETRY_AFTER: Final = timedelta(minutes=5)
#: Seconds a refresh waits for another process refreshing the same key.
_LOCK_TIMEOUT = 600.0
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_WAITING = "waiting for another untaped process"
#: Longest key segment kept in a sidecar's name, under any filesystem's limit.
_SEGMENT = 100


# --------------------------------------------------------------------------- #
# The directive gather opens
# --------------------------------------------------------------------------- #


@dataclass
class CallState:
    """One provider call's cache directive and what the call served.

    ``stale_lines`` are the stale lines the call's caches served: under a
    call state :meth:`Cache.serve` defers them to whoever reads the answer.
    ``caller`` names the provider call (``library's BookSource.books``) for an
    error raised from inside it.
    """

    refresh: bool | None
    caller: str = "a provider"
    stale: BaseException | None = None
    refreshed_at: datetime | None = None
    stale_lines: list[StaleData] = field(default_factory=list)

    def served(self, at: datetime) -> None:
        if self.refreshed_at is None or at < self.refreshed_at:
            self.refreshed_at = at


_CALL: ContextVar[CallState | None] = ContextVar("untaped_contract_call", default=None)


@contextmanager
def call_state(refresh: bool | None, *, caller: str = CallState.caller) -> Iterator[CallState]:
    """Run a provider call under ``refresh``; yields what the call served."""
    state = CallState(refresh, caller=caller)
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


_ANSWERING: ContextVar[tuple[Cache[Any], str] | None] = ContextVar(
    "untaped_answering", default=None
)


@contextmanager
def answering(cache: Cache[Any], provider: str) -> Iterator[None]:
    """Serve ``cache`` as the answer cache, answering for ``provider``.

    Its lines name ``provider`` and suggest no option: whoever reads an
    answer has no ``--refresh``/``--cached`` of the answer cache's own.
    """
    token = _ANSWERING.set((cache, provider))
    try:
        yield
    finally:
        _ANSWERING.reset(token)


def _answering_for(cache: Cache[Any]) -> str | None:
    """The provider ``cache`` answers for as the answer cache, else ``None``."""
    scope = _ANSWERING.get()
    return scope[1] if scope is not None and scope[0] is cache else None


# --------------------------------------------------------------------------- #
# The wall clock (``untaped.sdk.testing.cache_clock`` installs its own)
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
    """What a cache knows about one key; kept in the first line of its file.

    ``refreshed_at`` is the last completed fill (``None``: never);
    ``attempted_at`` the last one that did not complete (failed or
    interrupted), and ``error`` why. After a completed fill that left items
    out, ``partial`` is set and ``error`` names them. ``retry_at`` is when
    auto may try again: upstream's own time, else ``attempted_at`` plus
    :data:`RETRY_AFTER`. ``version`` is the cache's version at the last
    completed fill (``None``: unversioned, seeded, or never filled);
    ``outdated``: the cache has a version and the key was filled at another
    one, so it is due and stale whatever its age.
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


def _is_stale(freshness: Freshness, max_age: timedelta, now: datetime) -> bool:
    """Past its max age, outdated, or with a remembered error (a failed refresh, a partial fill)."""
    return (
        freshness.past_max_age(max_age, now=now)
        or freshness.outdated
        or freshness.error is not None
    )


class Filled[M = None](BaseModel):
    """What a ``fill`` returns; an owner subclasses it to keep a result of its own.

    ``value``: what a cache declared with ``model=`` keeps for the key; its
    fill must set it, and any other cache's fill must leave it out.
    ``partial``: the fill completed but left items out (one ``Failure``
    naming them); every read until the next whole refresh warns about it.
    ``retry_at``: when upstream said to come back (a rate-limit reset).
    ``summary``: the refresh task's last line (``780 repos, 12 changed``).
    """

    model_config = ConfigDict(frozen=True)

    # Not validated here: an unparameterized ``Filled(value=...)`` would check it against
    # ``None``. The cache checks the value against its model= when it keeps it.
    value: SkipValidation[M | None] = None
    partial: Failure | None = None
    retry_at: UtcTimestamp | None = None
    summary: str | None = None


@dataclass(frozen=True)
class Served[T, F: Filled[Any] = Filled]:
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

    def __init__(self, cache: Cache[Any], key: str, label: str) -> None:
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
class ValueTyping:
    """How a typed cache's values are checked and told apart: an adapter and its shape's hash.

    ``model`` is the class ``model=`` named (``None`` for the answer cache's
    per-call type), which a fill's value must be an instance of.
    """

    adapter: TypeAdapter[Any]
    shape: str
    model: type[BaseModel] | None = None


@dataclass(frozen=True)
class _Kept:
    """What a key's file holds: its freshness and, for a typed cache, the value and its bytes.

    ``damaged``: this version's file was there but could not be used.
    """

    freshness: Freshness
    value: Any = None
    raw: bytes | None = None
    damaged: bool = False


#: The lock paths this context holds: a fill serving its own cache fails at once.
_HELD: ContextVar[frozenset[Path]] = ContextVar("untaped_cache_held", default=frozenset())
#: How long another partition's file of a key outlives its last refresh, at least.
_PARTITION_HORIZON = timedelta(days=30)
#: A version's file of a key: ``<segment>[.p<hash>].v<hash>.json``.
_VERSIONED = re.compile(r"(?P<stem>.+)\.v[0-9a-f]{8}\.json")
#: An untyped cache's one file per key and partition.
_PLAIN = re.compile(r"(?P<stem>.+)\.json")


class Cache[M = None](OwnedBlock):
    """Data a plugin keeps from an upstream, refreshed past ``max_age``.

    A plugin class holding it names it (``motd = Cache(label=..., max_age=...)``
    is the plugin's cache ``motd``); the 11.x spelling passes ``plugin`` and
    ``name`` first. The name is one settings segment (``[a-z][a-z0-9_]*``); ``label`` names
    the data for a human (``source data``). ``max_age`` is the declared
    default, in whole seconds (``0``: check every time), or ``None`` for a
    version-only cache, which needs ``model=`` and ``version=``, refreshes
    only when the version changes and has no setting; list the cache on
    ``PluginSpec.caches`` and its setting ``<plugin>.caches.<name>.max_age_seconds``
    overrides it. ``renamed_from`` names old keys of the plugin's section
    (dotted, relative to it) that are still read as that setting, with a
    warning, until the plugin's major ``removed_in`` (undated without it).
    ``version`` returns the version of the code that fills the
    cache (usually :func:`code_version`); it is called each time the cache
    is read (once per :meth:`serve`), never at import, and a key filled at
    another version is refreshed like an expired one. ``store`` ties the
    cache's files to a :class:`~untaped.sdk.storage.store.Store`'s layout: they live under
    ``cached/<name>.v<layout>/``, so a key is never fresh over another
    layout's file, and a complete fill (no error, not partial) ends with
    :meth:`Store.prune() <untaped.sdk.storage.store.Store.prune>`.

    ``model`` makes the cache keep the value itself (a ``Cache[M]``): a
    pydantic model class, or a function of no arguments returning it when
    importing the class would load more than a declaration should. The
    class is resolved and hashed on first use. Each key's value and
    freshness are one file per version of the value (its type's shape and
    ``version``): a key filled by another version is served, outdated, when
    a refresh fails or under ``--cached``. ``split`` says whether the files
    are kept per settings of the plugin's own section (``"settings"``: a
    folder per :mod:`settings digest <untaped.sdk.storage._digest>`, shared by
    every profile whose section reads the same) or once for all of them
    (``"none"``, when the key and partition already say whose data it is).
    Two declarations with the same values (the same callables) are equal.
    """

    block_kind = "cache"

    label: str
    #: ``None`` for a version-only cache, which has no max age.
    default_max_age: timedelta | None
    renamed_from: tuple[str, ...]
    removed_in: int | None
    version: Callable[[], str] | None
    store: Store | None
    model: type[M] | Callable[[], type[M]] | None
    split: Literal["settings", "none"]

    @overload
    def __init__(
        self: Cache[None],
        plugin: str | None = None,
        name: str | None = None,
        *,
        label: str,
        max_age: timedelta | None,
        renamed_from: Sequence[str] = (),
        removed_in: int | None = None,
        version: Callable[[], str] | None = None,
        store: Store | None = None,
        split: Literal["settings", "none"] = "settings",
    ) -> None: ...

    @overload
    def __init__(
        self,
        plugin: str | None = None,
        name: str | None = None,
        *,
        label: str,
        max_age: timedelta | None,
        renamed_from: Sequence[str] = (),
        removed_in: int | None = None,
        version: Callable[[], str] | None = None,
        store: Store | None = None,
        model: type[M] | Callable[[], type[M]],
        split: Literal["settings", "none"] = "settings",
    ) -> None: ...

    def __init__(
        self,
        plugin: str | None = None,
        name: str | None = None,
        *,
        label: str,
        max_age: timedelta | None,
        renamed_from: Sequence[str] = (),
        removed_in: int | None = None,
        version: Callable[[], str] | None = None,
        store: Store | None = None,
        model: type[M] | Callable[[], type[M]] | None = None,
        split: Literal["settings", "none"] = "settings",
    ) -> None:
        from untaped.runtime.registry import check_plugin_name  # noqa: PLC0415 - no cycle
        from untaped.sdk.marks import check_removed_in  # noqa: PLC0415 - no cycle

        super().__init__(plugin, name)
        if plugin is not None and name is not None:
            check_plugin_name(plugin)
            if (problem := self.name_problem(name)) is not None:
                raise ConfigError(problem)
        where = self.where
        if max_age is None:
            _check_version_only(
                where, model=model, version=version, renamed_from=renamed_from, store=store
            )
        elif max_age < timedelta(0) or max_age % timedelta(seconds=1):
            raise ConfigError(f"cache {where}: max_age must be whole seconds, not negative")
        self.label = label
        self.default_max_age = max_age
        self.renamed_from = _old_keys(where, renamed_from)
        if check_removed_in(removed_in) is not None and not self.renamed_from:
            raise ConfigError(f"cache {where}: removed_in dates renamed_from, which is empty")
        self.removed_in = removed_in
        self.version = version
        if store is not None and not isinstance(store, Store):
            raise ConfigError(f"cache {where}: store takes a Store, not {store!r}")
        if store is not None and plugin is None:
            raise ConfigError(
                f"cache {where}: store goes with the plugin and name; a plugin class's cache "
                "will follow a Dir instead"
            )
        if store is not None and store.plugin != plugin:
            raise ConfigError(f"cache {where}: store {store.qualified} belongs to another plugin")
        if model is not None and not callable(model):
            raise ConfigError(
                f"cache {where}: model takes a model class or a function returning one"
            )
        if split not in ("settings", "none"):
            raise ConfigError(f"cache {where}: split is 'settings' or 'none', not {split!r}")
        self.model = model
        self.split = split
        self._resolved: ValueTyping | None = None
        self.store = store
        if store is not None:
            store._follow(self)  # its folder moves with the store's layouts

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if _NAME.fullmatch(name):
            return None
        return f"cache name {name!r} must be one settings segment (pattern {_NAME.pattern})"

    def _identity(self) -> tuple[object, ...]:
        if not self.spelled:
            return (id(self),)  # a bound cache is itself, as every block is
        return (
            self._plugin,
            self._name,
            self.label,
            self.default_max_age,
            self.renamed_from,
            self.removed_in,
            self.version,
            self.store,
            self.model,
            self.split,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Cache):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        if not self.spelled:
            return f"Cache(label={self.label!r})"
        return f"Cache({self._plugin!r}, {self._name!r}, label={self.label!r})"

    @property
    def qualified(self) -> str:
        """``<plugin>.<name>``: what a stale line's ``cache`` says."""
        return f"{self.plugin}.{self.name}"

    @property
    def setting(self) -> str:
        """The setting that tunes the max age: ``<plugin>.caches.<name>.max_age_seconds``."""
        return f"{self.plugin}.caches.{self.name}.max_age_seconds"

    @property
    def version_only(self) -> bool:
        """Whether the cache was declared with ``max_age=None``: refreshed by its version alone."""
        return self.default_max_age is None

    @property
    def default_max_age_text(self) -> str:
        """The declared max age for a human: ``24 h``."""
        return duration_text(self._declared_max_age().total_seconds())

    def _declared_max_age(self) -> timedelta:
        if self.default_max_age is None:
            raise ConfigError(f"cache {self.where} is version-only: it has no max age")
        return self.default_max_age

    def max_age(self) -> timedelta:
        """The max age in force now: the setting, read at call time, else the declared default.

        The default holds when the plugin's composed section has no such
        cache (the plugin is used uncomposed, or does not list the cache on
        ``PluginSpec.caches``). An invalid setting raises the settings
        :class:`ConfigError`; a version-only cache, which has none, raises
        one too.
        """
        default = self._declared_max_age()
        model = registered_profile_model(self.plugin)
        declared = None if model is None else model.model_fields.get("caches")
        if declared is None or self.name not in getattr(declared.annotation, "model_fields", {}):
            return default
        held = getattr(load_settings_section(self.plugin).caches, self.name)
        return timedelta(seconds=held.max_age_seconds)

    def _version_now(self) -> str | None:
        """The version of the code that fills the cache now; ``None`` when unversioned."""
        return None if self.version is None else self.version()

    def _typing(self) -> ValueTyping | None:
        """The value's type when the cache keeps one (``model=``), resolved on first use."""
        if self.model is None:
            return None
        if self._resolved is None:
            model = self.model if isinstance(self.model, type) else self.model()
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise ConfigError(
                    f"cache {self.qualified}: model is not a pydantic model: {model!r}"
                )
            self._resolved = value_typing(model, model=model)
        return self._resolved

    # ---- reading and filling ------------------------------------------------

    @overload
    def serve[T, F: Filled](
        self: Cache[None],
        keys: Mapping[str, str],
        *,
        refresh: bool | None,
        fill: Callable[[str, ProgressHandle], F],
        read: Callable[[], T],
        partition: str | None = None,
        empty: Callable[[T], bool] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> Served[T, F]: ...

    @overload
    def serve(
        self,
        keys: Mapping[str, str],
        *,
        refresh: bool | None,
        fill: Callable[[str, ProgressHandle], Filled[M]],
        partition: str | None = None,
        empty: Callable[[Mapping[str, M]], bool] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> Served[Mapping[str, M], Filled[M]]: ...

    def serve(
        self,
        keys: Mapping[str, str],
        *,
        refresh: bool | None,
        fill: Callable[[str, ProgressHandle], Any],
        read: Callable[[], Any] | None = None,
        partition: str | None = None,
        empty: Callable[[Any], bool] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> Served[Any, Any]:
        """Refresh what ``refresh`` asks for, then answer over every key.

        ``keys`` maps each key to its label for a human (``source prod``).
        ``fill(key, handle)`` refreshes one key from upstream and reports
        through ``handle``; it raises an :class:`~untaped.sdk.errors.UntapedError`
        when the refresh fails, with upstream's own retry time, when it knows
        one, in ``details["retry_at"]`` (ISO 8601). A cache with ``model=``
        keeps what the fill returns as ``Filled(value=...)`` and answers a
        mapping of each key to its value; any other cache answers ``read()``,
        called once over every key. ``partition`` keeps another set of files
        for the same keys (a credential, hashed into the file names and never
        shown). ``empty(value)`` says whether the answer found nothing: a
        stale empty answer fails with the refresh's error instead of passing
        for an absence. Under a ``progress`` handle no task is opened: the
        refresh is a phase of the caller's.

        A failed refresh with data cached serves that data with a warning; with
        none, the error is raised. ``--cached`` on a key never filled raises
        :class:`NeverRefreshed`. A Ctrl-C during a fill is remembered like a
        failed attempt and said as one ``error:`` line before it propagates.
        """
        typing = self._typing()
        if typing is None and read is None:
            raise ConfigError(f"cache {self.qualified}: serve() needs read= without model=")
        if typing is not None and read is not None:
            raise ConfigError(
                f"cache {self.qualified}: a cache with model= answers its kept values; "
                "serve() takes no read="
            )
        if self.version_only:
            assert typing is not None  # a version-only cache is declared with model=
            return self._serve_version(
                keys,
                refresh=refresh,
                fill=fill,
                typing=typing,
                partition=partition,
                progress=progress,
            )
        return self._serve_with(
            keys,
            refresh=refresh,
            fill=fill,
            typing=typing,
            read=read,
            partition=partition,
            empty=empty,
            progress=progress,
        )

    def _serve_with(
        self,
        keys: Mapping[str, str],
        *,
        refresh: bool | None,
        fill: Callable[[str, ProgressHandle], Filled[Any]],
        typing: ValueTyping | None,
        read: Callable[[], Any] | None = None,
        partition: str | None = None,
        empty: Callable[[Any], bool] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> Served[Any, Any]:
        """:meth:`serve` for a value of ``typing``'s type (``None``: answered by ``read``).

        The answer cache serves through it with each provider's own type.
        """
        max_age, started, version = self.max_age(), _utcnow(), self._version_now()
        if refresh is None:
            refresh = refresh_scope()
        folder = self._folder()
        paths = {key: self._paths(key, partition, folder=folder) for key in keys}
        kept = {key: self._load(paths[key], key, version, typing) for key in keys}
        filled: dict[str, Filled[Any]] = {}
        for key, label in keys.items():
            if kept[key].freshness.due(refresh, max_age=max_age, now=started):
                kept[key] = self._refresh(
                    key,
                    label,
                    paths[key],
                    kept[key],
                    filled,
                    fill=fill,
                    refresh=refresh,
                    started=started,
                    max_age=max_age,
                    version=version,
                    typing=typing,
                    progress=progress,
                )
        if typing is None:
            assert read is not None
            answer = read
        else:
            values = MappingProxyType({key: entry.value for key, entry in kept.items()})
            answer = lambda: values  # noqa: E731 - the kept values, read once already
        return self._serve(
            keys,
            answer,
            {key: entry.freshness for key, entry in kept.items()},
            filled,
            refresh=refresh,
            started=started,
            empty=empty,
            max_age=max_age,
        )

    def _refresh(
        self,
        key: str,
        label: str,
        paths: _KeyPaths,
        kept: _Kept,
        filled: dict[str, Filled[Any]],
        *,
        fill: Callable[[str, ProgressHandle], Filled[Any]],
        refresh: bool | None,
        started: datetime,
        max_age: timedelta,
        version: str | None,
        typing: ValueTyping | None,
        progress: ProgressHandle,
    ) -> _Kept:
        """Fill ``key`` under its task and lock; what the cache keeps of it afterwards.

        Raises when the fill fails with nothing cached to fall back to, when
        the lock never comes, and on Ctrl-C.
        """
        failure: BaseException | None = None
        own = paths.file(None if typing is None else versioned(typing, version))
        try:
            text = self._task_text(label, kept, max_age)
            with (
                self._task(text, progress) as task,
                # back to the parent's phase after a wait; an own task has none
                self._lock(paths, label, task, resume=None if progress is NO_PROGRESS else text),
            ):
                current = kept = self._load(paths, key, version, typing)
                refreshed_at = current.freshness.refreshed_at
                # another version's typed value is a fallback, never an answer: fill this one's
                fallback = typing is not None and current.freshness.outdated
                if refreshed_at is not None and refreshed_at >= started and not fallback:
                    return current  # refreshed while this run waited: serve it, under --refresh too
                due = current.freshness.due(None, max_age=max_age, now=_utcnow())
                if refresh is None and not due:
                    return current  # auto: no longer due (another run failed meanwhile)
                try:
                    # the budget is a fallback too: a first fill keeps its full retries
                    budget = refresh is None and refreshed_at is not None
                    with fail_fast() if budget else full_retries():
                        result = fill(key, task)
                except UntapedError as exc:
                    failure = exc
                    attempt = _failed(current.freshness, exc, started)
                    kept = self._remember(own, key, current, attempt, typing)
                    raise
                except KeyboardInterrupt as exc:
                    failure = exc
                    attempt = _interrupted(current.freshness, exc, label, started)
                    kept = self._remember(own, key, current, attempt, typing)
                    raise
                filled[key] = result
                if progress is NO_PROGRESS:
                    task.update(result.summary)  # the done line carries it
                saved, kept_it = self._filled(
                    paths, own, key, label, result, version, typing, max_age
                )
            if saved.error is None and kept_it:
                if self.store is not None:
                    # a complete fill: this layout has data, so the ones nothing holds may go
                    self.store.prune()
                _remove_legacy(self.plugin)
            return _Kept(saved, value=None if typing is None else result.value)
        except UntapedError as exc:
            # the task closed `failed` on the way out, before the warning _serve emits
            if exc is not failure or kept.freshness.refreshed_at is None:
                raise  # a lock that never came, or nothing cached to fall back to
            return kept
        except KeyboardInterrupt as exc:
            if exc is failure and kept.freshness.error is not None:
                self._say_interrupted(kept.freshness)
            raise

    def _filled(
        self,
        paths: _KeyPaths,
        own: Path,
        key: str,
        label: str,
        result: Filled[Any],
        version: str | None,
        typing: ValueTyping | None,
        max_age: timedelta,
    ) -> tuple[Freshness, bool]:
        """Keep what a fill returned, under the key's lock: the key's freshness, and if kept.

        Only a value kept lets older files go: until then they are what a
        later read falls back on.
        """
        value = self._filled_value(result, typing)
        saved = Freshness(
            refreshed_at=_utcnow(),
            error=result.partial,
            partial=result.partial is not None,
            retry_at=result.retry_at,
            version=version,
        )
        kept_it = self._keep(own, key, label, saved, typing, value)
        if kept_it and saved.error is None:
            if typing is not None:
                self._sweep(paths, key, own, max_age)
            self._sweep_siblings(paths, key)
        return saved, kept_it

    def _serve_version(
        self,
        keys: Mapping[str, str],
        *,
        refresh: bool | None,
        fill: Callable[[str, ProgressHandle], Filled[Any]],
        typing: ValueTyping,
        partition: str | None,
        progress: ProgressHandle,
    ) -> Served[Any, Any]:
        """:meth:`serve` for a version-only cache: this version's value, else a fill, quietly.

        No task, lock or stale line: two processes filling at once compute the
        same value and each writes it whole. ``--cached`` on a key this
        version never filled raises :class:`NeverRefreshed`; a fill that
        raises propagates, since no other version's value may stand in.
        """
        folder = self._folder()
        values: dict[str, Any] = {}
        freshness: dict[str, Freshness] = {}
        filled: dict[str, Filled[Any]] = {}
        for key, label in keys.items():
            paths = self._paths(key, partition, folder=folder)
            kept = None if refresh else self._own_version(paths, key, typing)
            if kept is None:
                if refresh is False:
                    raise NeverRefreshed(self, key, label)
                result = filled[key] = fill(key, progress)
                kept = self._keep_version(paths, key, result, typing)
            values[key], freshness[key] = kept.value, kept.freshness
        return Served(
            value=MappingProxyType(values),
            freshness=MappingProxyType(freshness),
            filled=MappingProxyType(filled),
        )

    def _own_version(self, paths: _KeyPaths, key: str, typing: ValueTyping) -> _Kept | None:
        """This version's value of ``key``; ``None`` when it was never kept or no longer reads."""
        from untaped.sdk.storage.data_file import read_lines  # noqa: PLC0415 - first use

        version = self._version_now()
        kept = _read_kept(
            read_lines(paths.file(versioned(typing, version))), key, version, typing, check=True
        )
        if kept is None or kept.freshness.refreshed_at is None or kept.freshness.partial:
            return None
        return kept

    def _keep_version(
        self, paths: _KeyPaths, key: str, result: Filled[Any], typing: ValueTyping
    ) -> _Kept:
        """Keep a version-only fill, then drop the key's other versions; the value either way.

        A partial fill, a value that won't serialise or a file that can't be
        written is served from memory, and the next run fills again.
        """
        version = self._version_now()
        own = paths.file(versioned(typing, version))
        saved = Freshness(refreshed_at=_utcnow(), version=version)
        value = self._filled_value(result, typing)
        if (
            result.partial is None
            and value is not None
            and self._write(own, key, saved, typing, value)
        ):
            for entry, stem, _ in self._key_files(paths.dir, key, _VERSIONED):
                if stem == paths.stem and entry != own:
                    with contextlib.suppress(OSError):
                        entry.unlink(missing_ok=True)
        return _Kept(saved, value=result.value)

    def _say_interrupted(self, freshness: Freshness) -> None:
        """Say the Ctrl-C a fill stopped on: nothing else prints it, the CLI exits 130 bare."""
        assert freshness.error is not None
        error = freshness.error
        # else the next run fills again; the answer cache's reader has no --cached
        if freshness.refreshed_at is not None and _answering_for(self) is None:
            hint = "pass --cached to read the cached data without checking"
            error = error.model_copy(update={"hint": hint})
        current_sink().emit(error)

    def _filled_value(self, result: Filled[Any], typing: ValueTyping | None) -> bytes | None:
        """The bytes of the value ``result`` carries; refused when the cache keeps none, or does."""
        if typing is None:
            if result.value is not None:
                raise TypeError(
                    f"a fill of {self.qualified} returned a value, which a cache without "
                    "model= never keeps"
                )
            return None
        if "value" not in result.model_fields_set:
            raise TypeError(
                f"a fill of {self.qualified} returned no value: Filled(value=...) carries "
                "what a cache with model= keeps"
            )
        if typing.model is not None and not isinstance(result.value, typing.model):
            raise TypeError(
                f"a fill of {self.qualified} returned a {type(result.value).__name__}, "
                f"not the {typing.model.__name__} its model= names"
            )
        try:
            return typing.adapter.dump_json(
                result.value, by_alias=True, round_trip=True, warnings=False
            )
        except Exception:  # the live answer still stands; the next run fills again
            _LOG.debug("could not serialise a value of %s", self.qualified, exc_info=True)
            return None

    def _serve[T, F: Filled[Any]](
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
        provider = _answering_for(self)
        for key, state in freshness.items():
            if key in filled and not state.partial:
                continue  # whole and fresh from this run
            if (
                state.past_max_age(max_age, now=now)
                or state.outdated
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
                    plugin=provider,
                    max_age=max_age,
                    options=provider is None,
                )
                self._emit_stale(line)
        return Served(
            value=value,
            freshness=MappingProxyType(dict(freshness)),
            filled=MappingProxyType(dict(filled)),
            stale=any(_is_stale(state, max_age, now) for state in freshness.values()),
        )

    @overload
    def cached[T](
        self: Cache[None], key: str, read: Callable[[], T], *, partition: str | None = None
    ) -> Served[T] | None: ...

    @overload
    def cached(self, key: str, *, partition: str | None = None) -> Served[M] | None: ...

    def cached(
        self, key: str, read: Callable[[], Any] | None = None, *, partition: str | None = None
    ) -> Served[Any] | None:
        """What is cached for ``key``, without a directive, a lock, a warning or a raise.

        ``None`` when the key was never refreshed. A cache with ``model=``
        answers the kept value and takes no ``read``; any other answers
        ``read()``. For in-process peeks (a screen opening on what is
        cached), never a command's answer: a command asked for ``--cached``
        on a key never filled still fails.
        """
        typing = self._typing()
        if (typing is None) == (read is None):
            raise ConfigError(
                f"cache {self.qualified}: cached() takes read= exactly when the cache has no model="
            )
        paths = self._paths(key, partition)
        if self.version_only:
            assert typing is not None  # a version-only cache is declared with model=
            own = self._own_version(paths, key, typing)
            if own is None:
                return None
            return Served(value=own.value, freshness=MappingProxyType({key: own.freshness}))
        kept = self._load(paths, key, self._version_now(), typing)
        state = kept.freshness
        if state.refreshed_at is None:
            return None
        stale = _is_stale(state, self.max_age(), _utcnow())
        value = kept.value if read is None else read()
        return Served(value=value, freshness=MappingProxyType({key: state}), stale=stale)

    def freshness(self, key: str, *, partition: str | None = None) -> Freshness:
        """What the cache knows about ``key`` now, without a lock (a status row).

        Finding the folder loads the plugin's settings, so an invalid section
        raises its :class:`ConfigError`, as :meth:`max_age` does.

        Reads each file's first line only: a value that no longer reads is
        found by :meth:`serve`, which refills it.
        """
        paths = self._paths(key, partition)
        return self._load(paths, key, self._version_now(), self._typing(), check=False).freshness

    def seed(self, key: str, *, refreshed_at: datetime) -> bool:
        """Record ``key`` as refreshed at ``refreshed_at`` if the cache knows nothing of it.

        For an owner whose data predates the cache (upgrade day). Writes only
        when no file exists and no other process is filling the key;
        returns whether it wrote. The seeded key has no version: under a
        versioned cache it is outdated, so its first read refreshes it. A
        cache with ``model=`` has no value to seed from and refuses.
        """
        if self.model is not None:
            raise ConfigError(
                f"cache {self.qualified}: seed() records data the owner holds; "
                "a cache with model= holds its own"
            )
        paths = self._paths(key)
        if paths.file().exists():
            return False
        try:
            with self._held(paths, timeout=0.0, label=key):
                if paths.file().exists():
                    return False
                return self._write(
                    paths.file(), key, Freshness(refreshed_at=parse_utc(refreshed_at)), None, None
                )
        except _LockError as exc:
            if exc.busy:
                return False  # another process is filling it: it will write the file
            raise

    def forget(self, key: str, *, partition: str | None = None) -> None:
        """Forget what the cache keeps for ``key``: its next read refreshes it.

        For an owner whose data under ``key`` changed or went away (a source
        redefined or removed). Every version's value goes with its
        freshness. Waits for a fill of the key in progress; a key the cache
        knows nothing of is left as it is.
        """
        from untaped.sdk.storage.data_file import Lines, read_lines  # noqa: PLC0415 - first use

        paths = self._paths(key, partition)
        with self._lock(paths, key, NO_PROGRESS):
            paths.file().unlink(missing_ok=True)
            for entry in _listed(paths.dir):
                matched = _VERSIONED.fullmatch(entry.name)
                if matched is None or matched["stem"] != paths.stem:
                    continue
                found = read_lines(entry, value=False)
                if isinstance(found, Lines) and found.stamp.get("key") != key:
                    continue  # another key whose name reads like a version of this one
                entry.unlink(missing_ok=True)

    # ---- the seams ----------------------------------------------------------

    def _root(self, layout: int | None = None) -> Path:
        """``plugins/<plugin>/cached/<name>``, or ``<name>.v<layout>`` for a store's layout."""
        from untaped.runtime.registry import named_plugin_dir  # noqa: PLC0415 - no cycle

        folder = self.name if layout is None else f"{self.name}.v{layout}"
        return named_plugin_dir(self.plugin) / "cached" / folder

    def _legacy_root(self) -> Path:
        """``plugins/<plugin>/caches/<name>``: where untaped 11.0 kept this cache's sidecars."""
        from untaped.runtime.registry import named_plugin_dir  # noqa: PLC0415 - no cycle

        return named_plugin_dir(self.plugin) / "caches" / self.name

    def _folder(self) -> Path:
        """The folder this cache's files are in for the active settings: computed here only.

        Under ``split="settings"``, the root's folder named by the plugin's
        settings digest (:mod:`untaped.sdk.storage._digest`).
        """
        from untaped.sdk.storage._digest import settings_digest  # noqa: PLC0415 - no cycle

        root = self._root(None if self.store is None else self.store.layout)
        return root if self.split == "none" else root / settings_digest(self.plugin)

    def _paths(
        self, key: str, partition: str | None = None, *, folder: Path | None = None
    ) -> _KeyPaths:
        """``key``'s folder, file stem and lock in ``partition`` (the active settings' folder)."""
        folder = self._folder() if folder is None else folder
        stem = _segment(key) if partition is None else f"{_segment(key)}.p{_digest(partition)}"
        return _KeyPaths(folder, stem, folder / f"{stem}.lock")

    def _load(
        self,
        paths: _KeyPaths,
        key: str,
        version: str | None,
        typing: ValueTyping | None,
        *,
        check: bool = True,
    ) -> _Kept:
        """What ``key``'s file holds: never refreshed when missing, unreadable or another key's.

        ``version`` is the cache's version now: a key filled at another one
        is ``outdated``. A typed cache reads this version's file, else the
        newest file of the same type another version filled (outdated);
        ``check`` validates the value (``False``: the first line only).
        """
        from untaped.sdk.storage.data_file import (  # noqa: PLC0415 - first use
            Lines,
            Unreadable,
            read_lines,
        )

        if typing is None:
            found = read_lines(paths.file(), value=False)
            if not isinstance(found, Lines) or found.stamp.get("key") != key:
                return _Kept(Freshness())
            return _Kept(_freshness_of(found.stamp, version, typed=False))
        own = paths.file(versioned(typing, version))
        found = read_lines(own, value=check)
        kept = _read_kept(found, key, version, typing, check=check)
        if kept is not None and kept.freshness.refreshed_at is not None:
            return kept
        fallback = self._fallback(paths, key, own, version, typing, check=check)
        if fallback is None:
            return kept or _Kept(Freshness(), damaged=isinstance(found, (Lines, Unreadable)))
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

    @staticmethod
    def _fallback(
        paths: _KeyPaths,
        key: str,
        own: Path,
        version: str | None,
        typing: ValueTyping,
        *,
        check: bool,
    ) -> _Kept | None:
        """The newest value of the same type another version filled for ``key``, outdated.

        Stamps are read first; only the newest value is validated, and the
        next newest when it doesn't.
        """
        from untaped.sdk.storage.data_file import read_lines  # noqa: PLC0415 - first use

        candidates: list[tuple[datetime, Path, _Kept]] = []
        for entry in _listed(paths.dir):
            matched = _VERSIONED.fullmatch(entry.name)
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

    def _write(
        self,
        path: Path,
        key: str,
        freshness: Freshness,
        typing: ValueTyping | None,
        value: bytes | None,
    ) -> bool:
        """Write ``key``'s file; whether it was written (a failure is logged, never raised)."""
        try:
            self._write_file(path, key, freshness, typing, value)
        except OSError:
            # The next run refreshes again; the data the fill wrote is the owner's.
            _LOG.debug("could not write the cache file %s", path, exc_info=True)
            return False
        return True

    @staticmethod
    def _write_file(
        path: Path,
        key: str,
        freshness: Freshness,
        typing: ValueTyping | None,
        value: bytes | None,
    ) -> None:
        from untaped.sdk.storage.data_file import write_lines  # noqa: PLC0415 - first use

        error = freshness.error
        stamp: dict[str, Any] = {"key": key}
        if typing is not None:
            stamp["type"] = typing.shape
        stamp |= {
            "version": freshness.version,
            "refreshed_at": _iso(freshness.refreshed_at),
            "attempted_at": _iso(freshness.attempted_at),
            "error": None if error is None else error.model_dump(mode="json"),
            "partial": freshness.partial,
            "retry_at": _iso(freshness.retry_at),
        }
        write_lines(path, stamp, value)

    def _remember(
        self,
        own: Path,
        key: str,
        current: _Kept,
        attempt: Freshness,
        typing: ValueTyping | None,
    ) -> _Kept:
        """Keep a failed attempt in this version's file, with the value it falls back on, if any.

        The value's bytes are copied unparsed: from this version's file, or
        from another version's, which this version's file then serves,
        outdated, until a fill succeeds.
        """
        self._write(own, key, attempt, typing, current.raw)
        return replace(current, freshness=attempt)

    def _keep(
        self,
        own: Path,
        key: str,
        label: str,
        freshness: Freshness,
        typing: ValueTyping | None,
        value: bytes | None,
    ) -> bool:
        """Keep a fill's result; whether it did. A file that can't be written is one warning."""
        if typing is not None and value is None:
            return False  # a value that won't serialise: the older file stays, due, and is refilled
        try:
            self._write_file(own, key, freshness, typing, value)
        except OSError as exc:
            from untaped.runtime.sink import Diagnostics  # noqa: PLC0415 - no cycle

            Diagnostics().warning(
                f"could not keep {self.label} for {label} ({exc.strerror or exc}); "
                "the next run refreshes it again"
            )
            return False
        return True

    def _sweep(self, paths: _KeyPaths, key: str, own: Path, max_age: timedelta) -> None:
        """After a typed cache's complete fill, under the key's lock: drop what no reader serves.

        This key and partition's files of other versions go once past
        ``max_age``: whoever reads one refills it anyway. Another
        partition's files of the key (a rotated credential) go only past
        :meth:`_horizon`, under their own lock taken without waiting: a
        folder shared by profiles may serve one whose max age is longer
        than this one's.
        """
        now = _utcnow()
        for entry, stem, refreshed in self._key_files(paths.dir, key, _VERSIONED):
            if entry == own:
                continue
            if stem == paths.stem:
                if refreshed is None or now - refreshed >= max_age:
                    entry.unlink(missing_ok=True)
                continue
            self._sweep_other(paths.dir, entry, stem, now - self._horizon(), key, refreshed)

    def _sweep_siblings(self, paths: _KeyPaths, key: str) -> None:
        """After a complete fill, under the key's lock: the key in other settings' folders.

        Its files there (settings since changed, every partition and
        version) go past :meth:`_horizon`, under their own lock taken
        without waiting; a folder left with nothing but free locks is
        removed. Nothing under ``split="none"``, which has one folder.
        """
        if self.split == "none":
            return
        cutoff = _utcnow() - self._horizon()
        for sibling in _listed(paths.dir.parent):
            if sibling == paths.dir or not sibling.is_dir():
                continue
            for entry, stem, refreshed in self._key_files(sibling, key, _VERSIONED, _PLAIN):
                self._sweep_other(sibling, entry, stem, cutoff, key, refreshed)
            self._remove_if_unused(sibling)

    def _horizon(self) -> timedelta:
        """How long a key's files in another partition or folder stay: 30 days or 10 max ages."""
        return max(_PARTITION_HORIZON, 10 * (self.default_max_age or timedelta(0)))

    @staticmethod
    def _key_files(
        folder: Path, key: str, *names: re.Pattern[str]
    ) -> Iterator[tuple[Path, str, datetime | None]]:
        """``key``'s files in ``folder``: path, stem, refreshed.

        A name is read by the first of ``names`` whose stem is the key's
        (``x.vdeadbeef.json`` is key ``x``'s version, or key ``x.vdeadbeef``'s
        one file); the stamp says which key it holds.
        """
        segment = _segment(key)
        for entry in _listed(folder):
            stems = (each["stem"] for name in names if (each := name.fullmatch(entry.name)))
            stem = next(
                (each for each in stems if each == segment or each.startswith(f"{segment}.p")),
                None,
            )
            if stem is None:
                continue
            refreshed = _stamped(entry, key)
            if refreshed is not False:
                yield entry, stem, refreshed

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
        other = _KeyPaths(folder, stem, folder / f"{stem}.lock")
        try:
            with self._held(other, timeout=0.0, label=key):
                if _refreshed_before(entry, key, cutoff):
                    entry.unlink(missing_ok=True)
        except _LockError:
            pass  # busy, or its lock won't open: the next complete fill tries again

    def _remove_if_unused(self, folder: Path) -> None:
        """Remove ``folder`` when it holds only locks, each free; else leave it as it is."""
        entries = _listed(folder)
        if not entries or any(entry.suffix != ".lock" for entry in entries):
            return
        for lock in entries:
            stem = lock.name.removesuffix(".lock")
            try:
                with self._held(_KeyPaths(folder, stem, lock), timeout=0.0, label=stem):
                    lock.unlink(missing_ok=True)
            except _LockError:
                return  # a fill in progress: it keeps the folder
        # Only when empty: another process may have just written to it. One
        # creating its first lock here in the same instant fails that fill
        # ("could not lock"); the folder has seen no fill for the horizon.
        with contextlib.suppress(OSError):
            folder.rmdir()

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
    def _task_text(label: str, kept: _Kept, max_age: timedelta) -> str:
        """The refresh's task, worded from the key's age."""
        freshness = kept.freshness
        if freshness.refreshed_at is None:
            if kept.damaged:
                return f"Refreshing {label} (the kept copy could not be read)"
            return f"Refreshing {label} (first use)"
        if freshness.outdated:
            return f"Refreshing {label} (made by another version)"
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
        options: bool = True,
    ) -> StaleData:
        """The ``untaped.stale`` line for ``key`` served from ``freshness``.

        ``options``: the reader has ``--refresh``/``--cached`` to suggest (an
        answer cache's reader has none).
        """
        assert freshness.refreshed_at is not None
        hint = None
        if options and freshness.partial:
            hint = "pass --refresh to retry them"
        elif options and freshness.error is not None and freshness.attempted_at == started:
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
            outdated=freshness.outdated,
            hint=hint,
            cached=cached,
        )
        remembered = freshness.error is not None and freshness.attempted_at != started
        if options and remembered and not cached and not freshness.partial and freshness.retry_at:
            # an earlier run's failed attempt, inside its retry window
            line = line.model_copy(update={"message": f"{line.message}, or pass --refresh"})
        return line

    def _emit_stale(self, line: StaleData) -> None:
        """The one place a stale line leaves :meth:`serve`: deferred under a call state.

        Inside a provider call the line waits on the call state for whoever
        reads the answer (``gather``'s ``Answers``, ``select_one``);
        elsewhere it is said now.
        """
        call = _CALL.get()
        if call is None:
            current_sink().emit(line)
        else:
            call.stale_lines.append(line)

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
        retry_at=parse_utc(error.details.get("retry_at")) or started + RETRY_AFTER,
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


def _iso(value: datetime | None) -> str | None:
    return None if value is None else format_utc(value)


def value_typing(hint: Any, *, model: type[BaseModel] | None = None) -> ValueTyping:
    """How a cache keeps values of type ``hint``: its adapter and its shape's hash."""
    from untaped.sdk.storage.data_file import type_hash  # noqa: PLC0415 - first use

    adapter: TypeAdapter[Any] = TypeAdapter(hint)
    return ValueTyping(adapter, type_hash(adapter.json_schema()), model)


def _check_version_only(
    where: str,
    *,
    model: object,
    version: Callable[[], str] | None,
    renamed_from: Sequence[str],
    store: Store | None,
) -> None:
    """Refuse a version-only declaration without its model and version, or with a setting."""
    if model is None or version is None:
        raise ConfigError(
            f"cache {where}: max_age=None keeps a value refreshed by its version alone, "
            "so it takes model= and version="
        )
    if renamed_from:
        raise ConfigError(f"cache {where}: a version-only cache has no setting to rename")
    if store is not None:
        raise ConfigError(f"cache {where}: a version-only cache follows no store")


def versioned(typing: ValueTyping, version: str | None) -> str:
    """The hash naming one version's file of a value: its type's shape and ``version``."""
    return _digest(f"{typing.shape}:{version or ''}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


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


def _listed(folder: Path) -> list[Path]:
    """``folder``'s entries; none when it is missing or can't be listed."""
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _read_kept(
    found: object, key: str, version: str | None, typing: ValueTyping, *, check: bool
) -> _Kept | None:
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
        return _Kept(freshness)  # attempts only: no value yet
    if not found.holds:
        return None
    if not check:
        return _Kept(freshness, raw=found.value)
    if found.value is None:
        return None  # read without its value: nothing to check
    try:
        value = typing.adapter.validate_json(found.value, strict=True)
    except ValidationError:
        return None
    return _Kept(freshness, value=value, raw=found.value)


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


def outdated_keys(cache: Cache[Any]) -> int:
    """How many of ``cache``'s keys hold only another version's value: each refills when due.

    Read from each file's stamp, in every settings folder of the cache's
    root (a key counts once per partition and settings folder). A key with
    this version's value isn't counted, though another install's version of
    it may sit beside it; a file that isn't a cache file is skipped.
    """
    from untaped.sdk.storage.data_file import Lines, read_lines  # noqa: PLC0415 - doctor only

    typing = cache._typing()
    version = cache._version_now()
    root = cache._root(None if cache.store is None else cache.store.layout)
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
        named = _VERSIONED.fullmatch(entry.name) or _PLAIN.fullmatch(entry.name)
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


def _remove_legacy(plugin: str) -> None:
    """Remove ``plugin``'s ``caches/`` folder, untaped 11.0's, after a complete fill. Never raises.

    No release since reads it: each cache keeps its files under ``cached/``.
    Only a registered install removes it (``untaped setup register``), as
    :meth:`Store.prune <untaped.sdk.storage.store.Store.prune>` does: renamed aside, then
    deleted, with one ``untaped.pruned`` line, even under ``--quiet``.
    """
    from untaped.runtime.registry import named_plugin_dir  # noqa: PLC0415 - no cycle
    from untaped.sdk.storage import _refs as refs  # noqa: PLC0415 - refs imports the registry

    folder = named_plugin_dir(plugin) / "caches"
    try:
        if not folder.is_dir() or folder.is_symlink() or refs.this_install() is None:
            return
        aside = folder.with_name(f"caches.removing-{uuid.uuid4().hex[:8]}")
        os.rename(folder, aside)
    except (OSError, ConfigError) as exc:
        _LOG.debug("could not remove %s: %s", folder, exc)
        return
    from untaped.sdk.diagnostic import Pruned  # noqa: PLC0415 - no cycle
    from untaped.sdk.health.migrations import (  # noqa: PLC0415 - migrations imports the registry
        dir_bytes,
    )
    from untaped.sdk.words import shown_path, size_text  # noqa: PLC0415 - no cycle

    size = dir_bytes(aside)
    shutil.rmtree(aside, ignore_errors=True)
    line = Pruned(
        message=f"removed {shown_path(folder)} ({size_text(size)}): left by untaped 11.0, "
        "which kept caches there; this release keeps them in cached/",
        plugin=plugin,
        path=str(folder),
        bytes=size,
    )
    current_sink().emit(line, unmuted=True)


# --------------------------------------------------------------------------- #
# The version of the code that fills a cache
# --------------------------------------------------------------------------- #


def code_version(
    module: str, *, distributions: Sequence[str] = (), extra: Sequence[str] = ()
) -> str:
    """A version for ``Cache(version=)``: sha256 over a module, distributions and ``extra``.

    It hashes the bytes of ``module``'s file, the installed version of each
    of ``distributions`` (``"absent"`` when one is not installed) and each
    ``extra`` string, so editing the module or upgrading a library it relies
    on changes it. The module is found without being imported (its parent
    packages are); only that one file is read, so code it imports from
    elsewhere is a distribution to name. Computed once per process.
    """
    for name, value in (("distributions", distributions), ("extra", extra)):
        if isinstance(value, str):
            raise TypeError(f"code_version: {name} takes a sequence of strings, not one string")
    return _code_version(module, tuple(distributions), tuple(extra))


@functools.cache
def _code_version(module: str, distributions: tuple[str, ...], extra: tuple[str, ...]) -> str:
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:  # a parent package is missing
        spec = None
    origin = None if spec is None else spec.origin
    if origin is None or not Path(origin).is_file():
        raise ConfigError(f"code_version: module {module!r} has no source file to hash")
    digest = hashlib.sha256()
    parts = [b"module:" + Path(origin).read_bytes()]
    for distribution in distributions:
        try:
            installed = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            installed = "absent"
        parts.append(f"distribution:{distribution}=={installed}".encode())
    parts.extend(f"extra:{item}".encode() for item in extra)
    for part in parts:  # length-prefixed: no two lists of parts hash alike
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# The option and the status row
# --------------------------------------------------------------------------- #


def refresh_option(cache: Cache[Any]) -> Parameter:
    """The ``--refresh``/``--cached`` option for ``cache``'s directive; its help names the setting.

    Declare it as ``refresh: Annotated[bool | None, refresh_option(CACHE)] = None``
    and pass the value to :meth:`Cache.serve`.
    """
    if cache.version_only:
        raise ConfigError(
            f"cache {cache.where} is version-only: --refresh and --cached are about a max age"
        )
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

    ``stale``: past its max age, outdated, or with a remembered error;
    ``outdated``: filled by another version of the code that fills it.
    ``refresh_error`` is the last refresh's failure, or what a partial one
    left out; reporting it never fails the row (``error`` stays a failed
    row's).
    """

    refreshed_at: UtcTimestamp | None
    stale: bool
    max_age_seconds: int
    partial: bool = False
    outdated: bool = False
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
            "outdated": freshness.outdated,
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
    "code_version",
    "current_call",
    "installed_utcnow",
    "refresh_option",
    "refresh_scope",
]
