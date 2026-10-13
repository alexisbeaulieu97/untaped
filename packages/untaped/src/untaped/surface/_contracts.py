"""``contract`` lines: each owned contract, its methods and the payload models they reach.

Contracts are implementer-shaped: a provider's signature must match the
contract's exactly, so the owner line names every abstract method and each
method's line carries its full typed parameter list and return type. Any
change to either removes a line.

Every pydantic model reachable from a contract method's parameters or return
type is a payload model, named ``<owner>.<ClassName>``. Providers construct
them, so each gets an owner line with its required fields (a new required
field fails), then one typed line per field, constraints included, with a
``choice=`` line per Literal value. A model is stable when a stable contract
or a stable SDK export reaches it, and takes the mark of what reaches it
otherwise.
"""

from __future__ import annotations

import inspect
import types
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from untaped.stability import function_mark
from untaped.surface._lines import Line, MarkName
from untaped.surface._sdk import SurfaceError, mark_name
from untaped.surface._types import (
    choice_text,
    default_text,
    factory_default,
    generic_names,
    node_token,
    schema_of,
    type_token,
)

if TYPE_CHECKING:
    from untaped.contracts._declare import ContractInfo
    from untaped.plugins.registry import PluginSpec


def owned_contracts(spec: PluginSpec) -> list[ContractInfo]:
    """The contracts ``spec`` declares, as the SDK knows them."""
    from untaped.contracts._declare import contract_of  # noqa: PLC0415
    from untaped.plugins.registry import owns_contracts  # noqa: PLC0415

    if not owns_contracts(spec):
        return []
    infos = []
    for cls in spec.contracts():
        info = contract_of(cls)
        if info is None or info.cls is not cls:
            raise SurfaceError(f"plugin {spec.name!r} lists {cls!r}, which is not a contract")
        infos.append(info)
    return sorted(infos, key=lambda info: info.name)


def contract_lines(
    owner: str,
    infos: Iterable[ContractInfo],
    plugin_mark: MarkName | None,
    stable_classes: set[type],
) -> list[Line]:
    """The ``contract`` lines of ``owner``'s contracts and their payload models."""
    lines: list[Line] = []
    reached: dict[type[BaseModel], set[MarkName | None]] = {}
    for info in infos:
        contract_mark = mark_name(function_mark(info.cls)) or plugin_mark
        subject = f"contract {owner}.{info.name}"
        abstract = ",".join(sorted(getattr(info.cls, "__abstractmethods__", ())))
        lines.append(Line(f"{subject} abstract={abstract}", contract_mark))
        for name, method in sorted(info.methods.items()):
            mark = method_mark(info, name, plugin_mark)
            hints = {key: _substitute(hint, info) for key, hint in method.hints.items()}
            lines.append(Line(f"{subject}.{name}{_signature(method.function, hints)}", mark))
            for hint in hints.values():
                for model in _models(hint):
                    reached.setdefault(model, set()).add(mark)
    names: dict[str, type[BaseModel]] = {}
    for model in _closure(reached):
        clash = names.setdefault(model.__name__, model)
        if clash is not model:
            raise SurfaceError(
                f"{owner}: two payload models are named {model.__name__} "
                f"({clash.__module__} and {model.__module__}); rename one"
            )
    for model in sorted(names.values(), key=lambda m: m.__name__):
        marks = _reaching(model, reached)
        model_mark = _model_mark(marks, model in stable_classes)
        lines.extend(model_lines(f"contract {owner}.{model.__name__}", model, model_mark))
    return lines


def method_mark(info: ContractInfo, name: str, plugin_mark: MarkName | None) -> MarkName | None:
    """A contract method's mark: its own, else its contract's, else its plugin's."""
    from untaped.contracts._schema import method_stability  # noqa: PLC0415

    stability = method_stability(info, name)
    return (None if stability == "stable" else stability) or plugin_mark


def model_lines(subject: str, model: type[BaseModel], mark: MarkName | None) -> list[Line]:
    """A payload model's owner line (its required fields) and one typed line per field."""
    schema = schema_of(model)
    properties: Mapping[str, Any] = schema.get("properties", {})
    required = sorted(schema.get("required", ()))
    lines = [Line(f"{subject}({', '.join(required)})", mark)]
    by_alias = {
        (info.validation_alias if isinstance(info.validation_alias, str) else None)
        or info.alias
        or name: info
        for name, info in model.model_fields.items()
    }
    defs = generic_names(schema.get("$defs", {}), model)
    for key in sorted(properties):
        token = node_token(properties[key], defs)
        text = f"{subject}.{key} {token.token}"
        if key not in required:
            info = by_alias.get(key)
            text += f" default={'None' if info is None else _field_default(info)}"
        lines.append(Line(text, mark))
        lines.extend(
            Line(f"{subject}.{key} choice={choice_text(value)}", mark) for value in token.choices
        )
    return lines


def _field_default(info: Any) -> str:
    if info.default is not PydanticUndefined:
        return default_text(info.default)
    if info.default_factory is not None:
        return default_text(factory_default(info.default_factory))
    return "None"


def _signature(function: Any, hints: Mapping[str, Any]) -> str:
    parameters = list(inspect.signature(function).parameters.values())[1:]
    parts: list[str] = []
    star = False
    for parameter in parameters:
        if parameter.kind is parameter.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        text = f"{parameter.name}: {type_token(hints[parameter.name]).token}"
        if parameter.default is not parameter.empty:
            text += f" = {default_text(parameter.default)}"
        parts.append(text)
    return f"({', '.join(parts)}) -> {type_token(hints['return']).token}"


def _substitute(hint: Any, info: ContractInfo) -> Any:
    from untaped.contracts._cache import substitute  # noqa: PLC0415

    return substitute(hint, info.item_param, info.item)


def _models(annotation: object) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in get_args(annotation):
        if not isinstance(arg, list | types.EllipsisType):
            yield from _models(arg)


def _closure(reached: dict[type[BaseModel], set[MarkName | None]]) -> list[type[BaseModel]]:
    """Every model reachable from ``reached``'s, through their fields."""
    seen: list[type[BaseModel]] = []
    queue = list(reached)
    while queue:
        model = queue.pop(0)
        if model in seen:
            continue
        seen.append(model)
        for info in model.model_fields.values():
            queue.extend(_models(info.annotation))
    return seen


def _reaching(
    model: type[BaseModel], reached: dict[type[BaseModel], set[MarkName | None]]
) -> set[MarkName | None]:
    """The marks of the contract methods that reach ``model``, through any chain of fields."""
    marks: set[MarkName | None] = set()
    for root, root_marks in reached.items():
        if model in _closure({root: root_marks}):
            marks |= root_marks
    return marks


def _model_mark(marks: set[MarkName | None], stable_export: bool) -> MarkName | None:
    if stable_export or None in marks:
        return None
    if "deprecated" in marks:
        return "deprecated"
    return "experimental"
