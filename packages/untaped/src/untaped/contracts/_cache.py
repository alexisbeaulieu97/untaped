"""The answer cache behind ``@cached``: providers' answers kept in the owner's :class:`Cache`.

One entry per provider, method, profile and arguments, keyed ``<provider>:<hash>``
(``<hash>`` is the sha256 of the arguments' canonical JSON): the cache is the
owner's and the entry one provider's, so whatever reports an entry (a stale
line) names whose it is. The entry, ``<segment>.answer.json`` beside the key's
sidecar under the owner's ``caches/<contract>_<method>/<profile>/``, holds
``{"untaped": "2", "schema": …, "value": …}``; how fresh it is lives in the
sidecar. ``schema`` is the sha256 of the return type's JSON schema (with the
provider's own record model's, which ``source.record`` carries), so an entry
written for another shape of either model is a miss, never an error. A cache
that can't be written never costs the live answer.

:meth:`Cache.serve` decides when to call the provider, from the ``refresh``
directive ``gather`` opens (``untaped.cache.call_state``), and holds the key's
lock across the call. ``gather`` reads back what the call served: the oldest
``refreshed_at`` and, for a stale answer, why (the live call's error, or the
one an earlier attempt left). The ``untaped.stale`` line waits on the call
state for whoever reads the answer; a provider called outside ``gather`` says
it at the call.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin

from pydantic import TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from untaped.cache import (
    Cache,
    CallState,
    Filled,
    Freshness,
    answering,
    call_state,
    current_call,
)
from untaped.contracts._declare import Binding, Contract, binding_of
from untaped.errors import UntapedError
from untaped.fs import atomic_write
from untaped.output.progress import ProgressHandle
from untaped.output.sink import current_sink

_FORMAT = "2"
_LOG = logging.getLogger("untaped.contracts")
#: The entries this context is filling, as ``(cache, key)``.
_FILLING: ContextVar[frozenset[tuple[str, str]]] = ContextVar(
    "untaped_answer_filling", default=frozenset()
)


def return_type(binding: Binding, name: str) -> Any:
    """The contract method's return type with the provider's ``T`` put in for the parameter."""
    hint = binding.contract.methods[name].hints["return"]
    return substitute(hint, binding.contract.item_param, binding.item)


def substitute(hint: Any, param: TypeVar | None, item: type | None) -> Any:
    """``hint`` with ``item`` put in for the contract's type parameter (``T``, ``list[T]``)."""
    if param is None or item is None:
        return hint
    if hint is param:
        return item
    origin, args = get_origin(hint), get_args(hint)
    if origin is list and args == (param,):
        return list[item]  # type: ignore[valid-type]  # a runtime class
    return hint


@functools.cache
def _adapter(hint: Any) -> tuple[TypeAdapter[Any], str]:
    adapter: TypeAdapter[Any] = TypeAdapter(hint)
    schema = json.dumps(adapter.json_schema(), sort_keys=True, separators=(",", ":"))
    return adapter, hashlib.sha256(schema.encode()).hexdigest()


def _schema(binding: Binding, hint: Any) -> str:
    """The entry's schema hash: the return type's, and the provider's own model's when bridged."""
    schema = _adapter(hint)[1]
    if binding.owns_item or binding.item is None:
        return schema
    return hashlib.sha256(f"{schema}:{_adapter(binding.item)[1]}".encode()).hexdigest()


def entry_key(binding: Binding, digest: str) -> str:
    """The entry's key in the owner's cache: ``<provider>:<arguments hash>``."""
    return f"{binding.plugin}:{digest}"


def entry_path(cache: Cache, key: str) -> Path:
    """Where ``key``'s answer lives in the active profile: beside its sidecar."""
    sidecar = cache._paths(key).sidecar
    return sidecar.with_name(f"{sidecar.stem}.answer.json")


