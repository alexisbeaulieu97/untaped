"""Asking every provider of a contract method: ``gather`` and its answers.

``gather(method, plugins=, refresh=, progress=)(*args)`` returns one answer
per provider that fills the method, in rank order (ranked plugins first, then
the rest by name): :class:`Ok`, :class:`Errored` or :class:`Skipped`. Every issued
item is validated once, strictly, as the owner's model after the call, with
its ``source`` stamped. A stale answer's ``untaped.stale`` line is said when
the answer is used, never when it is fetched: :class:`Answers` says it the
first time it is iterated or indexed, ``select_one`` for the match it returns.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Concatenate, cast, get_args, get_origin, overload

from pydantic import BaseModel, TypeAdapter, ValidationError

from untaped.runtime.contracts.registry import (
    BoundProvider,
    Quarantined,
    offers,
    owner_of,
    ranking,
    ready_of,
    upgrade_hint,
)
from untaped.runtime.sink import Diagnostics
from untaped.sdk._concurrency import bounded_map
from untaped.sdk.contracts._cache import return_type
from untaped.sdk.contracts._declare import (
    Binding,
    Contract,
    Method,
    NotReady,
    fills,
    method_contract,
)
from untaped.sdk.contracts._reasons import ErrorReason, QuarantineReason, Reason, SkipReason
from untaped.sdk.diagnostic import StaleData
from untaped.sdk.errors import ConfigError, UntapedError, first_validation_error
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.records import Issued, Source, kind_of
from untaped.sdk.storage.cache import CallState, NeverRefreshed, call_state
from untaped.sdk.words import command_line

_LOG = logging.getLogger("untaped.contracts")

#: Providers asked at once by one ``gather``.
_POOL = 8


@dataclass(frozen=True, slots=True)
class Errored:
    """A provider that was asked and failed; ``error`` keeps its category and exit code."""

    plugin: str
    error: UntapedError
    rank: int | None = None
    #: ``raised`` when the provider raised, else why the SDK failed it
    #: (``invalid-item``, a quarantine reason).
    reason: ErrorReason | None = None


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
    stale: Errored | None = None
    invalid: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Skipped:
    """A provider that was not asked, or whose answer doesn't count, and why."""

    plugin: str
    reason: SkipReason
    detail: str
    rank: int | None = None


type Answer[R] = Ok[R] | Errored | Skipped


class NoProviderReady(ConfigError):
    """No provider of the method could be asked (exit 4); ``not_ready`` says why, per plugin.

    ``not_ready`` holds each composed provider that isn't ready in the
    profile, ``quarantined`` each provider offer the registry refused and its
    reason; both empty means no composed plugin fills the method. A plugin
    quarantined at composition offers nothing, so it is in neither: it is in
    ``composition().quarantine``. An owner may turn this into its own error
    (a name nobody could look up is not found).
    """

    def __init__(
        self,
        message: str,
        *,
        not_ready: Mapping[str, NotReady],
        quarantined: Mapping[str, QuarantineReason] | None = None,
    ) -> None:
        super().__init__(message)
        self.not_ready = dict(not_ready)
        self.quarantined = dict(quarantined or {})


class Answers[R](Sequence[Answer[R]]):
    """Every provider's answer to one method call, in rank order.

    ``pending`` holds, by plugin, the ``untaped.stale`` lines of the answers
    served stale; they are said once, the first time the answers are
    iterated or indexed (``len`` and :meth:`rank_command` say nothing).
    """

    def __init__(
        self,
        items: Sequence[Answer[R]],
        *,
        owner: str,
        contract: str,
        method: str,
        pending: Mapping[str, Sequence[StaleData]] | None = None,
    ) -> None:
        self._items = tuple(items)
        self.owner = owner
        self.contract = contract
        self.method = method
        self._pending = {plugin: tuple(lines) for plugin, lines in (pending or {}).items() if lines}
        self._lock = threading.Lock()

    @overload
    def __getitem__(self, index: int) -> Answer[R]: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[Answer[R]]: ...
    def __getitem__(self, index: int | slice) -> Answer[R] | Sequence[Answer[R]]:
        self._say()
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Answer[R]]:
        self._say()
        return iter(self._items)

    @property
    def ok(self) -> tuple[Ok[R], ...]:
        """The answers that came back, in rank order."""
        return tuple(answer for answer in self if isinstance(answer, Ok))

    @property
    def errored(self) -> tuple[Errored, ...]:
        """The providers that were asked and failed, in rank order."""
        return tuple(answer for answer in self if isinstance(answer, Errored))

    @property
    def skipped(self) -> tuple[Skipped, ...]:
        """The providers that were not asked, or whose answer doesn't count."""
        return tuple(answer for answer in self if isinstance(answer, Skipped))

    def take_stale(self) -> tuple[StaleData, ...]:
        """The stale lines not yet said, which nothing says afterwards.

        For a consumer that reports staleness itself: it prints, drops or
        raises them. Iterating the answers then says nothing.
        """
        with self._lock:
            pending, self._pending = self._pending, {}
        return tuple(line for lines in pending.values() for line in lines)

    def _say(self) -> None:
        for line in self.take_stale():
            Diagnostics().emit(line)

    def _unsaid(self) -> tuple[Answer[R], ...]:
        """The answers, their stale lines left pending (``select_one`` says them per answer)."""
        return self._items

    def _take(self, plugin: str) -> tuple[StaleData, ...]:
        """``plugin``'s pending stale lines, which nothing says afterwards."""
        with self._lock:
            return self._pending.pop(plugin, ())

    def __repr__(self) -> str:
        return f"Answers({self.owner}.{self.contract}.{self.method}, {list(self._items)!r})"

    def rank_command(self, plugins: Sequence[str] | None = None) -> str:
        """The ``untaped plugin rank`` line ordering ``plugins`` (default: every answer's)."""
        named = [answer.plugin for answer in self._items] if plugins is None else plugins
        args = " ".join([f"{self.owner}.{self.contract}", self.method, *named])
        return command_line(f"plugin rank {args}")


