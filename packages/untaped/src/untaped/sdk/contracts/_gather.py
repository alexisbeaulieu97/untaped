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
from untaped.runtime.run_output import Diagnostics
from untaped.sdk._concurrency import bounded_map
from untaped.sdk.contracts._cache import return_type
from untaped.sdk.contracts._declare import (
    Binding,
    Contract,
    Method,
    NotReady,
    contract_of,
    fills,
    method_contract,
    old_spellings,
)
from untaped.sdk.contracts._reasons import ErrorReason, QuarantineReason, Reason, SkipReason
from untaped.sdk.diagnostic import Stale
from untaped.sdk.errors import ConfigError, UntapedError, first_validation_error
from untaped.sdk.marks import experimental
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.records import Issued, Record, Source, kind_of
from untaped.sdk.storage._serve_scope import CallState, call_state
from untaped.sdk.storage.cache import NeverRefreshed
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

    ``not_ready`` holds, by plugin, its first provider that isn't ready in
    the profile, ``quarantined`` its first offer the registry refused and that
    offer's reason (the message names every one); both empty means no
    composed plugin fills the method. A plugin quarantined at composition
    offers nothing, so it is in neither: it is in ``composition().quarantine``.
    An owner may turn this into its own error (a name nobody could look up is
    not found).
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

    ``pending`` holds, by position in ``items``, the ``untaped.stale`` lines
    of the answers served stale; they are said once, the first time the answers are
    iterated or indexed (``len`` and :meth:`rank_command` say nothing).
    """

    def __init__(
        self,
        items: Sequence[Answer[R]],
        *,
        owner: str,
        contract: str,
        method: str,
        pending: Mapping[int, Sequence[Stale]] | None = None,
    ) -> None:
        self._items = tuple(items)
        self.owner = owner
        self.contract = contract
        self.method = method
        self._pending = {at: tuple(lines) for at, lines in (pending or {}).items() if lines}
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

    def take_stale(self) -> tuple[Stale, ...]:
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

    def _take(self, answer: Answer[R]) -> tuple[Stale, ...]:
        """``answer``'s pending stale lines (``answer`` may be a stale ``Ok``'s ``stale``),
        which nothing says afterwards."""
        at = next(
            (
                n
                for n, each in enumerate(self._items)
                if each is answer or (isinstance(each, Ok) and each.stale is answer)
            ),
            None,
        )
        with self._lock:
            return () if at is None else self._pending.pop(at, ())

    def __repr__(self) -> str:
        return f"Answers({self.owner}.{self.contract}.{self.method}, {list(self._items)!r})"

    def rank_command(self, plugins: Sequence[str] | None = None) -> str:
        """The ``untaped plugin rank`` line ordering ``plugins`` (default: every answer's)."""
        named = [answer.plugin for answer in self._items] if plugins is None else plugins
        named = list(dict.fromkeys(named))  # one plugin's providers share its rank
        args = " ".join([f"{self.owner}.{self.contract}", self.method, *named])
        return command_line(f"plugin rank {args}")


@experimental
@dataclass(frozen=True, slots=True)
class ProviderOffer:
    """One plugin's offer to fill a contract, as composition took it (:func:`providers`).

    A taken offer has its ``provider`` class, which ``gather(plugins=)`` asks
    alone, and its own ``record`` with that record's ``kind`` when it issues
    one (both ``None`` when it keeps the owner's model). A refused offer has
    ``refused`` (the quarantine reason), ``detail`` and ``fix`` instead, and
    its ``provider`` and ``kind`` when the registry got as far as them.
    """

    plugin: str
    provider: type[Contract] | None = None
    record: type[Record] | None = None
    kind: str | None = None
    refused: QuarantineReason | None = None
    detail: str = ""
    fix: str = ""


@experimental
def providers(contract: type[Contract], /) -> list[ProviderOffer]:
    """Every offer to fill ``contract`` in this composition and profile.

    A plugin offers it once per record it issues, so one plugin can appear
    several times, each with a record (and kind) of its own. For an owner
    that tells its providers apart by their own records, such as a step kind
    named in a file: it reads which provider issues which record kind, then
    asks that one with ``gather(method, plugins={provider})``. The
    offers are loaded as an ask would load them; none is asked. A contract no
    composed plugin declares raises :class:`ConfigError`.
    """
    info = contract_of(contract)
    if info is None or info.cls is not contract:
        raise TypeError(f"{contract!r} is not a contract: pass the Contract class (RepoSource)")
    found: list[ProviderOffer] = []
    for entry in offers(info):
        if isinstance(entry, Quarantined):
            found.append(
                ProviderOffer(
                    entry.plugin,
                    provider=entry.provider,
                    kind=entry.issues or None,
                    refused=entry.reason,
                    detail=entry.detail,
                    fix=entry.fix,
                )
            )
            continue
        binding = entry.binding
        own = None if binding.owns_item else binding.item
        found.append(
            ProviderOffer(
                entry.plugin,
                provider=type(entry.instance),
                record=own,
                kind=(binding.item_kind or None) if own is not None else None,
            )
        )
    return found


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
    the others are left out of the answers, and a named one that doesn't
    fill ``method`` (an optional method) answers ``Skipped(not-filled)``, so
    a caller falling back to another method can say why. ``refresh``
    directs ``@cached`` methods: ``False`` serves cached answers only (none
    stored: ``Skipped(no-cache)``), ``True`` always calls, and ``None``
    serves within the cache's max age (its
    ``<owner>.caches.<name>.max_age_seconds`` setting): a command whose
    ``Refresh`` names the method passes its ``directive``. A call refreshing an
    answer holds that answer's lock, so a second process asking the same
    provider waits for it. With no ready
    provider at all, and none named that doesn't fill it, it raises
    :class:`NoProviderReady` (exit 4) naming why each one isn't.
    ``progress`` counts the providers as they answer, each named as it does;
    a provider's method never gets the handle.
    """
    info, wanted = method_contract(method)
    asked = _picked(plugins)

    def call(*args: P.args, **kwargs: P.kwargs) -> Answers[R]:
        owner = owner_of(info)
        order = ranking(owner, info.name, wanted.name, old_spellings(info, wanted.name))
        answers: dict[int, Answer[R]] = {}  # by offer: one plugin may offer several
        runnable: list[tuple[int, BoundProvider, int | None]] = []
        reasons: list[str] = []
        not_ready: dict[str, NotReady] = {}
        quarantined: dict[str, QuarantineReason] = {}
        for at, entry in enumerate(offers(info)):
            if not asked(entry):
                continue
            rank = order.index(entry.plugin) if entry.plugin in order else None
            if isinstance(entry, Quarantined):
                answers[at] = _excluded(
                    entry.plugin,
                    entry.reason,
                    entry.detail,
                    rank,
                    fix=entry.fix,
                    described=entry.described,
                )
                reasons.append(f"{entry.plugin}: quarantined ({entry.reason}): {entry.described}")
                quarantined.setdefault(entry.plugin, entry.reason)  # its first refused offer
                continue
            if not fills(type(entry.instance), info, wanted.name):
                if plugins is not None:  # named, so the caller's fallback has a reason to say
                    answers[at] = Skipped(
                        entry.plugin,
                        Reason.not_filled,
                        f"{entry.plugin} doesn't fill {info.cls.__name__}.{wanted.name}",
                        rank,
                    )
                continue
            ready = ready_of(entry, wanted.name)
            if ready is not None:
                answers[at] = Skipped(entry.plugin, Reason.not_configured, ready.reason, rank)
                not_ready.setdefault(entry.plugin, ready)
                setting = f" (set {ready.spelled})" if ready.spelled else ""
                reasons.append(f"{entry.plugin}: {ready.reason}{setting}")
                continue
            runnable.append((at, entry, rank))
        if not runnable and not any(
            isinstance(answer, Skipped) and answer.reason is Reason.not_filled
            for answer in answers.values()
        ):
            what = f"{owner}.{info.name}.{wanted.name}"
            detail = "; ".join(reasons) if reasons else "no installed plugin fills it"
            raise NoProviderReady(
                f"no provider of {what} is ready: {detail}",
                not_ready=not_ready,
                quarantined=quarantined,
            )

        stale: dict[int, list[Stale]] = {}

        def ask(job: tuple[int, BoundProvider, int | None]) -> tuple[Answer[R], list[Stale]]:
            _, provider, rank = job
            return _ask(provider, wanted, rank, refresh, args, kwargs)

        def keep(
            job: tuple[int, BoundProvider, int | None], asked: tuple[Answer[R], list[Stale]]
        ) -> None:
            answers[job[0]], stale[job[0]] = asked
            progress.update(job[1].plugin, done=len(stale), total=len(runnable))

        progress.update(done=0, total=len(runnable))
        if runnable:
            bounded_map(ask, runnable, concurrency=min(_POOL, len(runnable)), on_each=keep)
        ordered, pending = _ordered(answers, stale)
        return Answers(
            ordered, owner=owner, contract=info.name, method=wanted.name, pending=pending
        )

    return call


def _ordered[R](
    answers: Mapping[int, Answer[R]], stale: Mapping[int, list[Stale]]
) -> tuple[list[Answer[R]], dict[int, list[Stale]]]:
    """The answers (by offer) in rank order, and their stale lines by that order."""
    placed = sorted(
        answers.items(),
        key=lambda each: (each[1].rank is None, each[1].rank or 0, each[1].plugin, each[0]),
    )
    pending = {n: stale.get(at, []) for n, (at, _) in enumerate(placed)}
    return [answer for _, answer in placed], pending


def _picked(
    plugins: Collection[type[Contract] | str] | None,
) -> Callable[[BoundProvider | Quarantined], bool]:
    """Whether ``gather(plugins=)`` asks an offer: by its plugin's name or its provider class.

    A class picks that provider exactly, never a subclass of it, even when it
    was quarantined, so the answer says why; a class the run's composition
    places in a plugin whose offers failed to load as a whole picks that
    failure.
    """
    if plugins is None:
        return lambda _: True
    if isinstance(plugins, str):
        raise TypeError(f"plugins= takes a collection of names or classes, not {plugins!r}")
    names = {each for each in plugins if isinstance(each, str)}
    classes = {each for each in plugins if isinstance(each, type)}
    quarantined = {plugin for each in classes if (plugin := _plugin_of(each)) is not None}

    def picked(entry: BoundProvider | Quarantined) -> bool:
        if entry.plugin in names:
            return True
        if isinstance(entry, BoundProvider):
            return type(entry.instance) in classes
        if entry.provider is not None:
            return entry.provider in classes
        return entry.plugin in quarantined  # its whole group failed: no class to match

    return picked


def _plugin_of(provider: type) -> str | None:
    """The composed plugin whose package defines ``provider``, if a run knows one."""
    from untaped.sdk._run import run_or_none  # noqa: PLC0415 - the run imports the SDK

    run = run_or_none()
    if run is None:
        return None
    try:
        return run.composition.owner_of_module(provider.__module__)
    except LookupError:
        return None


def _excluded[R](
    plugin: str,
    reason: QuarantineReason | ErrorReason,
    detail: str,
    rank: int | None,
    *,
    fix: str = "",
    described: str = "",
) -> Answer[R]:
    """A provider the SDK set aside: skipped when unranked, failed when ranked.

    A skip says ``described`` (the detail with its fix), else ``detail``; the
    failure says ``detail`` and hints at ``fix``, else at upgrading ``plugin``.
    """
    if rank is None:
        return Skipped(plugin, cast("SkipReason", reason), described or detail, rank)
    error = ConfigError(detail, system=plugin, hint=fix or upgrade_hint(plugin))
    return Errored(plugin, error, rank, reason)


def _ask[R](
    provider: BoundProvider,
    method: Method,
    rank: int | None,
    refresh: bool | None,
    args: Any,
    kwargs: Any,
) -> tuple[Answer[R], list[Stale]]:
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
    A name of untaped or another plugin that the provider's owner no longer
    has is said as the command's error boundary says it
    (:func:`untaped.runtime.missing_names.translated`).
    """
    from untaped.runtime.missing_names import translated  # noqa: PLC0415 - on a failure only

    _LOG.debug("%s's %s raised %s", plugin, method.name, type(exc).__name__, exc_info=exc)
    explained = translated(exc)
    if explained is None:
        return UntapedError(f"{type(exc).__name__}: {exc}", system=plugin)
    explained.system = plugin  # the provider's answer, whatever it failed on
    return explained


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
        source = Source(plugin=binding.plugin, kind=kind_of(model) or binding.owner_kind)
    elif not isinstance(source, Source) or source.plugin != binding.plugin:
        named = source.plugin if isinstance(source, Source) else source
        raise _Invalid(
            Reason.unbridged_item,
            f"{binding.plugin} returned a {model.__qualname__} whose source names {named!r}",
        )
    data = item.model_dump(mode="json", by_alias=True, round_trip=True, warnings=False)
    data["source"] = source.model_dump(mode="json")
    return model.model_validate_json(json.dumps(data), strict=True)