def argument_key(function: Callable[..., Any], provider: object, args: Any, kwargs: Any) -> str:
    """sha256 of the call's arguments as canonical JSON (defaults applied)."""
    bound = inspect.signature(function).bind(provider, *args, **kwargs)
    bound.apply_defaults()
    arguments = dict(list(bound.arguments.items())[1:])
    canonical = json.dumps(
        to_jsonable_python(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class _Entry:
    value: Any


def _read(path: Path, adapter: TypeAdapter[Any], schema: str) -> _Entry | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("untaped") != _FORMAT or raw.get("schema") != schema:
            return None
        value = adapter.validate_json(json.dumps(raw["value"]), strict=True)
    except OSError, ValueError, KeyError, TypeError, AttributeError, ValidationError:
        return None
    return _Entry(value)


def _write(path: Path, adapter: TypeAdapter[Any], schema: str, value: Any) -> None:
    """Write ``value`` as the entry; the caller holds the key's lock (inside ``fill``)."""
    try:
        dumped = adapter.dump_python(
            value, mode="json", by_alias=True, round_trip=True, warnings=False
        )
    except Exception:
        _LOG.debug("could not serialise the answer cache entry %s", path, exc_info=True)
        return
    text = json.dumps({"untaped": _FORMAT, "schema": schema, "value": dumped}, ensure_ascii=False)
    try:
        atomic_write(path, text)
    except OSError:
        _LOG.debug("could not write the answer cache entry %s", path, exc_info=True)


class _ProviderBug(UntapedError):
    """A provider's own exception, carried through ``serve()`` (which falls back on these)."""

    def __init__(self, bug: Exception, plugin: str) -> None:
        super().__init__(str(bug) or type(bug).__name__, system=plugin)
        self.bug = bug


@contextmanager
def _answering(provider: str, cache: Cache) -> Iterator[CallState]:
    """The call state ``serve()`` defers to while answering for ``provider`` from ``cache``.

    ``gather``'s when it opened one; otherwise one of this call's own, whose
    stale lines are said when the call returns.
    """
    outer = current_call()
    if outer is not None:
        with answering(cache, provider):
            yield outer
        return
    with call_state(None) as state, answering(cache, provider):
        yield state
    for line in state.stale_lines:
        current_sink().emit(line)


@dataclass
class _Call:
    """One provider call through the answer cache: what ``serve()`` fills and reads."""

    live_call: Callable[[], Any]
    cache: Cache
    key: str
    plugin: str
    path: Path
    adapter: TypeAdapter[Any]
    schema: str
    stored: _Entry | None = None
    known: datetime | None = None
    live: list[Any] = field(default_factory=list)
    failure: BaseException | None = None

    def load(self) -> None:
        """Read the stored entry; a miss the sidecar calls refreshed is forgotten, so it fills."""
        self.stored = _read(self.path, self.adapter, self.schema)
        self.known = self.cache.freshness(self.key).refreshed_at
        if self.stored is None and self.known is not None:
            # a schema change, an older format or a lost entry: what the sidecar says is wrong
            self.cache.forget(self.key)
            self.known = None

    def fill(self, _key: str, _task: ProgressHandle) -> Filled:
        token = _FILLING.set(_FILLING.get() | {(self.cache.qualified, self.key)})
        try:
            value = self.live_call()
        except UntapedError as exc:
            self.failure = exc
            raise
        except Exception as exc:
            self.failure = exc
            raise _ProviderBug(exc, self.plugin) from exc
        finally:
            _FILLING.reset(token)
        self.live.append(value)  # served as is: never re-read, so never validated twice
        _write(self.path, self.adapter, self.schema, value)
        return Filled()

    def read(self) -> Any:
        if self.live:
            return self.live[0]
        if self.stored is not None and self.cache.freshness(self.key).refreshed_at == self.known:
            return self.stored.value
        entry = _read(self.path, self.adapter, self.schema)  # refreshed by another process
        if entry is None:
            raise UntapedError(
                f"{self.plugin}'s cached answer changed while it was read; run it again",
                system=self.plugin,
                details={"cache": self.cache.qualified, "key": self.key},
            )
        return entry.value

    def record(self, state: CallState, freshness: Freshness) -> None:
        """Tell ``gather`` how old the answer is and, when it is stale, why."""
        if freshness.refreshed_at is not None:
            state.served(freshness.refreshed_at)
        if self.failure is not None:  # the live call failed and the entry stood in
            state.stale = state.stale or self.failure
        elif freshness.error is not None:  # an earlier attempt's, inside its retry window
            state.stale = state.stale or freshness.error.to_exception()


def call(
    provider: Contract,
    name: str,
    function: Callable[..., Any],
    cache: Cache,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Serve ``function``, filling contract method ``name``, through the answer cache.

    The caller's refresh directive decides. A provider used outside the
    registry (no binding) is called live, uncached.
    """
    binding = binding_of(provider)
    if binding is None:
        return function(provider, *args, **kwargs)
    key = entry_key(binding, argument_key(function, provider, args, kwargs))
    if (cache.qualified, key) in _FILLING.get():
        return function(provider, *args, **kwargs)  # an override's super(): the outer call keeps it
    hint = return_type(binding, name)
    answer = _Call(
        functools.partial(function, provider, *args, **kwargs),
        cache,
        key,
        binding.plugin,
        entry_path(cache, key),
        _adapter(hint)[0],
        _schema(binding, hint),
    )
    answer.load()
    with _answering(binding.plugin, cache) as state:
        try:
            served = cache.serve(
                {key: binding.plugin}, refresh=None, fill=answer.fill, read=answer.read
            )
        except _ProviderBug as exc:
            bug = exc.bug
        else:
            answer.record(state, served.freshness[key])
            return served.value
    raise bug  # outside the except: the provider's exception keeps its own cause and context
