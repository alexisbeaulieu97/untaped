"""Choosing one item from many providers' answers, and reading a piped record.

``select_one`` is the SDK's mechanism; ``matches`` is the owner's policy. The
rules, in order (design §3):

1. The candidates are the items of each :class:`Ok` that ``matches`` accepts.
2. A stale answer with no match counts as its failure: a stale listing
   confirms a match, never an absence. An answer that dropped invalid rows
   counts as ``invalid-item``: skipped when unranked, failed when ranked (the
   bad row could be the one meant).
3. "Ranked above" means an explicit rank only; unranked providers are below
   every ranked one and never above each other. With any failure, a match
   from a provider ranked above every failed one wins, with a warning;
   otherwise the first failure's error stands. So with nothing ranked, any
   failure blocks.
4. With no failure: no match is :class:`NotFound`; one item from the
   highest-ranked matching provider is the answer; several from it, or
   several unranked providers matching, are :class:`Ambiguous`.
5. A skipped provider other than ``not-configured`` is reported as a warning.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from untaped.contracts._declare import ContractInfo, method_contract
from untaped.contracts._gather import Answers, Failed, Ok, Skipped, _Invalid, issue
from untaped.contracts._registry import Provider, offers
from untaped.errors import ConfigError, UsageError, first_validation_error
from untaped.pipe import PipeEnvelope
from untaped.records import kind_of, record_model


class NotFound(UsageError):
    """No provider's answer matched (exit 2)."""


class Ambiguous(UsageError):
    """More than one item matched and nothing ranks one above the other (exit 2)."""


def _warn(message: str) -> None:
    from untaped.ui import ui_context  # noqa: PLC0415 - keep the import light

    ui_context(strict=False).message("warning", message)


def _rank_key(rank: int | None) -> float:
    return float("inf") if rank is None else float(rank)


def select_one[I](answers: Answers[list[I]], matches: Callable[[I], bool]) -> I:
    """The one item ``matches`` picks across ``answers``, by the rules above.

    Raises the first failed provider's own error when a failure could hide
    the match, :class:`NotFound` or :class:`Ambiguous` (exit 2) otherwise.
    """
    failed, skipped, found = _sorted(answers, matches)
    for skip in skipped:
        if skip.reason != "not-configured":
            _warn(f"{skip.plugin} wasn't asked ({skip.reason}): {skip.detail}")
    if failed:
        lowest = min(_rank_key(each.rank) for each in failed)
        above = [(ok, hits) for ok, hits in found if _rank_key(ok.rank) < lowest]
        if not above:
            raise failed[0].error
        for each in failed:
            _warn(f"{each.plugin} failed ({each.error}); using a match ranked above it")
        return _one(min(above, key=lambda pair: _rank_key(pair[0].rank)), answers)
    if not found:
        unasked = [f"{skip.plugin} ({skip.reason})" for skip in skipped]
        tail = f"; not asked: {', '.join(unasked)}" if unasked else ""
        raise NotFound(f"no provider of {answers.owner}.{answers.contract} matched{tail}")
    ranked = [pair for pair in found if pair[0].rank is not None]
    if ranked:
        return _one(min(ranked, key=lambda pair: _rank_key(pair[0].rank)), answers)
    if len(found) > 1:
        plugins = ", ".join(ok.plugin for ok, _ in found)
        raise Ambiguous(
            f"{plugins} each have a match; rank them to choose",
            hint=f"run `{answers.rank_command}`",
            details={"providers": [ok.plugin for ok, _ in found]},
        )
    return _one(found[0], answers)


