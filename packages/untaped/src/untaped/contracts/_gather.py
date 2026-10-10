"""Asking every provider of a contract method: ``gather`` and its answers.

``gather(method, refresh=, needs=, deadline=)(*args)`` returns one answer per
provider that fills the method, in rank order (ranked plugins first, then the
rest by name): :class:`Ok`, :class:`Failed` or :class:`Skipped`. Every issued
item is validated once, strictly, as the owner's model after the call, with
its ``source`` stamped.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Concatenate, get_args, get_origin, overload

from pydantic import BaseModel, TypeAdapter, ValidationError

from untaped.concurrency import bounded_map
from untaped.contracts._cache import NoCache, call_state, return_type
from untaped.contracts._declare import (
    Binding,
    Contract,
    Issued,
    Method,
    NotReady,
    Source,
    fills,
    method_contract,
)
from untaped.contracts._registry import Provider, Quarantined, offers, owner_of, ranking
from untaped.errors import ConfigError, UntapedError, first_validation_error
from untaped.http import request_deadline
from untaped.records import kind_of

#: Providers asked at once by one ``gather``.
_POOL = 8


@dataclass(frozen=True, slots=True)
class Failed:
    """A provider that was asked and failed; ``error`` keeps its category and exit code."""

    plugin: str
    error: UntapedError
    rank: int | None = None
    #: A stable reason when the SDK failed it (``invalid-item``, a quarantine reason).
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Ok[R]:
    """A provider's answer.

    ``stale`` is set when a cached answer stood in for a live call that
    failed; ``invalid`` lists the rows a listing dropped as invalid.
    """

    plugin: str
    value: R
    rank: int | None = None
    refreshed_at: datetime | None = None
    stale: Failed | None = None
    invalid: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Skipped:
    """A provider that was not asked, or whose answer doesn't count, and why.

    ``reason`` is stable: ``not-configured``, ``no-cache``, ``invalid-item``,
    ``unbridged-item`` or a quarantine reason.
    """

    plugin: str
    reason: str
    detail: str
    rank: int | None = None


type Answer[R] = Ok[R] | Failed | Skipped


class Answers[R](Sequence[Answer[R]]):
    """Every provider's answer to one method call, in rank order."""

    def __init__(
        self, items: Sequence[Answer[R]], *, owner: str, contract: str, method: str
    ) -> None:
        self._items = tuple(items)
        self.owner = owner
        self.contract = contract
        self.method = method

    @overload
    def __getitem__(self, index: int) -> Answer[R]: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[Answer[R]]: ...
    def __getitem__(self, index: int | slice) -> Answer[R] | Sequence[Answer[R]]:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Answer[R]]:
        return iter(self._items)

    def __repr__(self) -> str:
        return f"Answers({self.owner}.{self.contract}.{self.method}, {list(self._items)!r})"

    @property
    def rank_command(self) -> str:
        """The ``untaped plugin rank`` line that orders these providers."""
        plugins = " ".join(answer.plugin for answer in self._items)
        return f"untaped plugin rank {self.owner}.{self.contract} {self.method} {plugins}".rstrip()


