"""How a cache serves: decide per key whether to fill, fill under the key's lock, and answer.

The flow runs over a :class:`CacheFacts` (what it says about the cache) and a
:class:`~untaped.sdk.storage._cache_files.CacheFiles` (its files for this
call), never over the :class:`~untaped.sdk.storage.cache.Cache` itself, so
the contracts' answer cache serves through it with each binding's own typing.
It runs on every :meth:`Cache.serve <untaped.sdk.storage.cache.Cache.serve>`:
a stale answer is said once per key (``untaped.stale``), and waits on the call
state inside a provider call ``gather`` made (:mod:`._serve_scope`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, SkipValidation

from untaped.runtime.run_output import current_sink
from untaped.sdk.diagnostic import Failure, Stale, format_utc, parse_utc
from untaped.sdk.errors import ErrorCategory, UntapedError
from untaped.sdk.network.upstream import fail_fast, full_retries
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.records import UtcTimestamp
from untaped.sdk.storage._cache_files import (
    CacheFiles,
    Freshness,
    Kept,
    KeyPaths,
    ValueTyping,
    is_stale,
    utcnow,
)
from untaped.sdk.storage._serve_scope import _CALL, answering_for, refresh_scope
from untaped.sdk.words import duration_text

if TYPE_CHECKING:
    from pathlib import Path

    from untaped.sdk.storage.dir import Dir

_LOG = logging.getLogger("untaped.cache")

#: How long auto waits after a failed or interrupted refresh when upstream gave no time.
RETRY_AFTER: Final = timedelta(minutes=5)


# --------------------------------------------------------------------------- #
# What a fill returns, what serve() answers
# --------------------------------------------------------------------------- #


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


class _Named(Protocol):
    """What an error about a cache names: a :class:`Cache` or its :class:`CacheFacts`."""

    @property
    def label(self) -> str: ...
    @property
    def plugin(self) -> str: ...
    @property
    def qualified(self) -> str: ...
    @property
    def version_only(self) -> bool: ...


class NeverRefreshed(UntapedError):
    """``--cached`` asked for a key the cache never filled: nothing to serve (exit 1)."""

    category = ErrorCategory.NOT_FOUND

    def __init__(self, cache: _Named, key: str, label: str) -> None:
        # a version-only cache has no --cached: its owner asked to read without filling
        hint = "" if cache.version_only else "; drop --cached to refresh it"
        super().__init__(
            f"no cached {cache.label} for {label}{hint}",
            system=cache.plugin,
            details={"cache": cache.qualified, "key": key},
        )


@dataclass(frozen=True)
class CacheFacts:
    """What the serve flow says about a cache: its names, and what a complete fill prunes.

    ``qualified`` is ``<plugin>.<name>``, what a stale line's ``cache`` says;
    ``follows`` the numbered ``Dir`` whose unheld layouts a complete fill
    prunes (``None``: none).
    """

    qualified: str
    plugin: str
    label: str
    version_only: bool
    follows: Dir | None = None


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #


def serve_with(
    facts: CacheFacts,
    files: CacheFiles,
    keys: Mapping[str, str],
    *,
    refresh: bool | None,
    fill: Callable[[str, ProgressHandle], Filled[Any]],
    max_age: timedelta,
    read: Callable[[], Any] | None = None,
    partition: str | None = None,
    empty: Callable[[Any], bool] | None = None,
    progress: ProgressHandle = NO_PROGRESS,
) -> Served[Any, Any]:
    """:meth:`Cache.serve` over ``files``: a value of its typing, or ``read``'s answer.

    The answer cache serves through it with each provider's own type.
    """
    started = utcnow()
    if refresh is None:
        refresh = refresh_scope()
    paths = {key: files.paths(key, partition) for key in keys}
    kept = {key: files.load(paths[key], key) for key in keys}
    filled: dict[str, Filled[Any]] = {}
    for key, label in keys.items():
        if kept[key].freshness.due(refresh, max_age=max_age, now=started):
            kept[key] = _refresh(
                facts,
                files,
                key,
                label,
                paths[key],
                kept[key],
                filled,
                fill=fill,
                refresh=refresh,
                started=started,
                max_age=max_age,
                progress=progress,
            )
    if files.typing is None:
        assert read is not None
        answer = read
    else:
        values = MappingProxyType({key: entry.value for key, entry in kept.items()})
        answer = lambda: values  # noqa: E731 - the kept values, read once already
    return _answer(
        facts,
        keys,
        answer,
        {key: entry.freshness for key, entry in kept.items()},
        filled,
        refresh=refresh,
        started=started,
        empty=empty,
        max_age=max_age,
    )


def serve_version(
    facts: CacheFacts,
    files: CacheFiles,
    keys: Mapping[str, str],
    *,
    refresh: bool | None,
    fill: Callable[[str, ProgressHandle], Filled[Any]],
    partition: str | None,
    progress: ProgressHandle,
) -> Served[Any, Any]:
    """:meth:`Cache.serve` for a version-only cache: this version's value, else a fill, quietly.

    No task, lock or stale line: two processes filling at once compute the
    same value and each writes it whole. ``--cached`` on a key this
    version never filled raises :class:`NeverRefreshed`; a fill that
    raises propagates, since no other version's value may stand in.
    """
    values: dict[str, Any] = {}
    freshness: dict[str, Freshness] = {}
    filled: dict[str, Filled[Any]] = {}
    for key, label in keys.items():
        paths = files.paths(key, partition)
        kept = None if refresh else files.own_version(paths, key)
        if kept is None:
            if refresh is False:
                raise NeverRefreshed(facts, key, label)
            result = filled[key] = fill(key, progress)
            kept = _keep_version(facts, files, paths, key, result)
        values[key], freshness[key] = kept.value, kept.freshness
    return Served(
        value=MappingProxyType(values),
        freshness=MappingProxyType(freshness),
        filled=MappingProxyType(filled),
    )


def _refresh(
    facts: CacheFacts,
    files: CacheFiles,
    key: str,
    label: str,
    paths: KeyPaths,
    kept: Kept,
    filled: dict[str, Filled[Any]],
    *,
    fill: Callable[[str, ProgressHandle], Filled[Any]],
    refresh: bool | None,
    started: datetime,
    max_age: timedelta,
    progress: ProgressHandle,
) -> Kept:
    """Fill ``key`` under its task and lock; what the cache keeps of it afterwards.

    Raises when the fill fails with nothing cached to fall back to, when
    the lock never comes, and on Ctrl-C.
    """
    failure: BaseException | None = None
    own = files.own(paths)
    try:
        text = _task_text(label, kept, max_age)
        with (
            _task(text, progress) as task,
            # back to the parent's phase after a wait; an own task has none
            files.locked(paths, label, task, resume=None if progress is NO_PROGRESS else text),
        ):
            read_before = kept.freshness.refreshed_at
            current = kept = files.load(paths, key)
            refreshed_at = current.freshness.refreshed_at
            # another version's typed value is a fallback, never an answer: fill this one's
            fallback = files.typing is not None and current.freshness.outdated
            # another run refreshed it after this one started, or (on a stopped clock, a
            # test's) since this one read it: an earlier run's fill is never that
            moved = refreshed_at is not None and (
                refreshed_at > started or refreshed_at != read_before
            )
            if moved and not fallback:
                return current  # refreshed while this run waited: serve it, under --refresh too
            due = current.freshness.due(None, max_age=max_age, now=utcnow())
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
                kept = files.remember(own, key, current, attempt)
                raise
            except KeyboardInterrupt as exc:
                failure = exc
                attempt = _interrupted(current.freshness, exc, label, started)
                kept = files.remember(own, key, current, attempt)
                raise
            filled[key] = result
            if progress is NO_PROGRESS:
                task.update(result.summary)  # the done line carries it
            saved, kept_it = _filled(facts, files, paths, own, key, label, result, max_age)
        if saved.error is None and kept_it and (layouts := facts.follows):
            # a complete fill: this layout has data, so the ones nothing holds may go
            layouts.prune()
        return Kept(saved, value=None if files.typing is None else result.value)
    except UntapedError as exc:
        # the task closed `failed` on the way out, before the warning _answer emits
        if exc is not failure or kept.freshness.refreshed_at is None:
            raise  # a lock that never came, or nothing cached to fall back to
        return kept
    except KeyboardInterrupt as exc:
        if exc is failure and kept.freshness.error is not None:
            _say_interrupted(facts, kept.freshness)
        raise


def _filled(
    facts: CacheFacts,
    files: CacheFiles,
    paths: KeyPaths,
    own: Path,
    key: str,
    label: str,
    result: Filled[Any],
    max_age: timedelta,
) -> tuple[Freshness, bool]:
    """Keep what a fill returned, under the key's lock: the key's freshness, and if kept.

    Only a value kept lets older files go: until then they are what a
    later read falls back on.
    """
    value = filled_value(facts, result, files.typing)
    saved = Freshness(
        refreshed_at=utcnow(),
        error=result.partial,
        partial=result.partial is not None,
        retry_at=result.retry_at,
        version=files.version,
    )
    kept_it = _keep(facts, files, own, key, label, saved, value)
    if kept_it and saved.error is None:
        if files.typing is not None:
            files.sweep(paths, key, own, max_age)
        files.sweep_siblings(paths, key)
    return saved, kept_it


def _keep(
    facts: CacheFacts,
    files: CacheFiles,
    own: Path,
    key: str,
    label: str,
    freshness: Freshness,
    value: bytes | None,
) -> bool:
    """Keep a fill's result; whether it did. A file that can't be written is one warning."""
    if files.typing is not None and value is None:
        return False  # a value that won't serialise: the older file stays, due, and is refilled
    try:
        files.write_file(own, key, freshness, value)
    except OSError as exc:
        from untaped.runtime.run_output import Diagnostics  # noqa: PLC0415 - no cycle

        Diagnostics().warning(
            f"could not keep {facts.label} for {label} ({exc.strerror or exc}); "
            "the next run refreshes it again"
        )
        return False
    return True


def _keep_version(
    facts: CacheFacts, files: CacheFiles, paths: KeyPaths, key: str, result: Filled[Any]
) -> Kept:
    """Keep a version-only fill, then drop the key's other versions; the value either way.

    A partial fill, a value that won't serialise or a file that can't be
    written is served from memory, and the next run fills again. Another
    partition's value of the key stays: it is that partition's.
    """
    own = files.own(paths)
    saved = Freshness(
        refreshed_at=utcnow(),
        error=result.partial,
        partial=result.partial is not None,
        version=files.version,
    )
    value = filled_value(facts, result, files.typing)
    if result.partial is None and value is not None and files.write(own, key, saved, value):
        files.drop_other_versions(paths, key, own)
    return Kept(saved, value=result.value)


def filled_value(
    facts: CacheFacts, result: Filled[Any], typing: ValueTyping | None
) -> bytes | None:
    """The bytes of the value ``result`` carries; refused when the cache keeps none, or does."""
    if typing is None:
        if result.value is not None:
            raise TypeError(
                f"a fill of {facts.qualified} returned a value, which a cache without "
                "model= never keeps"
            )
        return None
    if "value" not in result.model_fields_set:
        raise TypeError(
            f"a fill of {facts.qualified} returned no value: Filled(value=...) carries "
            "what a cache with model= keeps"
        )
    if typing.model is not None and not isinstance(result.value, typing.model):
        raise TypeError(
            f"a fill of {facts.qualified} returned a {type(result.value).__name__}, "
            f"not the {typing.model.__name__} its model= names"
        )
    try:
        return typing.adapter.dump_json(
            result.value, by_alias=True, round_trip=True, warnings=False
        )
    except Exception:  # the live answer still stands; the next run fills again
        _LOG.debug("could not serialise a value of %s", facts.qualified, exc_info=True)
        return None


def _say_interrupted(facts: CacheFacts, freshness: Freshness) -> None:
    """Say the Ctrl-C a fill stopped on: nothing else prints it, the CLI exits 130 bare."""
    assert freshness.error is not None
    error = freshness.error
    # else the next run fills again; the answer cache's reader has no --cached
    if freshness.refreshed_at is not None and answering_for(facts.qualified) is None:
        hint = "pass --cached to read the cached data without checking"
        error = error.model_copy(update={"hint": hint})
    current_sink().emit(error)


def _answer[T, F: Filled[Any]](
    facts: CacheFacts,
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
    """Read the answer once over every key, then say each key served stale."""
    for key, state in freshness.items():
        if state.refreshed_at is None:  # only under --cached: anything else filled it
            raise NeverRefreshed(facts, key, keys[key])
    value = read()  # once, over every key, outside any lock
    now = utcnow()
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
        raise _raised(facts, key, state, hint=hint)
    provider = answering_for(facts.qualified)
    for key, state in freshness.items():
        if key in filled and not state.partial:
            continue  # whole and fresh from this run
        if (
            state.past_max_age(max_age, now=now)
            or state.outdated
            or state.partial
            or state.attempted_at == started
        ):
            line = _stale(
                facts,
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
            _emit_stale(line)
    return Served(
        value=value,
        freshness=MappingProxyType(dict(freshness)),
        filled=MappingProxyType(dict(filled)),
        stale=any(is_stale(state, max_age, now) for state in freshness.values()),
    )


# --------------------------------------------------------------------------- #
# What a refresh shows, and what a stale answer says
# --------------------------------------------------------------------------- #


@contextmanager
def _task(text: str, parent: ProgressHandle) -> Iterator[ProgressHandle]:
    """The refresh's task; a phase of ``parent`` when one is passed."""
    if parent is not NO_PROGRESS:
        parent.update(phase=text)
        yield parent
        return
    with current_sink().progress(text) as task:
        yield task