def _sorted[I](
    answers: Answers[list[I]], matches: Callable[[I], bool]
) -> tuple[list[Failed], list[Skipped], list[tuple[Ok[list[I]], list[I]]]]:
    """Rules 1 and 2: the failures (stale absences included), the skips and the matches."""
    failed: list[Failed] = []
    skipped: list[Skipped] = []
    found: list[tuple[Ok[list[I]], list[I]]] = []
    for answer in answers:
        if isinstance(answer, Failed):
            failed.append(answer)
        elif isinstance(answer, Skipped):
            skipped.append(answer)
        elif answer.invalid:
            detail = f"{len(answer.invalid)} invalid: {answer.invalid[0]}"
            if answer.rank is None:
                skipped.append(Skipped(answer.plugin, "invalid-item", detail))
            else:
                error = ConfigError(
                    f"{answer.plugin} returned {detail}",
                    system=answer.plugin,
                    hint=f"upgrade untaped-{answer.plugin}",
                )
                failed.append(Failed(answer.plugin, error, answer.rank, "invalid-item"))
        elif hits := [item for item in answer.value if matches(item)]:
            found.append((answer, hits))
        elif answer.stale is not None:
            failed.append(answer.stale)
    return failed, skipped, found


def _one[I](pair: tuple[Ok[list[I]], list[I]], answers: Answers[list[I]]) -> I:
    ok, hits = pair
    if len(hits) > 1:
        raise Ambiguous(
            f"{len(hits)} items from {ok.plugin} match; be more specific",
            details={"provider": ok.plugin, "contract": f"{answers.owner}.{answers.contract}"},
        )
    return hits[0]


def convert[R](bridge: Callable[..., R], envelope: PipeEnvelope) -> R:
    """Read a piped record as the owner's model, through the provider whose kind it is.

    ``bridge`` names the contract's bridge method (``RepoSource.to_repo``),
    whose return type is the owner's model. A record of the owner's own kind
    validates directly, its ``source`` as given; no provider is asked. Any
    other kind goes to the provider whose ``T`` is the record's model or its
    nearest base (the most derived wins), is validated as that model and runs
    through the provider's bridge. A record without a kind, or of a kind no
    provider reads, is a :class:`UsageError` (exit 2).
    """
    info, method = method_contract(bridge)
    if not method.bridge:
        raise TypeError(f"{info.cls.__qualname__}.{method.name} is not a @bridge method")
    model = method.hints["return"]
    owner_kind = kind_of(model)
    kind = envelope.kind
    if kind is None:
        raise UsageError(f"line {envelope.lineno}: a piped record needs a kind to be read")
    if kind == owner_kind:
        return _read(model, envelope)  # type: ignore[no-any-return]  # R is the owner's model
    given = record_model(kind)
    provider = None if given is None else _reader(info, given)
    if provider is None or given is None:
        raise UsageError(
            f"line {envelope.lineno}: no installed provider reads {kind} records as {owner_kind}"
        )
    item = _read(given, envelope)
    plugin = provider.plugin
    try:
        result = getattr(provider.instance, method.name)(item)
        return issue(provider.binding, model, result)  # type: ignore[no-any-return]  # R
    except _Invalid as exc:
        raise ConfigError(str(exc), system=plugin, hint=f"upgrade untaped-{plugin}") from None
    except ValueError as exc:
        message = first_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
        raise ConfigError(
            f"{plugin} turned a {kind} record into an invalid {owner_kind}: {message}",
            system=plugin,
            hint=f"upgrade untaped-{plugin}",
        ) from None


def _read(model: type[Any], envelope: PipeEnvelope) -> Any:
    try:
        return model.model_validate_json(json.dumps(envelope.record), strict=True)
    except ValidationError as exc:
        raise ConfigError(
            f"line {envelope.lineno}: invalid {envelope.kind} record: "
            f"{first_validation_error(exc)}",
            category="invalid",
        ) from None


def _reader(info: ContractInfo, given: type[Any]) -> Provider | None:
    readers = [
        entry
        for entry in offers(info)
        if isinstance(entry, Provider)
        and not entry.binding.owns_item
        and entry.binding.item is not None
        and issubclass(given, entry.binding.item)
    ]
    nearest = [
        entry
        for entry in readers
        if not any(
            other.binding.item is not entry.binding.item
            and other.binding.item is not None
            and entry.binding.item is not None
            and issubclass(other.binding.item, entry.binding.item)
            for other in readers
        )
    ]
    if len(nearest) > 1:
        plugins = ", ".join(entry.plugin for entry in nearest)
        raise ConfigError(
            f"{plugins} all read {given.__qualname__} records [duplicate-kind]",
            details={"providers": [entry.plugin for entry in nearest]},
        )
    return nearest[0] if nearest else None