class _Invalid(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def gather[C: Contract, **P, R](
    method: Callable[Concatenate[C, P], R],
    /,
    *,
    refresh: bool | None = None,
    needs: Sequence[Callable[..., Any]] = (),
    deadline: float | timedelta | None = None,
) -> Callable[P, Answers[R]]:
    """Ask every ready provider of ``method``; call the result with the method's arguments.

    ``refresh`` directs ``@cached`` methods: ``False`` serves cached answers
    only (none stored: ``Skipped(no-cache)``), ``None`` serves within its max age
    and calls otherwise, ``True`` always calls. ``needs`` names further
    methods a provider must fill to be asked. ``deadline`` (seconds) bounds
    every request a provider makes through ``self.http``. With no ready
    provider at all it raises :class:`ConfigError` (exit 4) naming why each
    one isn't.
    """
    info, wanted = method_contract(method)
    needed = [wanted.name]
    for each in needs:
        other, also = method_contract(each)
        if other.cls is not info.cls:
            raise TypeError(f"needs= names {other.name}.{also.name}, not a {info.name} method")
        needed.append(also.name)
    seconds = deadline.total_seconds() if isinstance(deadline, timedelta) else deadline

    def call(*args: P.args, **kwargs: P.kwargs) -> Answers[R]:
        owner = owner_of(info)
        order = ranking(owner, info.name, wanted.name)
        answers: dict[str, Answer[R]] = {}
        runnable: list[tuple[Provider, int | None]] = []
        reasons: list[str] = []
        for entry in offers(info):
            rank = order.index(entry.plugin) if entry.plugin in order else None
            if isinstance(entry, Quarantined):
                answers[entry.plugin] = _excluded(entry.plugin, entry.reason, entry.detail, rank)
                reasons.append(f"{entry.plugin}: quarantined ({entry.reason}): {entry.detail}")
                continue
            provider_class = type(entry.instance)
            if not all(fills(provider_class, info, name) for name in needed):
                continue
            ready = _ready(entry)
            if ready is not None:
                answers[entry.plugin] = Skipped(entry.plugin, "not-configured", ready.reason, rank)
                setting = f" (set {ready.setting})" if ready.setting else ""
                reasons.append(f"{entry.plugin}: {ready.reason}{setting}")
                continue
            runnable.append((entry, rank))
        if not runnable:
            what = f"{owner}.{info.name}.{wanted.name}"
            detail = "; ".join(reasons) if reasons else "no installed plugin fills it"
            raise ConfigError(f"no provider of {what} is ready: {detail}")

        def ask(job: tuple[Provider, int | None]) -> Answer[R]:
            provider, rank = job
            return _ask(provider, wanted, rank, refresh, seconds, args, kwargs)

        def keep(job: tuple[Provider, int | None], answer: Answer[R]) -> None:
            answers[job[0].plugin] = answer

        bounded_map(ask, runnable, concurrency=min(_POOL, len(runnable)), on_each=keep)
        ordered = sorted(
            answers.values(),
            key=lambda answer: (answer.rank is None, answer.rank or 0, answer.plugin),
        )
        return Answers(ordered, owner=owner, contract=info.name, method=wanted.name)

    return call


def _ready(provider: Provider) -> NotReady | None:
    try:
        return provider.instance.ready()
    except Exception as exc:
        return NotReady(f"ready() raised {type(exc).__name__}: {exc}")


def _excluded[R](plugin: str, reason: str, detail: str, rank: int | None) -> Answer[R]:
    """A provider the SDK set aside: skipped when unranked, failed when ranked."""
    if rank is None:
        return Skipped(plugin, reason, detail, rank)
    error = ConfigError(detail, system=plugin, hint=f"upgrade untaped-{plugin}")
    return Failed(plugin, error, rank, reason)


def _ask[R](
    provider: Provider,
    method: Method,
    rank: int | None,
    refresh: bool | None,
    deadline: float | None,
    args: Any,
    kwargs: Any,
) -> Answer[R]:
    plugin = provider.plugin
    with call_state(refresh) as state, request_deadline(deadline):
        try:
            value = getattr(provider.instance, method.name)(*args, **kwargs)
        except NoCache:
            return Skipped(plugin, "no-cache", "no cached answer is stored", rank)
        except UntapedError as exc:
            return Failed(plugin, exc, rank)
        except Exception as exc:
            error = UntapedError(f"{type(exc).__name__}: {exc}", system=plugin)
            return Failed(plugin, error, rank)
    try:
        checked, invalid = validate(provider.binding, method, value)
    except _Invalid as exc:
        return _excluded(plugin, exc.reason, str(exc), rank)
    stale = None
    if state.stale is not None:
        cause = state.stale
        if isinstance(cause, UntapedError):
            stale = Failed(plugin, cause, rank)
        else:
            wrapped = UntapedError(f"{type(cause).__name__}: {cause}", system=plugin)
            stale = Failed(plugin, wrapped, rank)
    return Ok(plugin, checked, rank, state.refreshed_at, stale, invalid)


def validate(binding: Binding, method: Method, value: Any) -> tuple[Any, tuple[str, ...]]:
    """``value`` validated once as the contract's return type; issued items stamped.

    A listing drops an invalid row (its message is returned); any other
    method's invalid item raises ``_Invalid`` (``invalid-item``), as does a
    forged or missing ``source`` (``unbridged-item``) anywhere.
    """
    hint = return_type(binding, method.name)
    origin, args = get_origin(hint), get_args(hint)
    model = args[0] if origin is list and args else hint
    issued = isinstance(model, type) and issubclass(model, Issued)
    if not method.listing:
        if not issued:
            try:
                return TypeAdapter(hint).validate_python(value, strict=True), ()
            except ValueError as exc:
                raise _invalid(binding, hint, _message(exc)) from None
        if origin is not list:
            try:
                return issue(binding, model, value), ()
            except ValueError as exc:
                raise _invalid(binding, model, _message(exc)) from None
    if not isinstance(value, list):
        raise _invalid(binding, hint, f"expected a list, got {type(value).__name__}")
    row = None if issued else TypeAdapter(model)
    kept: list[Any] = []
    invalid: list[str] = []
    for index, item in enumerate(value):
        try:
            kept.append(
                issue(binding, model, item)
                if row is None
                else row.validate_python(item, strict=True)
            )
        except ValueError as exc:
            message = f"row {index + 1}: {_message(exc)}"
            if not method.listing:
                raise _invalid(binding, model, message) from None
            invalid.append(message)
    return kept, tuple(invalid)


def _message(exc: ValueError) -> str:
    return first_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)


def _invalid(binding: Binding, what: Any, message: str) -> _Invalid:
    name = getattr(what, "__qualname__", repr(what))
    return _Invalid("invalid-item", f"{binding.plugin} returned an invalid {name}: {message}")


def issue[I: Issued](binding: Binding, model: type[I], item: object) -> I:
    """``item`` validated strictly as ``model`` (the owner's), with its ``source`` stamped.

    An item without a source gets the provider's own when its ``T`` is the
    owner's model; otherwise the bridge must have stamped it. A source naming
    another plugin is never accepted.
    """
    if not isinstance(item, BaseModel):
        raise ValueError(f"expected a {model.__qualname__}, got {type(item).__name__}")
    source = getattr(item, "source", None)
    if source is None:
        if not binding.owns_item:
            raise _Invalid(
                "unbridged-item",
                f"{binding.plugin} returned a {model.__qualname__} without a source; "
                "build it with the provider's bridge method",
            )
        source = Source(plugin=binding.plugin, kind=kind_of(model) or "")
    elif not isinstance(source, Source) or source.plugin != binding.plugin:
        named = source.plugin if isinstance(source, Source) else source
        raise _Invalid(
            "unbridged-item",
            f"{binding.plugin} returned a {model.__qualname__} whose source names {named!r}",
        )
    data = item.model_dump(mode="json", by_alias=True, round_trip=True, warnings=False)
    data["source"] = source.model_dump(mode="json")
    return model.model_validate_json(json.dumps(data), strict=True)
