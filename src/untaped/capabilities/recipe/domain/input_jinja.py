"""Sandboxed Jinja input derivation adapter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from untaped.capabilities.recipe.errors import RecipeError

if TYPE_CHECKING:
    from jinja2 import Environment, Template

MAX_DERIVED_VALUE_LENGTH = 8192
MAX_DERIVED_VALUE_DEPTH = 64
UNRESOLVED = object()


@dataclass(frozen=True)
class CompiledInputSource:
    """One ordered set of compiled input source candidates."""

    templates: tuple[Template, ...]


class InputSourceError(RecipeError, ValueError):
    """Raised when an input source cannot be compiled or rendered safely."""


def compile_input_source(candidates: tuple[str, ...]) -> CompiledInputSource:
    """Compile an ordered set of Jinja source candidates."""
    try:
        return CompiledInputSource(
            templates=tuple(_compile_template(candidate) for candidate in candidates)
        )
    except InputSourceError:
        raise
    except Exception as exc:
        raise InputSourceError(_error_message(exc)) from exc


def derive_input_value(
    source: CompiledInputSource,
    *,
    context: Mapping[str, object],
) -> object:
    """Return the first resolved candidate value or UNRESOLVED."""
    from jinja2 import Undefined  # noqa: PLC0415
    from jinja2.exceptions import TemplateError, UndefinedError  # noqa: PLC0415

    for template in source.templates:
        try:
            value: object = template.render(**context)
        except UndefinedError:
            continue
        except (TemplateError, ArithmeticError) as exc:
            message = _error_message(exc)
            raise InputSourceError(f"invalid input source expression: {message}") from exc
        if value is None or isinstance(value, Undefined):
            continue
        ensure_derived_value_within_bound(value, structured=True)
        return value
    return UNRESOLVED


@lru_cache(maxsize=1024)
def _compile_template(expression: str) -> Template:
    env = _jinja_env()
    _validate_template_ast(env, expression)
    return env.from_string(expression)


@lru_cache(maxsize=1)
def _jinja_env() -> Environment:
    from jinja2 import StrictUndefined  # noqa: PLC0415
    from jinja2.nativetypes import NativeCodeGenerator, native_concat  # noqa: PLC0415
    from jinja2.sandbox import SandboxedEnvironment  # noqa: PLC0415

    # No operator or call reaches evaluation: the AST allowlist in
    # _validate_template_ast admits only literals and field access.
    class _NativeSandboxedEnvironment(SandboxedEnvironment):
        code_generator_class = NativeCodeGenerator
        concat = staticmethod(native_concat)  # type: ignore[assignment]

    env = _NativeSandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
    env.globals.clear()
    return env


def _validate_template_ast(env: Environment, expression: str) -> None:
    from jinja2 import nodes  # noqa: PLC0415

    parsed = env.parse(expression)
    if any(not isinstance(node, nodes.Output) for node in parsed.body):
        raise InputSourceError("input source expressions may not use control blocks")
    for node in _walk_nodes(parsed):
        if isinstance(node, nodes.Name) and node.name not in {"target", "record"}:
            raise InputSourceError(_SOURCE_CONTRACT_MESSAGE)
        if not isinstance(
            node,
            (
                nodes.Template,
                nodes.Output,
                nodes.TemplateData,
                nodes.Const,
                nodes.Name,
                nodes.Getattr,
                nodes.Getitem,
            ),
        ):
            raise InputSourceError(_SOURCE_CONTRACT_MESSAGE)


_SOURCE_CONTRACT_MESSAGE = (
    "input source expressions may only use scalar literals and target/record field access"
)


def _walk_nodes(node: object) -> Iterator[object]:
    yield node
    iter_child_nodes = getattr(node, "iter_child_nodes", None)
    if iter_child_nodes is None:
        return
    for child in iter_child_nodes():
        yield from _walk_nodes(child)


def ensure_derived_value_within_bound(value: object, *, structured: bool = False) -> None:
    """Reject non-scalar or oversized derived input values."""
    if not isinstance(value, str | int | float | bool) and not (
        structured and isinstance(value, Mapping | list | tuple)
    ):
        raise InputSourceError("derived input value must be a scalar")
    if _value_size(value, seen=set(), depth=0) > MAX_DERIVED_VALUE_LENGTH:
        raise InputSourceError(
            f"derived input value exceeds maximum length of {MAX_DERIVED_VALUE_LENGTH}"
        )


def _value_size(value: object, *, seen: set[int], depth: int) -> int:
    if depth > MAX_DERIVED_VALUE_DEPTH:
        return MAX_DERIVED_VALUE_LENGTH + 1
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value.bit_length()
    if isinstance(value, float):
        return len(str(value))
    if isinstance(value, str | bytes):
        return len(value)
    if isinstance(value, Mapping):
        value_id = id(value)
        if value_id in seen:
            return 0
        seen.add(value_id)
        total = len(value)
        for key, item in value.items():
            total += _value_size(key, seen=seen, depth=depth + 1)
            total += _value_size(item, seen=seen, depth=depth + 1)
        return total
    if isinstance(value, list | tuple | set | frozenset):
        value_id = id(value)
        if value_id in seen:
            return 0
        seen.add(value_id)
        return len(value) + sum(_value_size(item, seen=seen, depth=depth + 1) for item in value)
    return 0


def _error_message(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return str(message or exc)
