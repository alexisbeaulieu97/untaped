"""Setting names and declared renames of a section model.

- ``settings-naming`` — a field breaks a naming rule of
  ``docs/reference/conventions.md#names``: a ``Path`` that does not end in
  ``_dir`` or ``_path``, a number named after ``concurrency``/``workers``/
  ``jobs``/``threads`` instead of ``parallel``, a duration without its unit;
- ``settings-renames`` — ``renamed_keys``/``retired_keys`` break a declaration
  rule (a chain, a collision…), or a stability mark sits where it takes no effect
  (inside a union, on a model or on a state field).

Only profile models and the models their fields reach are checked, never a
state model. Violations are ``<file>:<line>::<rule>::<detail>``;
``# untaped: allow settings-naming`` on a field's line waives that field.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path, PurePath
from textwrap import dedent
from typing import Any

from pydantic import BaseModel

from untaped.config_schema import unwrap_optional
from untaped.conventions.allow import allowed
from untaped.deprecated_keys import mapping_errors
from untaped.stability import mark_errors

NAMING = "settings-naming"
RENAMES = "settings-renames"

_CONCURRENCY = frozenset({"concurrency", "workers", "jobs", "threads"})
_DURATION = frozenset({"timeout", "age", "after", "interval", "ttl", "delay", "expiry"})
_UNITS = frozenset({"seconds", "minutes", "hours", "days", "ms"})


def name_problem(name: str, annotation: Any) -> str | None:
    """Why field ``name`` of type ``annotation`` breaks a naming rule, else ``None``."""
    kind = unwrap_optional(annotation)
    tokens = name.split("_")
    if (
        isinstance(kind, type)
        and issubclass(kind, PurePath)
        and name != "path"
        and not name.endswith(("_dir", "_path"))
    ):
        return "a path setting ends in _dir or _path"
    if kind in (int, float) and _CONCURRENCY.intersection(tokens):
        return "a concurrency setting is named parallel"
    if _DURATION.intersection(tokens) and tokens[-1] not in _UNITS:
        return "a duration ends in its unit (_seconds, _minutes, _hours, _days, _ms)"
    return None


def settings_name_violations(
    section: str,
    model: type[BaseModel],
    root: Path,
    *,
    state: type[BaseModel] | None = None,
) -> list[str]:
    """Violations of ``model`` (the ``section`` model) and the models it reaches.

    Paths are relative to ``root`` when the model's file is under it. The
    section's ``state`` model, when given, is checked only for stability marks.
    """
    errors = mapping_errors(model)
    where = _class_location(model, root) if errors else ""
    found = [f"{where}::{RENAMES}::{error}" for error in errors]
    if state is not None:
        state_errors = mark_errors(state, state=True)
        state_where = _class_location(state, root) if state_errors else ""
        found.extend(f"{state_where}::{RENAMES}::{error}" for error in state_errors)
    found.extend(_naming_violations(section, model, root))
    return found


def _naming_violations(prefix: str, model: type[BaseModel], root: Path) -> Iterator[str]:
    located = _class_source(model)
    if located is None:
        return
    path, offset, lines, node = located
    for statement in node.body:
        if not (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)):
            continue
        name = statement.target.id
        field = model.model_fields.get(name)
        if field is None:  # a ClassVar or a private attribute
            continue
        key = f"{prefix}.{name}"
        nested = unwrap_optional(field.annotation)
        if isinstance(nested, type) and issubclass(nested, BaseModel):
            yield from _naming_violations(key, nested, root)
            continue
        problem = name_problem(name, field.annotation)
        if problem is not None and not allowed(lines, statement.lineno, NAMING):
            line = offset + statement.lineno
            yield f"{_relative(path, root)}:{line}::{NAMING}::{key}: {problem}"


def _class_location(model: type[BaseModel], root: Path) -> str:
    located = _class_source(model)
    if located is None:
        return f"{model.__module__}.{model.__qualname__}"
    path, offset, _, node = located
    return f"{_relative(path, root)}:{offset + node.lineno}"


def _class_source(model: type) -> tuple[Path, int, list[str], ast.ClassDef] | None:
    """Where ``model`` is defined: file, line offset, the class's lines and node.

    ``None`` for a class without source, such as one made by ``create_model``.
    """
    try:
        path = Path(inspect.getsourcefile(model) or "")
        lines, start = inspect.getsourcelines(model)
    except OSError, TypeError:
        return None
    node = ast.parse(dedent("".join(lines))).body[0]
    assert isinstance(node, ast.ClassDef)
    return path, start - 1, [line.rstrip("\n") for line in lines], node


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
