"""Deprecated parameters: a mark on one parameter of a Python API.

:func:`deprecated_parameters` warns when a marked parameter is passed.
"""

from __future__ import annotations

import annotationlib
import ast
import functools
import inspect
import types
import typing
import warnings
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from untaped.sdk._replacement import sdk_replacement_text
from untaped.sdk.marks import (
    Deprecated,
    UntapedDeprecationWarning,
    descriptor_function,
    owner,
    removal_text,
)
from untaped.sdk.words import (
    deprecated_message,
)

_PLUGIN_PACKAGE = "untaped_"


_PARAMETERS_ATTR = "__untaped_deprecated_parameters__"


def callable_owner(obj: object) -> str | None:
    """The distribution whose major a deprecated SDK callable names; ``None`` for core's.

    Read from the ``untaped_<name>`` package of its ``__module__``, the rule
    every plugin's package follows: the composed plugin's distribution, else
    ``untaped-<name>`` (every plugin's distribution is named after it). No
    package metadata is read.
    """
    package = str(getattr(obj, "__module__", "") or "").partition(".")[0]
    if not package.startswith(_PLUGIN_PACKAGE):
        return None
    name = package.removeprefix(_PLUGIN_PACKAGE).replace("_", "-")
    return owner(name) or f"untaped-{name}"


def deprecation_sentence(obj: object, mark: Deprecated, *, subject: str | None = None) -> str:
    """The deprecation sentence of SDK callable ``obj``: what its ``warnings.deprecated`` says.

    ``<qualname> is deprecated and will be removed in <version>[; use <replacement>]``;
    ``subject`` replaces the qualname (a parameter's ``fetch(retries=)``).
    """
    name = subject if subject is not None else str(getattr(obj, "__qualname__", obj))
    removal = removal_text(mark.removed_in, callable_owner(obj))
    return deprecated_message(name, sdk_replacement_text(mark), removal)


