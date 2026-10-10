"""A contract's wire schema: what an owner snapshots and a provider records the hash of.

``contract_schema`` gives each method's parameters (one object schema) and
return type as JSON Schema, with ``T`` put in as the owner's model, and each
method's stability. ``schema_changes`` lists what in a new schema breaks
callers or providers of the old one, following the evolution table in
``docs/contracts.md``: a new method, a new optional parameter or field, and
the removal of an experimental method are compatible; a removed or retyped
field, a new required field or parameter, a changed constraint and the
removal of a stable method are not.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import TypeAdapter

from untaped.contracts._cache import substitute
from untaped.contracts._declare import ContractInfo
from untaped.stability import Deprecated, Experimental, function_mark

#: The ``untaped`` format of a snapshot.
FORMAT = "1"

#: Keys that document a schema without changing what it accepts.
_DOCUMENTATION = frozenset({"title", "description", "examples"})

type Stability = Literal["stable", "experimental", "deprecated"]


def method_stability(info: ContractInfo, name: str) -> Stability:
    """The method's own mark, else its contract's."""
    mark = function_mark(info.methods[name].function) or function_mark(info.cls)
    if isinstance(mark, Deprecated):
        return "deprecated"
    return "experimental" if isinstance(mark, Experimental) else "stable"


def contract_schema(info: ContractInfo) -> dict[str, Any]:
    """Every method's parameters, return type and stability, as JSON Schema."""
    methods: dict[str, Any] = {}
    for name, method in info.methods.items():
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in list(inspect.signature(method.function).parameters.values())[1:]:
            hint = substitute(method.hints[parameter.name], info.item_param, info.item)
            properties[parameter.name] = TypeAdapter(hint).json_schema()
            if parameter.default is parameter.empty:
                required.append(parameter.name)
        returns = substitute(method.hints["return"], info.item_param, info.item)
        methods[name] = {
            "stability": method_stability(info, name),
            "params": {"type": "object", "properties": properties, "required": required},
            "return": TypeAdapter(returns).json_schema(),
        }
    return {"untaped": FORMAT, "contract": info.name, "methods": methods}


def schema_hash(info: ContractInfo) -> str:
    """sha256 of the contract's schema, its documentation and stability left out."""
    methods = {
        name: {"params": _undocumented(each["params"]), "return": _undocumented(each["return"])}
        for name, each in contract_schema(info)["methods"].items()
    }
    canonical = json.dumps(methods, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def schema_changes(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """What in ``new`` breaks a caller or provider of ``old`` (each a :func:`contract_schema`)."""
    before: Mapping[str, Any] = old.get("methods", {})
    after: Mapping[str, Any] = new.get("methods", {})
    changes: list[str] = []
    for name, was in before.items():
        now = after.get(name)
        if now is None:
            if was.get("stability") != "experimental":
                changes.append(f"{name}: a {was.get('stability', 'stable')} method was removed")
            continue
        changes += _changes(was["params"], now["params"], f"{name}()", request=True)
        changes += _changes(was["return"], now["return"], f"{name} returns", request=False)
    return list(dict.fromkeys(changes))


def _changes(old: Any, new: Any, where: str, *, request: bool) -> Iterator[str]:
    old, new = _undocumented(old), _undocumented(new)
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        yield from _object_changes(old, new, where, request=request)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for index, (was, now) in enumerate(zip(old, new, strict=True)):
            yield from _changes(was, now, f"{where}[{index}]", request=request)
    else:
        yield f"{where}: {_short(old)} became {_short(new)}"


def _object_changes(
    old: dict[str, Any], new: dict[str, Any], where: str, *, request: bool
) -> Iterator[str]:
    old_props: dict[str, Any] = old.get("properties", {})
    new_props: dict[str, Any] = new.get("properties", {})
    old_required, new_required = set(old.get("required", ())), set(new.get("required", ()))
    for key, was in old_props.items():
        if key not in new_props:
            yield f"{where}.{key} was removed"
        else:
            yield from _changes(was, new_props[key], f"{where}.{key}", request=request)
    what = "parameter" if request and where.endswith("()") else "field"
    for key in new_props.keys() - old_props.keys():
        if key in new_required:
            yield f"{where}.{key} was added as a required {what}"
    for key in (new_required - old_required) & old_props.keys():
        yield f"{where}.{key} became required"
    old_defs: dict[str, Any] = old.get("$defs", {})
    new_defs: dict[str, Any] = new.get("$defs", {})
    for key in old_defs.keys() & new_defs.keys():
        yield from _changes(old_defs[key], new_defs[key], f"{where} ({key})", request=request)
    # A default changes no shape: a parameter gaining one is still accepted.
    for key in (old.keys() | new.keys()) - {"properties", "required", "$defs", "default"}:
        was, now = old.get(key), new.get(key)
        if was == now:
            continue
        if isinstance(was, dict | list) and isinstance(now, dict | list):
            yield from _changes(was, now, f"{where}.{key}", request=request)
        else:
            yield f"{where}: {key} {_short(was)} became {_short(now)}"


def _undocumented(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _undocumented(v) for k, v in schema.items() if k not in _DOCUMENTATION}
    if isinstance(schema, list):
        return [_undocumented(each) for each in schema]
    return schema


def _short(value: Any) -> str:
    text = json.dumps(value, sort_keys=True) if value is not None else "absent"
    return text if len(text) <= 60 else f"{text[:57]}..."
