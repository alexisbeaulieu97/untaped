"""Proving a provider fills its contract: the checks behind ``assert_fills``.

For each sample of the provider's own record type ``T``: it survives a JSON
round trip through ``T``; it issues as the owner's model the way the SDK
issues every answer; and each bridge (the method taking ``T`` and returning
the owner's model) turns it into a valid owner record whose ``source`` names
the provider and carries the sample back.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from untaped.runtime.contracts.registry import BoundProvider
from untaped.sdk.contracts._cache import return_type
from untaped.sdk.contracts._declare import Binding, fills, record_kind
from untaped.sdk.contracts._gather import issue
from untaped.sdk.errors import first_validation_error
from untaped.sdk.records import Issued, Record


def fills_problems(provider: BoundProvider, samples: Sequence[object]) -> list[str]:
    """What breaks when ``provider`` handles each sample of its ``T``; empty when nothing does."""
    binding = provider.binding
    item = binding.item
    if item is None:
        return []
    problems: list[str] = []
    for index, sample in enumerate(samples, 1):
        label = f"sample {index}"
        try:
            record = sample if isinstance(sample, item) else item.model_validate(sample)
        except ValidationError as exc:
            problems.append(f"{label} is not a {item.__qualname__}: {first_validation_error(exc)}")
            continue
        problems += _round_trip(item, record, label)
        if binding.owns_item:
            problems += _issues(binding, record, label)
        else:
            problems += _bridges(provider, record, label)
    return problems


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


def _bridges(provider: BoundProvider, record: Record, label: str) -> list[str]:
    binding = provider.binding
    info = binding.contract
    problems: list[str] = []
    for name, method in info.methods.items():
        if not method.bridge or not fills(type(provider.instance), info, name):
            continue
        call = f"{name}({label})"
        try:
            result = getattr(provider.instance, name)(record)
            issued = _issued(binding, name, result)
        except Exception as exc:
            problems.append(f"{call}: {_message(exc)}")
            continue
        source = getattr(issued, "source", None)
        if not isinstance(issued, Issued) or source is None:
            continue
        if source.plugin != binding.plugin or source.kind != (record_kind(type(record)) or ""):
            problems.append(f"{call}: source names {source.plugin} {source.kind}")
            continue
        try:
            again = type(record).model_validate_json(json.dumps(source.record), strict=True)
        except ValidationError as exc:
            problems.append(f"{call}: source.record doesn't read back: {_message(exc)}")
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
