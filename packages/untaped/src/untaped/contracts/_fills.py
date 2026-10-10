"""Proving a provider fills its contract: the checks behind ``assert_fills`` and ``plugin check``.

For each sample of the provider's own record type ``T``: it survives a JSON
round trip through ``T``; it issues as the owner's model the way the SDK
issues every answer; and each ``@bridge`` method turns it into a valid owner
record whose ``source`` names the provider and carries the sample back.

The owner's schema hash the provider was checked against lives in the
plugin package's ``fills.json`` (``{"untaped": "1", "fills": {"<owner>.<contract>":
"<sha256>"}}``), so ``plugin check`` and doctor can say when the installed owner
has changed since (``owner-schema-drift``).
"""

from __future__ import annotations

import json
import sys
import sysconfig
from collections.abc import Sequence
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from untaped.contracts._cache import return_type
from untaped.contracts._declare import Binding, Issued, fills
from untaped.contracts._gather import issue
from untaped.contracts._registry import Provider
from untaped.contracts._schema import schema_hash
from untaped.errors import first_validation_error
from untaped.fs import atomic_write
from untaped.records import Record, kind_of

#: The file in a provider's plugin package holding the owner schema hashes it was checked against.
FILLS_FILE = "fills.json"
_FORMAT = "1"


def fills_key(binding: Binding) -> str:
    """``<owner>.<contract>``: the key of a provider's hash in ``fills.json``."""
    return f"{binding.owner}.{binding.contract.name}"


def fills_problems(provider: Provider, samples: Sequence[object]) -> list[str]:
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


def _bridges(provider: Provider, record: Record, label: str) -> list[str]:
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
        if source.plugin != binding.plugin or source.kind != (kind_of(type(record)) or ""):
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


def fills_path(provider: type) -> Path | None:
    """``fills.json`` in the package defining ``provider``; ``None`` outside a package."""
    module = sys.modules.get(provider.__module__)
    top = (getattr(module, "__name__", None) or provider.__module__).partition(".")[0]
    found = find_spec(top)
    if found is None or not found.submodule_search_locations:
        return None
    return Path(next(iter(found.submodule_search_locations))) / FILLS_FILE


def recorded_hash(provider: Provider) -> str | None:
    """The owner schema hash recorded for ``provider``'s contract, if any."""
    path = fills_path(type(provider.instance))
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    recorded = raw.get("fills") if isinstance(raw, dict) else None
    value = recorded.get(fills_key(provider.binding)) if isinstance(recorded, dict) else None
    return value if isinstance(value, str) else None


def current_hash(provider: Provider) -> str:
    """The installed owner's schema hash for ``provider``'s contract."""
    return schema_hash(provider.binding.contract)


def record_hash(provider: Provider) -> Path | None:
    """Write the installed owner's schema hash into ``fills.json``; the file when it changed.

    Only a package outside the interpreter's site-packages (a checkout, an
    editable install) is written: an installed wheel keeps the file it
    shipped, so a stale committed hash still shows as ``owner-schema-drift``.
    The write is atomic, so a parallel reader never sees half a file.
    """
    path = fills_path(type(provider.instance))
    if path is None or _installed(path):
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        raw = {}
    recorded = raw.get("fills") if isinstance(raw, dict) else None
    entries = dict(recorded) if isinstance(recorded, dict) else {}
    key, value = fills_key(provider.binding), current_hash(provider)
    if entries.get(key) == value:
        return None
    entries[key] = value
    body = {"untaped": _FORMAT, "fills": dict(sorted(entries.items()))}
    atomic_write(path, json.dumps(body, indent=2) + "\n")
    return path


def _installed(path: Path) -> bool:
    paths = sysconfig.get_paths()
    where = path.resolve()
    return any(where.is_relative_to(Path(paths[key]).resolve()) for key in ("purelib", "platlib"))