def deprecated_parameters[F: Callable[..., Any]](function: F, /) -> F:
    """Warn whenever a parameter marked ``deprecated(...)`` in its annotation is passed.

    ``def emit(rows, *, kind: Annotated[str | None, deprecated(removed_in=13,
    replacement="kind= on the Record class")] = None)`` under
    ``@deprecated_parameters`` raises an :class:`UntapedDeprecationWarning`
    each time ``kind`` is passed, its default value included, since the major
    that removes it makes any spelling of it a ``TypeError``. The mark on the
    parameter is the declaration (its surface line shows it); the decorator
    is what warns. Signatures see through it, and an ``async def`` stays one.

    First consumer: the 12 to 13 transition.
    """
    if isinstance(function, classmethod | staticmethod | property):
        raise TypeError(
            f"put @deprecated_parameters under @{type(function).__name__}, on the function"
        )
    marked: dict[str, Deprecated] | None = None
    signature: inspect.Signature | None = None
    by_keyword_only = False

    def warn_for(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        nonlocal marked, signature, by_keyword_only
        if marked is None:
            signature = inspect.signature(
                function, annotation_format=annotationlib.Format.FORWARDREF
            )
            marked = parameter_marks(function)
            keyword_only = inspect.Parameter.KEYWORD_ONLY
            by_keyword_only = all(signature.parameters[n].kind is keyword_only for n in marked)
        if not marked or (by_keyword_only and not kwargs.keys() & marked.keys()):
            return
        try:
            bound = typing.cast("inspect.Signature", signature).bind_partial(*args, **kwargs)
        except TypeError:
            return  # the call itself raises, in Python's words
        for name in marked.keys() & bound.arguments.keys():
            mark = marked[name]
            subject = f"{function.__qualname__}({name}=)"
            shown = sdk_replacement_text(mark)
            warning = UntapedDeprecationWarning(
                deprecation_sentence(function, mark, subject=subject),
                id=f"sdk {subject}",
                removed_in=mark.removed_in,
                distribution=callable_owner(function),
                replacement=None if shown is None else shown.replace("`", ""),
            )
            warnings.warn(warning, stacklevel=3)  # untaped: allow warnings-warn

    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def awaiting(*args: Any, **kwargs: Any) -> Any:
            warn_for(args, kwargs)
            return await function(*args, **kwargs)

        wrapper: Callable[..., Any] = awaiting
    else:

        @functools.wraps(function)
        def calling(*args: Any, **kwargs: Any) -> Any:
            warn_for(args, kwargs)
            return function(*args, **kwargs)

        wrapper = calling
    setattr(wrapper, _PARAMETERS_ATTR, True)
    return typing.cast("F", wrapper)


def warns_parameters(function: object) -> bool:
    """Whether ``function`` (or a descriptor's) is under :func:`deprecated_parameters`."""
    return bool(getattr(descriptor_function(function), _PARAMETERS_ATTR, False))


def parameter_marks(function: Callable[..., Any]) -> dict[str, Deprecated]:
    """Each parameter of ``function`` whose annotation carries a ``deprecated(...)`` mark.

    Reads the annotations without evaluating them (``annotationlib``,
    ``FORWARDREF``) and evaluates only a string annotation's ``Annotated``
    metadata, so a hint imported under ``TYPE_CHECKING`` never raises. The
    ``Annotated`` may sit inside a union (``Annotated[bool, deprecated(...)] | None``).
    """
    return read_parameter_marks(function)[0]


def read_parameter_marks(
    function: Callable[..., Any],
) -> tuple[dict[str, Deprecated], dict[str, str]]:
    """:func:`parameter_marks`, and each parameter whose mark could not be evaluated, why."""
    target = inspect.unwrap(function)
    try:
        found = annotationlib.get_annotations(target, format=annotationlib.Format.FORWARDREF)
    except TypeError:
        return {}, {}
    namespace = getattr(target, "__globals__", {})
    marks: dict[str, Deprecated] = {}
    failures: dict[str, str] = {}
    for name, annotation in found.items():
        if name == "return":
            continue
        problems: list[str] = []
        mark = next(
            (
                item
                for item in _metadata(annotation, namespace, problems)
                if isinstance(item, Deprecated)
            ),
            None,
        )
        # Read as FORWARDREF, a name the module lacks comes back unresolved, not as an error.
        unresolved: object = None if mark is None else mark.replacement
        if isinstance(unresolved, annotationlib.ForwardRef):
            problems.append(f"replacement={unresolved.__forward_arg__} is not defined")
            mark = None
        if mark is not None:
            marks[name] = mark
        elif problems:
            failures[name] = problems[0]
    return marks, failures


def _metadata(
    annotation: object, namespace: Mapping[str, Any], problems: list[str]
) -> list[object]:
    """The ``Annotated`` metadata in ``annotation`` (a type or its source text), a union's too.

    A string's ``deprecated(...)`` that fails to evaluate adds its error to ``problems``.
    """
    if isinstance(annotation, annotationlib.ForwardRef):
        annotation = annotation.__forward_arg__
    if not isinstance(annotation, str):
        metadata = list(getattr(annotation, "__metadata__", ()))
        if not metadata and isinstance(annotation, types.UnionType):
            for argument in typing.get_args(annotation):
                metadata.extend(_metadata(argument, namespace, problems))
        return metadata
    return _text_metadata(annotation, namespace, problems) if "deprecated" in annotation else []


def _text_metadata(
    annotation: str, namespace: Mapping[str, Any], problems: list[str]
) -> list[object]:
    """The evaluated ``Annotated`` metadata of an annotation's source text."""
    try:
        tree = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return []
    found: list[object] = []
    for node in _union_members(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Tuple)):
            continue
        if _last_name(node.value) != "Annotated":
            continue
        for element in node.slice.elts[1:]:
            try:
                found.append(
                    eval(compile(ast.Expression(element), "<annotation>", "eval"), dict(namespace))
                )
            except Exception as exc:  # reported by the checks, never at a call
                if isinstance(element, ast.Call) and _last_name(element.func) == "deprecated":
                    problems.append(f"{ast.unparse(element)}: {type(exc).__name__}: {exc}")
    return found


def _union_members(node: ast.expr) -> Iterator[ast.expr]:
    """``node``, or each member of the union it spells (``X | None``, ``Optional[X]``)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from _union_members(node.left)
        yield from _union_members(node.right)
    elif isinstance(node, ast.Subscript) and _last_name(node.value) in ("Optional", "Union"):
        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        for member in members:
            yield from _union_members(member)
    else:
        yield node


def _last_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None
