"""A type checker accepts each old SDK name, and nothing the module doesn't keep.

An old name (:func:`untaped.sdk.marks.old_names`) resolves at run time through
the module's ``__getattr__``, which a type checker never sees: it reads the
module's ``TYPE_CHECKING`` imports instead. So each entry of a module's table
is imported there by name, as the object it resolves to
(``from <home> import <new> as <old>``), and nothing outside ``__all__`` and
the table is.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import types

import pytest

from untaped.core.surface import SDK_MODULES
from untaped.sdk import size_text
from untaped.sdk._lazy import OLD_NAMES_ATTR, old_names_of
from untaped.sdk.marks import deprecated, old_names


def _typed_imports(source: str) -> dict[str, tuple[str, str]]:
    """Each name a module-level ``TYPE_CHECKING`` block imports by name: bound → (home, name)."""
    found: dict[str, tuple[str, str]] = {}
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.If) and _is_type_checking(node.test)):
            continue
        for statement in (each for top in node.body for each in ast.walk(top)):
            if isinstance(statement, ast.ImportFrom) and statement.module is not None:
                for alias in statement.names:
                    if alias.name not in ("*", "__all__"):
                        found[alias.asname or alias.name] = (statement.module, alias.name)
    return found


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def typing_problems(module: types.ModuleType, source: str) -> list[str]:
    """Each way ``module``'s ``TYPE_CHECKING`` imports and its old-names table disagree."""
    path = module.__name__
    table = old_names_of(module)
    typed = {
        bound: home
        for bound, home in _typed_imports(source).items()
        if bound not in getattr(module, "__all__", ())
    }
    problems = [
        f"{path}.{name}: an old name a type checker rejects; import its replacement as {name} "
        "in the TYPE_CHECKING block"
        for name in sorted(set(table) - set(typed))
    ]
    problems.extend(
        f"{path}.{name}: imported under TYPE_CHECKING but neither in __all__ nor an old name"
        for name in sorted(set(typed) - set(table))
    )
    for name in sorted(set(typed) & set(table)):
        home, imported = typed[name]
        if getattr(importlib.import_module(home), imported, None) is not table[name].replacement:
            problems.append(
                f"{path}.{name}: the TYPE_CHECKING import is {home}.{imported}, not the object "
                "the old name resolves to"
            )
    return problems


@pytest.mark.parametrize("path", SDK_MODULES)
def test_each_old_name_types_as_its_replacement(path: str) -> None:
    module = importlib.import_module(path)
    if "__getattr__" not in vars(module):
        assert old_names_of(module) == {}, f"{path}: only a lazy module can keep old names"
        return
    assert typing_problems(module, inspect.getsource(module)) == []


# --- the check, both ways, over a sample ---------------------------------------------------

_TYPED = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.sdk.words import plural
    from untaped.sdk.words import size_text as size_words
"""


def _sample(**old: object) -> types.ModuleType:
    module = types.ModuleType("untaped.sdk.sample")
    module.__all__ = ["plural"]  # type: ignore[attr-defined]
    table = old_names(**old)  # type: ignore[arg-type]
    setattr(module, OLD_NAMES_ATTR, lambda: table)
    return module


def test_a_typed_old_name_passes() -> None:
    module = _sample(size_words=deprecated(removed_in=14, replacement=size_text))
    assert typing_problems(module, _TYPED) == []


def test_an_old_name_the_type_checker_cannot_see_fails() -> None:
    module = _sample(
        size_words=deprecated(removed_in=14, replacement=size_text),
        size_str=deprecated(removed_in=14, replacement=size_text),
    )
    assert typing_problems(module, _TYPED) == [
        "untaped.sdk.sample.size_str: an old name a type checker rejects; import its "
        "replacement as size_str in the TYPE_CHECKING block"
    ]


def test_a_typed_name_the_module_does_not_keep_fails() -> None:
    assert typing_problems(_sample(), _TYPED) == [
        "untaped.sdk.sample.size_words: imported under TYPE_CHECKING but neither in __all__ "
        "nor an old name"
    ]


def test_a_typed_old_name_importing_another_object_fails() -> None:
    from untaped.sdk import plural

    module = _sample(size_words=deprecated(removed_in=14, replacement=plural))
    assert typing_problems(module, _TYPED) == [
        "untaped.sdk.sample.size_words: the TYPE_CHECKING import is "
        "untaped.sdk.words.size_text, not the object the old name resolves to"
    ]
