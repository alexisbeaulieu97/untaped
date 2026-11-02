"""Caches: data a plugin keeps from an upstream it can ask again, checked past its max age.

A plugin class holds one :class:`Cache` per thing it keeps
(``index = Cache(label="source data", max_age=timedelta(days=1))``)
and reads it through :meth:`Cache.serve`, which decides per key whether to
refresh (the *directive*), runs the plugin's ``fill`` when it must, reads the
command's answer once over every key, and says on stderr when that answer
comes from stale data (one ``untaped.stale`` warning per key), so no caller
words or forgets it. Inside a provider call ``gather`` made, the warning
waits for whoever uses the provider's answer (docs/contracts.md).

The directive is ``refresh``: ``None`` (*auto*) refreshes a key past its max
age, failing fast when upstream is unreachable (:func:`untaped.sdk.network.upstream.fail_fast`);
``True`` checks upstream now with full retries; ``False`` reads what is cached
and never checks. A command's ``Refresh`` field is the ``--refresh``/``--cached``
option that sets it; ``gather`` opens one for every provider call it makes
(:func:`~untaped.sdk.storage._serve_scope.call_state`), which ``serve()`` reads
when its own is ``None``.

Layout: each key is one file under ``<plugin dir>/cached/<name>/<settings digest>/``
(``cached/<name>.v<layout>/`` when the cache follows a numbered ``Dir``; no digest
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
names that code's version (:func:`~untaped.sdk.storage.installed_code.installed_code`
hashes the modules it names): each key's file keeps the version it was
filled at, and a key filled at another one is *outdated*, due and stale the
way a key past its max age is.

A cache declared with ``max_age=None`` is *version-only*: its value depends on
the code that fills it and on nothing upstream, so it is refreshed only when
that code's version changes. It takes ``model=``, ``version=`` and
``split="none"``. It has no max-age setting, never serves another
version's value, drops the key's other versions once it fills this one, and
says nothing on stderr: a home it cannot write leaves the value in memory.

:class:`Cache` is the declaration and its API. Its parts take what it
resolved, never the cache: :mod:`._cache_files` is one cache's files for one
call, :mod:`._cache_serve` the serve flow, and :mod:`._serve_scope` the
directive ``gather`` opens around a provider call.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, Literal, overload

from pydantic import BaseModel

from untaped.runtime.settings import load_settings_section, registered_profile_model
from untaped.sdk._block import OwnedBlock
from untaped.sdk.diagnostic import NOT_COUNTED, Failure, parse_utc
from untaped.sdk.errors import ConfigError
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.records import Record, UtcTimestamp
from untaped.sdk.storage import _cache_files
from untaped.sdk.storage._cache_files import (
    PARTITION_HORIZON,
    CacheFiles,
    Freshness,
    LockError,
    ValueTyping,
    is_stale,
    utcnow,
    value_typing,
)
from untaped.sdk.storage._cache_serve import (
    RETRY_AFTER,
    CacheFacts,
    Filled,
    NeverRefreshed,
    Served,
    serve_version,
    serve_with,
)
from untaped.sdk.words import duration_text

if TYPE_CHECKING:
    from untaped.sdk.storage.dir import Dir

_NAME = re.compile(r"[a-z][a-z0-9_]*")


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


class Cache[M = None](OwnedBlock):
    """Data a plugin keeps from an upstream, refreshed past ``max_age``.

    A plugin class holding it names it (``motd = Cache(label=..., max_age=...)`` is the
    plugin's cache ``motd``). The name is one settings segment (``[a-z][a-z0-9_]*``);
    ``label`` names the data for a human (``source data``). ``max_age`` is the declared
    default, in whole seconds (``0``: check every time); its setting
    ``<plugin>.caches.<name>.max_age_seconds`` overrides it. ``max_age=None`` declares a
    version-only cache instead: a value that depends only on the code computing it, with
    ``model=``, ``version=`` and ``split="none"``, filled once per version, with no setting
    (see the module docstring). ``renamed_from`` names old keys of the plugin's section
    (dotted, relative to it) that are still read as that setting, with a warning, until the
    plugin's major ``removed_in`` (undated without it). ``version`` returns the version of
    the code that fills the cache (usually :func:`installed_code`); it is called each time the
    cache is read (once per :meth:`serve`), never at import, and a key filled at another
    version is refreshed like an expired one. ``follows`` ties the cache's files to a
    numbered :class:`~untaped.sdk.storage.dir.Dir` of the same plugin class
    (``Cache(follows=index)``): they live under ``cached/<name>.v<layout>/``, so a key is
    never fresh over another layout's file, they move with the place's layouts, and a
    complete fill (no error, not partial) ends with :meth:`Dir.prune()
    <untaped.sdk.storage.dir.Dir.prune>`.

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
    """

    block_kind = "cache"

    label: str
    #: ``None`` for a version-only cache, which has no max age.
    default_max_age: timedelta | None
    renamed_from: tuple[str, ...]
    removed_in: int | None
    version: Callable[[], str] | None
    follows: Dir | None
    model: type[M] | Callable[[], type[M]] | None
    split: Literal["settings", "none"]

    @overload
    def __init__(
        self: Cache[None],
        *,
        label: str,
        max_age: timedelta | None,
        renamed_from: Sequence[str] = (),
        removed_in: int | None = None,
        version: Callable[[], str] | None = None,
        follows: Dir | None = None,
        split: Literal["settings", "none"] = "settings",
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        label: str,
        max_age: timedelta | None,
        renamed_from: Sequence[str] = (),
        removed_in: int | None = None,
        version: Callable[[], str] | None = None,
        follows: Dir | None = None,
        model: type[M] | Callable[[], type[M]],
        split: Literal["settings", "none"] = "settings",
    ) -> None: ...

    def __init__(
        self,
        *,
        label: str,
        max_age: timedelta | None,
        renamed_from: Sequence[str] = (),
        removed_in: int | None = None,
        version: Callable[[], str] | None = None,
        follows: Dir | None = None,
        model: type[M] | Callable[[], type[M]] | None = None,
        split: Literal["settings", "none"] = "settings",
    ) -> None:
        from untaped.sdk.marks import check_removed_in  # noqa: PLC0415 - no cycle

        super().__init__()
        where = self.where
        if max_age is None:
            _check_version_only(
                where,
                model=model,
                version=version,
                renamed_from=renamed_from,
                follows=follows,
                split=split,
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
        _check_follows(where, follows)
        if model is not None and not callable(model):
            raise ConfigError(
                f"cache {where}: model takes a model class or a function returning one"
            )
        if split not in ("settings", "none"):
            raise ConfigError(f"cache {where}: split is 'settings' or 'none', not {split!r}")
        self.model = model
        self.split = split
        self._resolved: ValueTyping | None = None
        self.follows = follows
        if follows is not None:
            follows._follow(self)  # its folder moves with the place's layouts

    def problems(self) -> tuple[str, ...]:
        if self.follows is None:
            return ()
        owners = {binding.owner for binding in self.bindings}
        if owners and not owners & {binding.owner for binding in self.follows.bindings}:
            return (
                "a cache follows a Dir its own plugin class holds, not "
                f"{self.follows!r} of another class",
            )
        return ()

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if _NAME.fullmatch(name):
            return None
        return f"cache name {name!r} must be one settings segment (pattern {_NAME.pattern})"

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
        """The setting that tunes the max age: ``<plugin>.caches.<name>.max_age_seconds``.

        A version-only cache has none and raises :class:`ConfigError`.
        """
        self._declared_max_age()
        return _max_age_setting(self.plugin, self.name)

    @property
    def version_only(self) -> bool:
        """Whether the cache was declared with ``max_age=None``: refreshed by its version alone."""
        return self.default_max_age is None

    @property
    def default_max_age_text(self) -> str:
        """The declared max age for a human: ``24 h``."""
        return duration_text(self._declared_max_age().total_seconds())

    def _declared_max_age(self) -> timedelta:
        """The declared max age; a version-only cache has none and raises :class:`ConfigError`."""
        if self.default_max_age is None:
            raise ConfigError(f"cache {self.where} is version-only: it has no max age")
        return self.default_max_age

    def max_age(self) -> timedelta:
        """The max age in force now: the setting, read at call time, else the declared default.

        The default holds when the plugin's composed section has no such
        cache (the plugin is used uncomposed, or its class does not hold the
        cache). An invalid setting raises the settings
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
        none, the error is raised. A version-only cache (``max_age=None``)
        serves only this version's value and raises a failed fill.
        ``--cached`` on a key never filled raises :class:`NeverRefreshed`. A
        Ctrl-C during a fill is remembered like a failed attempt and said as
        one ``error:`` line before it propagates.
        """
        typing = self._typing()
        if typing is None and read is None:
            raise ConfigError(f"cache {self.qualified}: serve() needs read= without model=")
        if typing is not None and read is not None:
            raise ConfigError(
                f"cache {self.qualified}: a cache with model= answers its kept values; "
                "serve() takes no read="
            )
        max_age = None if self.version_only else self.max_age()
        files = self._files(typing)
        if max_age is None:
            assert typing is not None  # a version-only cache is declared with model=
            return serve_version(
                self._facts(),
                files,
                keys,
                refresh=refresh,
                fill=fill,
                partition=partition,
                progress=progress,
            )
        return serve_with(
            self._facts(),
            files,
            keys,
            refresh=refresh,
            fill=fill,
            max_age=max_age,
            read=read,
            partition=partition,
            empty=empty,
            progress=progress,
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
        files = self._files(typing)
        paths = files.paths(key, partition)
        if self.version_only:
            own = files.own_version(paths, key)
            if own is None:
                return None
            return Served(value=own.value, freshness=MappingProxyType({key: own.freshness}))
        kept = files.load(paths, key)
        state = kept.freshness
        if state.refreshed_at is None:
            return None
        stale = is_stale(state, self.max_age(), utcnow())
        value = kept.value if read is None else read()
        return Served(value=value, freshness=MappingProxyType({key: state}), stale=stale)

    def freshness(self, key: str, *, partition: str | None = None) -> Freshness:
        """What the cache knows about ``key`` now, without a lock (a status row).

        Finding the folder loads the plugin's settings, so an invalid section
        raises its :class:`ConfigError`, as :meth:`max_age` does.

        Reads each file's first line only: a value that no longer reads is
        found by :meth:`serve`, which refills it.
        """
        files = self._files()
        paths = files.paths(key, partition)
        if self.version_only:
            own = files.own_version(paths, key)
            return Freshness() if own is None else own.freshness
        return files.load(paths, key, check=False).freshness

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
        files = self._files_at(None, None)
        paths = files.paths(key)
        if paths.file().exists():
            return False
        try:
            with files.held(paths, timeout=0.0, label=key):
                if paths.file().exists():
                    return False
                return files.write(
                    paths.file(), key, Freshness(refreshed_at=parse_utc(refreshed_at)), None
                )
        except LockError as exc:
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
        files = self._files_at(None, None)
        files.forget(files.paths(key, partition), key)

    # ---- what the parts take ------------------------------------------------

    def _facts(self) -> CacheFacts:
        """What the serve flow says about this cache: built per call, once it is named."""
        return CacheFacts(
            qualified=self.qualified,
            plugin=self.plugin,
            label=self.label,
            version_only=self.version_only,
            follows=self.follows,
        )

    def _files(self, typing: ValueTyping | None = None) -> CacheFiles:
        """This cache's files for one call, its values kept as ``typing`` (default: its own).

        The answer cache passes each provider's type; the version is read
        now, before the folder (which reads the plugin's settings).
        """
        version = self._version_now()
        return self._files_at(version, self._typing() if typing is None else typing)

    def _files_at(self, version: str | None, typing: ValueTyping | None) -> CacheFiles:
        """This cache's files at ``version``, kept as ``typing``: neither is resolved here.

        ``seed`` and ``forget`` take neither (a key's paths need no version),
        so a cache whose version or model can't be resolved is still forgotten.
        """
        horizon = (
            None if self.version_only else max(PARTITION_HORIZON, 10 * self._declared_max_age())
        )
        return CacheFiles(
            qualified=self.qualified,
            folder=self._folder(),
            typing=typing,
            version=version,
            horizon=horizon,
            split=self.split,
        )

    # ---- the seams ----------------------------------------------------------

    @property
    def _layout(self) -> int | None:
        """The layout the cache's files follow: its place's, else none."""
        return None if self.follows is None else self.follows.layout

    def _root(self, layout: int | None = None) -> Path:
        """``plugins/<plugin>/cached/<name>``, or ``<name>.v<layout>`` for its place's layout."""
        from untaped.runtime.registry import named_plugin_dir  # noqa: PLC0415 - no cycle

        folder = self.name if layout is None else f"{self.name}.v{layout}"
        return named_plugin_dir(self.plugin) / "cached" / folder

    def _folder(self) -> Path:
        """The folder this cache's files are in for the active settings: computed here only.

        Under ``split="settings"``, the root's folder named by the plugin's
        settings digest (:mod:`untaped.sdk.storage._digest`).
        """
        root = self._root(self._layout)
        if self.split == "none":
            return root
        from untaped.sdk.storage._digest import settings_digest  # noqa: PLC0415 - no cycle

        return root / settings_digest(self.plugin)


def _check_follows(where: str, follows: object) -> None:
    """Refuse a ``follows=`` the cache cannot follow the layouts of."""
    from untaped.sdk.storage.dir import Dir  # noqa: PLC0415 - dir imports nothing of caches

    if follows is not None and (not isinstance(follows, Dir) or follows.layout is None):
        raise ConfigError(f"cache {where}: follows takes a numbered Dir (layout=), not {follows!r}")


def _check_version_only(
    where: str,
    *,
    model: object,
    version: Callable[[], str] | None,
    renamed_from: Sequence[str],
    follows: object,
    split: str,
) -> None:
    """Refuse a version-only declaration without its model and version, or with a setting."""
    if model is None or version is None:
        raise ConfigError(
            f"cache {where}: max_age=None keeps a value refreshed by its version alone, "
            "so it takes model= and version="
        )
    if renamed_from:
        raise ConfigError(f"cache {where}: a version-only cache has no setting to rename")
    if follows is not None:
        raise ConfigError(f"cache {where}: a version-only cache follows no place")
    if split != "none":
        raise ConfigError(
            f'cache {where}: a version-only cache depends on no setting, so it is split="none"'
        )


def outdated_keys(cache: Cache[Any]) -> int:
    """How many of ``cache``'s keys hold only another version's value: each refills when due.

    Read from each file's stamp, in every settings folder of the cache's root
    (:func:`untaped.sdk.storage._cache_files.outdated_keys`).
    """
    root = cache._root(cache._layout)
    return _cache_files.outdated_keys(root, cache._typing(), cache._version_now())


# --------------------------------------------------------------------------- #
# The option and the status row
# --------------------------------------------------------------------------- #


def _max_age_setting(plugin: str, name: str) -> str:
    """The setting that tunes a cache's max age: ``<plugin>.caches.<name>.max_age_seconds``."""
    return f"{plugin}.caches.{name}.max_age_seconds"


def _refresh_help(caches: Sequence[tuple[Cache[Any], str]]) -> str:
    """The ``--refresh``/``--cached`` help about ``caches``, each with its max-age setting.

    Composition writes it into a ``Refresh`` field's declaration, which help
    reads without importing the command. None of ``caches`` is version-only.
    """
    named = list({setting: cache for cache, setting in caches}.items())
    labels = _and(list(dict.fromkeys(cache.label for _, cache in named)))  # each once, in order
    if len(named) == 1:
        setting, cache = named[0]
        default = f"older than {setting} ({cache.default_max_age_text})"
    else:
        # a label two caches share would name neither: the settings already do
        shared = {label for label, count in Counter(c.label for _, c in named).items() if count > 1}
        default = "older than " + _and(
            [
                f"{each} ({cache.default_max_age_text})"
                + ("" if cache.label in shared else f" for {cache.label}")
                for each, cache in named
            ]
        )
    return (
        f"--refresh checks upstream for {labels} now; --cached reads what is cached "
        f"and never checks. Default: check when the cached data is {default}."
    )


def _and(items: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


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
            "stale": is_stale(freshness, max_age, now),
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
    "Filled",
    "Freshness",
    "FreshnessRecord",
    "NeverRefreshed",
    "Served",
    "ValueTyping",
    "value_typing",
]
