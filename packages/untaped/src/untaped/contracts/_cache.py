"""The answer cache behind ``@cached``: one JSON file per provider, method, profile and arguments.

Path: ``~/.untaped/plugins/<provider>/cache/<owner>.<contract>.<method>/<profile>/<key>.json``,
holding ``{"untaped": "1", "schema": …, "refreshed_at": …, "value": …}``. ``<key>`` is the
sha256 of the arguments' canonical JSON and ``schema`` the sha256 of the return type's JSON
schema, so an entry written for another shape of the model is a miss, never an error.

``gather`` passes its ``refresh`` directive down through a context variable and reads back
what the call served: the oldest ``refreshed_at`` and, when a live call failed and an entry
stood in, the live error (the answer is then stale).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin

from pydantic import TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from untaped.contracts._declare import Binding, Contract, binding_of
from untaped.errors import ConfigError
from untaped.fs import atomic_write, file_lock
from untaped.git import safe_path_segment
from untaped.plugins.registry import plugin_dir

_FORMAT = "1"
_LOCK_TIMEOUT = 10.0


class NoCache(Exception):
    """``refresh=False`` asked for a cached answer and none is stored."""


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


def return_type(binding: Binding, name: str) -> Any:
    """The contract method's return type with the provider's ``T`` put in for the parameter."""
    hint = binding.contract.methods[name].hints["return"]
    return _substitute(hint, binding.contract.item_param, binding.item)


def _substitute(hint: Any, param: TypeVar | None, item: type | None) -> Any:
    if param is None or item is None:
        return hint
    if hint is param:
        return item
    origin, args = get_origin(hint), get_args(hint)
    if origin is list and args == (param,):
        return list[item]  # type: ignore[valid-type]  # a runtime class
    return hint


@cache
def _adapter(hint: Any) -> tuple[TypeAdapter[Any], str]:
    adapter: TypeAdapter[Any] = TypeAdapter(hint)
    schema = json.dumps(adapter.json_schema(), sort_keys=True, separators=(",", ":"))
    return adapter, hashlib.sha256(schema.encode()).hexdigest()


def entry_path(binding: Binding, name: str, key: str) -> Path:
    """Where ``name``'s answer for argument key ``key`` lives in the active profile."""
    folder = f"{binding.owner}.{binding.contract.name}.{name}"
    profile = safe_path_segment(binding.profile)
    return plugin_dir(binding.spec) / "cache" / folder / profile / f"{key}.json"


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
    refreshed_at: datetime
    value: Any


def _read(path: Path, adapter: TypeAdapter[Any], schema: str) -> _Entry | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("untaped") != _FORMAT or raw.get("schema") != schema:
            return None
        at = datetime.fromisoformat(raw["refreshed_at"])
        value = adapter.validate_json(json.dumps(raw["value"]), strict=True)
    except OSError, ValueError, KeyError, TypeError, AttributeError, ValidationError:
        return None
    return _Entry(at if at.tzinfo else at.replace(tzinfo=UTC), value)


def _write(path: Path, adapter: TypeAdapter[Any], schema: str, value: Any, at: datetime) -> None:
    try:
        dumped = adapter.dump_python(
            value, mode="json", by_alias=True, round_trip=True, warnings=False
        )
    except Exception:
        return
    text = json.dumps(
        {"untaped": _FORMAT, "schema": schema, "refreshed_at": at.isoformat(), "value": dumped},
        ensure_ascii=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(
        path.with_name(f"{path.name}.lock"),
        timeout=_LOCK_TIMEOUT,
        error=ConfigError,
        busy=f"another untaped process is writing {path}",
        failed=f"could not lock {path}",
    ):
        atomic_write(path, text)


def call(
    provider: Contract,
    function: Callable[..., Any],
    ttl: timedelta,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Serve ``function`` through the answer cache, per the caller's refresh directive.

    A provider used outside the registry (no binding) is called live, uncached.
    """
    binding = binding_of(provider)
    if binding is None:
        return function(provider, *args, **kwargs)
    state = _CALL.get()
    refresh = None if state is None else state.refresh
    adapter, schema = _adapter(return_type(binding, function.__name__))
    path = entry_path(binding, function.__name__, argument_key(function, provider, args, kwargs))
    entry = _read(path, adapter, schema)
    now = datetime.now(UTC)
    if entry is not None and (
        refresh is False or (refresh is None and now - entry.refreshed_at < ttl)
    ):
        if state is not None:
            state.served(entry.refreshed_at)
        return entry.value
    if refresh is False:
        raise NoCache
    try:
        value = function(provider, *args, **kwargs)
    except Exception as exc:
        if entry is None:
            raise
        if state is not None:
            state.served(entry.refreshed_at)
            state.stale = state.stale or exc
        return entry.value
    _write(path, adapter, schema, value, now)
    if state is not None:
        state.served(now)
    return value
