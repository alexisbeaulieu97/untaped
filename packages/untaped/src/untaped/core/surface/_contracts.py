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
otherwise. A model marked ``@experimental`` itself that a stable contract
method reaches is a :class:`SurfaceError`: stabilising the contract would
promise the model by accident, so either is changed on purpose.
"""

from __future__ import annotations

import inspect
import types
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from untaped.core.surface._lines import DeprecatedMark, Line, LineMark
from untaped.core.surface._sdk import SurfaceError, line_mark
from untaped.core.surface._types import (
    TypeToken,
    choice_text,
    default_text,
    factory_default,
    generic_names,
    node_token,
    schema_of,
    type_token,
)
from untaped.sdk.marks import Experimental, class_mark, function_mark

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin
    from untaped.sdk.contracts._declare import ContractInfo


def owned_contracts(spec: ComposedPlugin) -> list[ContractInfo]:
    """The contracts ``spec`` declares, as the SDK knows them."""
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415

    if not spec.owns_contracts:
        return []
    infos = []
    for cls in spec.offers:
        info = contract_of(cls)
        if info is None or info.cls is not cls:
            raise SurfaceError(f"plugin {spec.name!r} lists {cls!r}, which is not a contract")
        infos.append(info)
    return sorted(infos, key=lambda info: info.name)


def contract_lines(
    owner: str,
    infos: Iterable[ContractInfo],
    plugin_mark: LineMark | None,
    stable_classes: set[type],
) -> list[Line]:
    """The ``contract`` lines of ``owner``'s contracts and their payload models."""
    lines: list[Line] = []
    reached: dict[type[BaseModel], set[LineMark | None]] = {}
    stable_methods: dict[type[BaseModel], set[str]] = {}
    for info in infos:
        contract_mark = line_mark(function_mark(info.cls)) or plugin_mark
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
                    if mark is None:
                        stable_methods.setdefault(model, set()).add(f"{info.name}.{name}")
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
        if None in marks and isinstance(class_mark(model), Experimental):
            methods = sorted(
                method
                for root, found in stable_methods.items()
                if model in _closure({root: set()})
                for method in found
            )
            raise SurfaceError(
                f"{owner}: the stable {', '.join(methods)} reaches {model.__name__}, which is "
                "@experimental; mark the method experimental, or stabilise the model on purpose"
            )
        model_mark = _model_mark(marks, model in stable_classes)
        lines.extend(model_lines(f"contract {owner}.{model.__name__}", model, model_mark))
    return lines


def method_mark(info: ContractInfo, name: str, plugin_mark: LineMark | None) -> LineMark | None:
    """A contract method's mark: its own, else its contract's, else its plugin's."""
    own = function_mark(info.methods[name].function) or function_mark(info.cls)
    return line_mark(own) or plugin_mark


def model_lines(subject: str, model: type[BaseModel], mark: LineMark | None) -> list[Line]:
    """A payload model's owner line (its required fields) and one typed line per field."""
    required, fields = model_fields(model)
    lines = [Line(f"{subject}({', '.join(required)})", mark)]
    for key, token, default in fields:
        text = f"{subject}.{key} {token.token}"
        lines.append(Line(text if default is None else f"{text} default={default}", mark))
        lines.extend(
            Line(f"{subject}.{key} choice={choice_text(value)}", mark) for value in token.choices
        )
    return lines


#: A JSON key, its type, and its default when the field is optional.
type FieldLine = tuple[str, TypeToken, str | None]


def model_fields(model: type[BaseModel]) -> tuple[list[str], list[FieldLine]]:
    """``model``'s required JSON keys, then each key's type and, when optional, its default."""
    schema = schema_of(model)
    # A model that refers to itself is a reference to its own def.
    body: Mapping[str, Any] = schema["$defs"][schema["$ref"]] if "$ref" in schema else schema
    properties: Mapping[str, Any] = body.get("properties", {})
    required = sorted(body.get("required", ()))
    by_alias = {
        (info.validation_alias if isinstance(info.validation_alias, str) else None)
        or info.alias
        or name: info
        for name, info in model.model_fields.items()
    }
    defs = generic_names(schema.get("$defs", {}), model)
    fields: list[FieldLine] = []
    for key in sorted(properties):
        default = None
        if key not in required:
            info = by_alias.get(key)
            default = "None" if info is None else _field_default(info)
        fields.append((key, node_token(properties[key], defs), default))
    return required, fields


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
    from untaped.sdk.contracts._cache import substitute  # noqa: PLC0415

    return substitute(hint, info.item_param, info.item)


def _models(annotation: object) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in get_args(annotation):
        if not isinstance(arg, list | types.EllipsisType):
            yield from _models(arg)


def _closure(reached: dict[type[BaseModel], set[LineMark | None]]) -> list[type[BaseModel]]:
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
    model: type[BaseModel], reached: dict[type[BaseModel], set[LineMark | None]]
) -> set[LineMark | None]:
    """The marks of the contract methods that reach ``model``, through any chain of fields."""
    marks: set[LineMark | None] = set()
    for root, root_marks in reached.items():
        if model in _closure({root: root_marks}):
            marks |= root_marks
    return marks


def _model_mark(marks: set[LineMark | None], stable_export: bool) -> LineMark | None:
    if stable_export or None in marks:
        return None
    deprecations = sorted(
        (mark for mark in marks if isinstance(mark, DeprecatedMark)),
        key=lambda mark: (mark.removed_in is None, mark.removed_in or 0),
    )
    if deprecations:
        last = deprecations[-1]
        uses = {mark.use for mark in deprecations if mark.removed_in == last.removed_in}
        # Reached through several replacements, the model names none of them.
        return last if len(uses) == 1 else DeprecatedMark(last.removed_in)
    return "experimental"
