"""A contract's wire schema: what a provider records the hash of.

``contract_schema`` gives each method's parameters (one object schema) and
return type as JSON Schema, with ``T`` put in as the owner's model, and each
method's stability. ``schema_hash`` is what ``fills.json`` records and
the plugin checks' ``owner-schema-drift`` compares; it never classifies a
change as breaking (``untaped.core.surface`` does that for every surface).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Literal

from pydantic import TypeAdapter

from untaped.sdk.contracts._cache import substitute
from untaped.sdk.contracts._declare import ContractInfo
from untaped.sdk.marks import Deprecated, Experimental, function_mark

#: The ``untaped`` format of a contract schema.
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


def _undocumented(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _undocumented(v) for k, v in schema.items() if k not in _DOCUMENTATION}
    if isinstance(schema, list):
        return [_undocumented(each) for each in schema]
    return schema