def _task_text(label: str, kept: Kept, max_age: timedelta) -> str:
    """The refresh's task, worded from the key's age."""
    freshness = kept.freshness
    if freshness.refreshed_at is None:
        if kept.damaged:
            return f"Refreshing {label} (the kept copy could not be read)"
        return f"Refreshing {label} (first use)"
    if freshness.outdated:
        return f"Refreshing {label} (made by another version)"
    age = duration_text((utcnow() - freshness.refreshed_at).total_seconds())
    limit = duration_text(max_age.total_seconds())
    return f"Refreshing {label} ({age} old, max age {limit})"


def _stale(
    facts: CacheFacts,
    key: str,
    label: str,
    freshness: Freshness,
    *,
    now: datetime,
    max_age: timedelta,
    started: datetime | None = None,
    cached: bool = False,
    plugin: str | None = None,
    options: bool = True,
) -> Stale:
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
    line = Stale.describe(
        plugin=facts.plugin if plugin is None else plugin,
        subject=label,
        cache=facts.qualified,
        key=key,
        refreshed_at=freshness.refreshed_at,
        now=now,
        max_age=max_age,
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


def _emit_stale(line: Stale) -> None:
    """The one place a stale line leaves the serve flow: deferred under a call state.

    Inside a provider call the line waits on the call state for whoever
    reads the answer (``gather``'s ``Answers``, ``select_one``);
    elsewhere it is said now.
    """
    call = _CALL.get()
    if call is None:
        current_sink().emit(line)
    else:
        call.stale_lines.append(line)


def _raised(facts: CacheFacts, key: str, freshness: Freshness, *, hint: str | None) -> UntapedError:
    """``freshness.error`` as the error to raise, naming the cache and the key."""
    assert freshness.error is not None
    error = freshness.error.to_exception()
    details: dict[str, object] = {**error.details, "cache": facts.qualified, "key": key}
    if freshness.refreshed_at is not None:
        details["refreshed_at"] = format_utc(freshness.refreshed_at)
    error.details = details
    if error.hint is None:
        error.hint = hint
    return error


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