class _Invalid(Exception):
    def __init__(self, reason: ErrorReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def gather[C: Contract, **P, R](
    method: Callable[Concatenate[C, P], R],
    /,
    *,
    plugins: Collection[type[Contract] | str] | None = None,
    refresh: bool | None = None,
    progress: ProgressHandle = NO_PROGRESS,
) -> Callable[P, Answers[R]]:
    """Ask every ready provider of ``method``; call the result with the method's arguments.

    ``plugins`` asks only those providers, each a provider class where the
    code holds one or a plugin name at the user edge (``--source github``);
    the others are left out of the answers. ``refresh`` directs ``@cached``
    methods: ``False`` serves cached answers only (none stored:
    ``Skipped(no-cache)``), ``True`` always calls, and ``None`` follows the
    command's ``Refresh`` when it names this method's cache, else serves
    within the cache's max age (its ``<owner>.caches.<name>.max_age_seconds``
    setting). A call refreshing an answer holds that answer's lock, so a
    second process asking the same provider waits for it. With no ready
    provider at all it raises :class:`NoProviderReady` (exit 4) naming why
    each one isn't. ``progress`` counts the providers as they answer, each
    named as it does; a provider's method never gets the handle.
    """
    info, wanted = method_contract(method)
    asked = _picked(plugins)

    def call(*args: P.args, **kwargs: P.kwargs) -> Answers[R]:
        owner = owner_of(info)
        order = ranking(owner, info.name, wanted.name)
        answers: dict[str, Answer[R]] = {}
        runnable: list[tuple[BoundProvider, int | None]] = []
        reasons: list[str] = []
        not_ready: dict[str, NotReady] = {}
        quarantined: dict[str, QuarantineReason] = {}
        for entry in offers(info):
            if not asked(entry):
                continue
            rank = order.index(entry.plugin) if entry.plugin in order else None
            if isinstance(entry, Quarantined):
                answers[entry.plugin] = _excluded(entry.plugin, entry.reason, entry.detail, rank)
                reasons.append(f"{entry.plugin}: quarantined ({entry.reason}): {entry.detail}")
                quarantined[entry.plugin] = entry.reason
                continue
            if not fills(type(entry.instance), info, wanted.name):
                continue
            ready = ready_of(entry)
            if ready is not None:
                answers[entry.plugin] = Skipped(
                    entry.plugin, Reason.not_configured, ready.reason, rank
                )
                not_ready[entry.plugin] = ready
                setting = f" (set {ready.spelled})" if ready.spelled else ""
                reasons.append(f"{entry.plugin}: {ready.reason}{setting}")
                continue
            runnable.append((entry, rank))
        if not runnable:
            what = f"{owner}.{info.name}.{wanted.name}"
            detail = "; ".join(reasons) if reasons else "no installed plugin fills it"
            raise NoProviderReady(
                f"no provider of {what} is ready: {detail}",
                not_ready=not_ready,
                quarantined=quarantined,
            )

        stale: dict[str, list[StaleData]] = {}

        def ask(job: tuple[BoundProvider, int | None]) -> tuple[Answer[R], list[StaleData]]:
            provider, rank = job
            return _ask(provider, wanted, rank, refresh, args, kwargs)

        def keep(
            job: tuple[BoundProvider, int | None], asked: tuple[Answer[R], list[StaleData]]
        ) -> None:
            answers[job[0].plugin], stale[job[0].plugin] = asked
            progress.update(job[0].plugin, done=len(stale), total=len(runnable))

        progress.update(done=0, total=len(runnable))
        bounded_map(ask, runnable, concurrency=min(_POOL, len(runnable)), on_each=keep)
        ordered = sorted(
            answers.values(),
            key=lambda answer: (answer.rank is None, answer.rank or 0, answer.plugin),
        )
        pending = {answer.plugin: stale.get(answer.plugin, []) for answer in ordered}
        return Answers(
            ordered, owner=owner, contract=info.name, method=wanted.name, pending=pending
        )

    return call


def _picked(
    plugins: Collection[type[Contract] | str] | None,
) -> Callable[[BoundProvider | Quarantined], bool]:
    """Whether ``gather(plugins=)`` asks an offer: by its plugin's name or its provider class."""
    if plugins is None:
        return lambda _: True
    names = {each for each in plugins if isinstance(each, str)}
    classes = tuple(each for each in plugins if isinstance(each, type))
    return lambda entry: (
        entry.plugin in names
        or (isinstance(entry, BoundProvider) and isinstance(entry.instance, classes))
    )


def _excluded[R](
    plugin: str, reason: QuarantineReason | ErrorReason, detail: str, rank: int | None
) -> Answer[R]:
    """A provider the SDK set aside: skipped when unranked, failed when ranked.

    The failure hints at upgrading ``plugin`` unless the reason is no defect
    of its own (its owner is not installed).
    """
    if rank is None:
        return Skipped(plugin, cast("SkipReason", reason), detail, rank)
    hint = None if reason == Reason.owner_not_installed else upgrade_hint(plugin)
    error = ConfigError(detail, system=plugin, hint=hint)
    return Errored(plugin, error, rank, reason)


def _ask[R](
    provider: BoundProvider,
    method: Method,
    rank: int | None,
    refresh: bool | None,
    args: Any,
    kwargs: Any,
) -> tuple[Answer[R], list[StaleData]]:
    """``provider``'s answer, and the stale lines its caches left for whoever reads it."""
    caller = f"{provider.plugin}'s {provider.binding.contract.cls.__name__}.{method.name}"
    with call_state(refresh, caller=caller) as state:
        return _answer(provider, method, rank, state, args, kwargs), state.stale_lines


def _answer[R](
    provider: BoundProvider,
    method: Method,
    rank: int | None,
    state: CallState,
    args: Any,
    kwargs: Any,
) -> Answer[R]:
    plugin = provider.plugin
    try:
        value = getattr(provider.instance, method.name)(*args, **kwargs)
    except NeverRefreshed:
        return Skipped(plugin, Reason.no_cache, "no cached answer is stored", rank)
    except UntapedError as exc:
        return Errored(plugin, exc, rank, Reason.raised)
    except Exception as exc:
        return Errored(plugin, _unexpected(plugin, method, exc), rank, Reason.raised)
    try:
        checked, invalid = validate(provider.binding, method, value)
    except _Invalid as exc:
        return _excluded(plugin, exc.reason, str(exc), rank)
    stale = None
    if state.stale is not None:
        cause = state.stale
        if isinstance(cause, UntapedError):
            stale = Errored(plugin, cause, rank, Reason.raised)
        else:
            stale = Errored(plugin, _unexpected(plugin, method, cause), rank, Reason.raised)
    return Ok(plugin, checked, rank, state.refreshed_at, stale, invalid)


def _unexpected(plugin: str, method: Method, exc: BaseException) -> UntapedError:
    """``exc``, a provider bug rather than an untaped error, as its answer's error.

    The answer keeps the message only, so the traceback goes to the debug log.
    """
    _LOG.debug("%s's %s raised %s", plugin, method.name, type(exc).__name__, exc_info=exc)
    return UntapedError(f"{type(exc).__name__}: {exc}", system=plugin)


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
    return _Invalid(Reason.invalid_item, f"{binding.plugin} returned an invalid {name}: {message}")


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
                Reason.unbridged_item,
                f"{binding.plugin} returned a {model.__qualname__} without a source; "
                "build it with the provider's bridge method",
            )
        source = Source(plugin=binding.plugin, kind=kind_of(model) or "")
    elif not isinstance(source, Source) or source.plugin != binding.plugin:
        named = source.plugin if isinstance(source, Source) else source
        raise _Invalid(
            Reason.unbridged_item,
            f"{binding.plugin} returned a {model.__qualname__} whose source names {named!r}",
        )
    data = item.model_dump(mode="json", by_alias=True, round_trip=True, warnings=False)
    data["source"] = source.model_dump(mode="json")
    return model.model_validate_json(json.dumps(data), strict=True)
