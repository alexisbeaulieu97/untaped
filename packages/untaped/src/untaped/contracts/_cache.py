"""The answer cache behind ``@cached``: providers' answers kept in the owner's :class:`Cache`.

One entry per provider, method, settings and arguments, keyed ``<provider>:<hash>``
(``<hash>`` is the sha256 of the arguments' canonical JSON) in the partition of
the provider's settings digest (:mod:`untaped.settings_digest`): the cache is the
owner's and the entry one provider's, so whatever reports an entry (a stale
line) names whose it is, and an answer is never served to a provider whose
settings read otherwise. The entry is a typed cache file like any
``Cache(model=)``'s, under the owner's ``cached/<contract>_<method>/<owner's settings digest>/``:
its freshness, then the answer. Its type is the return type's shape (with the
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar, get_args, get_origin

from pydantic_core import to_jsonable_python

from untaped.cache import (
    Cache,
    CallState,
    Filled,
    Freshness,
    ValueTyping,
    answering,
    call_state,
    current_call,
    value_typing,
)
from untaped.contracts._declare import Binding, Contract, binding_of
from untaped.errors import UntapedError
from untaped.output.progress import ProgressHandle
from untaped.output.sink import current_sink
from untaped.settings_digest import settings_digest

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
def _typing_of(hint: Any) -> ValueTyping:
    return value_typing(hint)


def _typing(binding: Binding, hint: Any) -> ValueTyping:
    """The entry's type: the return type's, and the provider's own model's when bridged."""
    typing = _typing_of(hint)
    if binding.owns_item or binding.item is None:
        return typing
    compound = hashlib.sha256(f"{typing.shape}:{_typing_of(binding.item).shape}".encode())
    return ValueTyping(typing.adapter, compound.hexdigest()[:16])


def entry_key(binding: Binding, digest: str) -> str:
    """The entry's key in the owner's cache: ``<provider>:<arguments hash>``."""
    return f"{binding.plugin}:{digest}"


def argument_key(function: Callable[..., Any], provider: object, args: Any, kwargs: Any) -> str:
    """sha256 of the call's arguments as canonical JSON (defaults applied)."""
    bound = inspect.signature(function).bind(provider, *args, **kwargs)
    bound.apply_defaults()
    arguments = dict(list(bound.arguments.items())[1:])
    canonical = json.dumps(
        to_jsonable_python(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class _ProviderBug(UntapedError):
    """A provider's own exception, carried through ``serve()`` (which falls back on these)."""

    def __init__(self, bug: Exception, plugin: str) -> None:
        super().__init__(str(bug) or type(bug).__name__, system=plugin)
        self.bug = bug


@contextmanager
def _answering(provider: str, cache: Cache[Any], caller: str) -> Iterator[CallState]:
    """The call state ``serve()`` defers to while answering for ``provider`` from ``cache``.

    ``gather``'s when it opened one; otherwise one of this call's own, whose
    stale lines are said when the call returns.
    """
    outer = current_call()
    if outer is not None:
        with answering(cache, provider):
            yield outer
        return
    with call_state(None, caller=caller) as state, answering(cache, provider):
        yield state
    for line in state.stale_lines:
        current_sink().emit(line)


@dataclass
class _Call:
    """One provider call through the answer cache: what ``serve()`` fills."""

    live_call: Callable[[], Any]
    cache: Cache[Any]
    key: str
    plugin: str
    failure: BaseException | None = None

    def fill(self, _key: str, _task: ProgressHandle) -> Filled[Any]:
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
        return Filled[Any](value=value)  # served as is: never re-read, so never validated twice

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
    answer = _Call(
        functools.partial(function, provider, *args, **kwargs), cache, key, binding.plugin
    )
    caller = f"{binding.plugin}'s {binding.contract.cls.__name__}.{name}"
    with _answering(binding.plugin, cache, caller) as state:
        try:
            served = cache._serve_with(
                {key: binding.plugin},
                refresh=None,
                fill=answer.fill,
                typing=_typing(binding, return_type(binding, name)),
                partition=settings_digest(binding.plugin),
            )
        except _ProviderBug as exc:
            bug = exc.bug
        else:
            answer.record(state, served.freshness[key])
            return served.value[key]
        raise bug  # outside the except: it keeps its own cause; inside the with: lines dropped
