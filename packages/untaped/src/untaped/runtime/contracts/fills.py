"""Proving a provider fills its contract: the checks behind ``assert_fills``.

Each sample is a record the provider issues from: its ``T``, or the record
one of its record bridges takes. It survives a JSON round trip through its
model; a ``T`` that is the owner's model issues as the SDK issues every
answer; and each bridge the provider fills that takes the sample's model (the
item bridge for ``T``, a record bridge for the record its fill names) turns it
into a valid issued record whose ``source`` names the provider and reads back
as the sample through ``own``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from untaped.runtime.contracts.registry import BoundProvider
from untaped.sdk.contracts._cache import return_type
from untaped.sdk.contracts._declare import Binding, bridge_takes
from untaped.sdk.contracts._gather import issue
from untaped.sdk.errors import UntapedError, first_validation_error
from untaped.sdk.records import Issued, Record


def fills_problems(provider: BoundProvider, samples: Sequence[object]) -> list[str]:
    """What breaks when ``provider`` handles each sample; empty when nothing does.

    A sample that is a model instance goes to the bridges taking its model; a
    mapping is read as the provider's ``T``.
    """
    binding = provider.binding
    if binding.item is None:
        return []  # a contract without ``T`` issues nothing a provider bridges
    takes = _bridges_taking(provider)
    problems: list[str] = []
    for index, sample in enumerate(samples, 1):
        label = f"sample {index}"
        model = _model_of(sample, binding, takes)
        if model is None:
            known = ", ".join(each.__qualname__ for each in dict.fromkeys(takes.values()))
            problems.append(
                f"{label} is a {type(sample).__qualname__}, which no bridge of "
                f"{type(provider.instance).__qualname__} takes"
                + (f" (it takes {known})" if known else "")
            )
            continue
        try:
            record = sample if isinstance(sample, model) else model.model_validate(sample)
        except ValidationError as exc:
            problems.append(f"{label} is not a {model.__qualname__}: {first_validation_error(exc)}")
            continue
        problems += _round_trip(model, record, label)
        if model is binding.item and binding.owns_item:
            problems += _issues(binding, record, label)
        else:
            names = [name for name, each in takes.items() if issubclass(model, each)]
            problems += _bridges(provider, names, record, label)
    return problems


def _bridges_taking(provider: BoundProvider) -> dict[str, type[Record]]:
    """Each bridge ``provider`` fills, and the record it takes."""
    info = provider.binding.contract
    cls = type(provider.instance)
    found: dict[str, type[Record]] = {}
    for name, method in info.methods.items():
        if method.bridge and (takes := bridge_takes(cls, info, name)) is not None:
            found[name] = takes
    return found


def _model_of(
    sample: object, binding: Binding, takes: dict[str, type[Record]]
) -> type[Record] | None:
    """The record model ``sample`` is checked as: its own when a bridge takes it, else ``T``."""
    if isinstance(sample, Record) and not isinstance(sample, binding.item or Record):
        fitting = [each for each in takes.values() if isinstance(sample, each)]
        return type(sample) if fitting else None
    return binding.item


def _round_trip(model: type[Record], record: Record, label: str) -> list[str]:
    dumped = record.model_dump_json(by_alias=True, round_trip=True)
    try:
        back = model.model_validate_json(dumped, strict=True)
    except ValidationError as exc:
        return [f"{label} doesn't read back from JSON: {first_validation_error(exc)}"]
    if back != record:
        return [f"{label} changes in a JSON round trip through {model.__qualname__}"]
    return []


def _issues(binding: Binding, record: Record, label: str) -> list[str]:
    model = binding.contract.item
    if model is None:
        return []
    try:
        issue(binding, model, record)
    except Exception as exc:
        return [f"{label} doesn't issue as a {model.__qualname__}: {_message(exc)}"]
    return []


def _bridges(provider: BoundProvider, names: list[str], record: Record, label: str) -> list[str]:
    binding = provider.binding
    problems: list[str] = []
    for name in names:
        call = f"{name}({label})"
        try:
            result = getattr(provider.instance, name)(record)
            issued = _issued(binding, name, result)
        except Exception as exc:
            problems.append(f"{call}: {_message(exc)}")
            continue
        if not isinstance(issued, Issued) or issued.source is None:
            continue
        if issued.source.plugin != binding.plugin:
            problems.append(f"{call}: source names {issued.source.plugin}")
            continue
        try:
            again = provider.instance.own(issued, type(record))
        except UntapedError as exc:
            problems.append(f"{call}: source.record doesn't read back: {exc}")
            continue
        if again != record:
            problems.append(f"{call}: source.record differs from the sample")
    return problems


def _issued(binding: Binding, name: str, result: Any) -> Any:
    hint = return_type(binding, name)
    if isinstance(hint, type) and issubclass(hint, Issued):
        return issue(binding, hint, result)
    return TypeAdapter(hint).validate_python(result, strict=True)


def _message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return first_validation_error(exc)
    return f"{type(exc).__name__}: {exc}" if not str(exc) else str(exc)
