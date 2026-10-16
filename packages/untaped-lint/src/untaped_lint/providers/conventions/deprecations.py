"""Deprecation lint: a deprecated SDK callable or parameter warns where its mark says.

An AST scan over a plugin's source flags:

- ``mark-on-overload`` — untaped's ``@deprecated(...)`` or ``@experimental``
  on an ``@overload`` item, which only ``typing.get_overloads`` sees: nothing
  warns and the surface walker never reads it. Mark the implementation
  (``warnings.deprecated`` on an overload is Python's own and fine);
- ``unwarned-parameter`` — a parameter whose ``Annotated`` carries
  ``deprecated(...)`` on a function without ``@deprecated_parameters``, so
  passing it never warns. A command's flag is the root's to warn about, so a
  parameter whose ``Annotated`` also holds cyclopts' ``Parameter(...)`` is not
  this rule's.

untaped's marks are told from Python's ``warnings.deprecated`` by their
arguments: they take keywords only, Python's takes the message first.
``# untaped: allow <rule>`` on the ``def`` line waives one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path

from untaped.testing import allows
from untaped_lint.infrastructure.files import SourceFile


def deprecation_violations(source_dir: Path, files: Sequence[SourceFile]) -> list[str]:
    """Violations in ``files`` (code in ``source_dir``); paths are relative to its parent."""
    found: list[str] = []
    for source in files:
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for rule, detail in _violations(node):
                if not allows(source.lines, node.lineno, rule):
                    found.append(f"{rel}:{node.lineno}::{rule}::{detail}")
    return found


def _violations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[tuple[str, str]]:
    names = {_name(decorator) for decorator in node.decorator_list}
    if "overload" in names and any(_is_mark(item) for item in node.decorator_list):
        yield "mark-on-overload", f"{node.name}: mark the implementation, not an overload"
    if "deprecated_parameters" in names:
        return
    marked = [
        argument.arg
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if argument.annotation is not None and _marked(argument.annotation)
    ]
    if marked:
        listed = ", ".join(f"{name}=" for name in marked)
        yield (
            "unwarned-parameter",
            f"{node.name}({listed}) is marked deprecated but nothing warns when it is passed; "
            "add @deprecated_parameters",
        )


def _name(node: ast.expr) -> str | None:
    """The last name of a decorator or callee: ``overload``, ``deprecated``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


def _is_mark(node: ast.expr) -> bool:
    """untaped's ``deprecated(...)`` (keywords only) or ``experimental``."""
    if isinstance(node, ast.Call):
        return _name(node) == "deprecated" and not node.args
    return _name(node) == "experimental"


def _marked(annotation: ast.expr) -> bool:
    """An ``Annotated[...]`` (a union's member too) with ``deprecated(...)``, no ``Parameter``."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    for node in _union_members(annotation):
        if not (
            isinstance(node, ast.Subscript)
            and _name(node.value) == "Annotated"
            and isinstance(node.slice, ast.Tuple)
        ):
            continue
        calls = [item for item in node.slice.elts[1:] if isinstance(item, ast.Call)]
        if any(_is_mark(item) for item in calls) and not any(
            _name(item) == "Parameter" for item in calls
        ):
            return True
    return False


def _union_members(node: ast.expr) -> Iterator[ast.expr]:
    """``node``, or each member of the union it spells (``X | None``, ``Optional[X]``)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from _union_members(node.left)
        yield from _union_members(node.right)
    elif isinstance(node, ast.Subscript) and _name(node.value) in ("Optional", "Union"):
        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        for member in members:
            yield from _union_members(member)
    else:
        yield node
