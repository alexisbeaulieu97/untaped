"""A type checker accepts each old SDK name, and nothing the module doesn't keep.

An old name (:func:`untaped.sdk.marks.old_names`) resolves at run time through
the module's ``__getattr__``, which a type checker never sees: it reads the
module's ``TYPE_CHECKING`` block instead. So each entry of a module's table is
assigned there from the current name it resolves to (``old = new``): an
assignment is the module's own name, which a strict checker accepts from
outside, where ``from <home> import new as old`` is not re-exported. Nothing
else outside ``__all__`` is bound there.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from repo.support import REPO_ROOT
from untaped.core.surface import SDK_MODULES
from untaped.sdk import size_text
from untaped.sdk._lazy import OLD_NAMES_ATTR, old_names_of
from untaped.sdk.marks import deprecated, old_names


def _typed_names(source: str) -> tuple[set[str], dict[str, str | None]]:
    """What a module-level ``TYPE_CHECKING`` block binds: names it imports, and ``old = new``.

    An assignment whose value is not a bare name maps to ``None``.
    """
    imported: set[str] = set()
    assigned: dict[str, str | None] = {}
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.If) and _is_type_checking(node.test)):
            continue
        for statement in (each for top in node.body for each in ast.walk(top)):
            if isinstance(statement, ast.ImportFrom):
                imported.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name not in ("*", "__all__")
                )
            elif isinstance(statement, ast.Assign):
                value = statement.value.id if isinstance(statement.value, ast.Name) else None
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = value
    return imported, assigned


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def typing_problems(module: types.ModuleType, source: str) -> list[str]:
    """Each way ``module``'s ``TYPE_CHECKING`` block and its old-names table disagree."""
    path = module.__name__
    table = old_names_of(module)
    current = set(getattr(module, "__all__", ()))
    imported, assigned = _typed_names(source)
    problems = [
        f"{path}.{name}: an old name a type checker rejects; add `{name} = <its new name>` "
        "to the TYPE_CHECKING block"
        for name in sorted(set(table) - set(assigned))
    ]
    problems.extend(
        f"{path}.{name}: bound under TYPE_CHECKING but neither in __all__ nor an old name"
        for name in sorted((imported - current) | (set(assigned) - set(table)))
    )
    for name in sorted(set(assigned) & set(table)):
        new = assigned[name]
        if new not in current or getattr(module, new) is not table[name].replacement:
            problems.append(
                f"{path}.{name}: under TYPE_CHECKING it is {new}, not the current name of the "
                "object it resolves to"
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
    from untaped.sdk.words import plural, size_text

    size_words = size_text
"""


def _sample(**old: object) -> types.ModuleType:
    module = types.ModuleType("untaped.sdk.sample")
    module.__all__ = ["plural", "size_text"]  # type: ignore[attr-defined]
    module.size_text = size_text  # type: ignore[attr-defined]
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
        "untaped.sdk.sample.size_str: an old name a type checker rejects; add "
        "`size_str = <its new name>` to the TYPE_CHECKING block"
    ]


def test_a_typed_name_the_module_does_not_keep_fails() -> None:
    aliased = _TYPED + "    from untaped.sdk.words import size_text as size_str\n"
    assert typing_problems(_sample(), aliased) == [
        "untaped.sdk.sample.size_str: bound under TYPE_CHECKING but neither in __all__ nor an "
        "old name",
        "untaped.sdk.sample.size_words: bound under TYPE_CHECKING but neither in __all__ nor an "
        "old name",
    ]


def test_a_typed_old_name_spelling_another_object_fails() -> None:
    from untaped.sdk import plural

    module = _sample(size_words=deprecated(removed_in=14, replacement=plural))
    assert typing_problems(module, _TYPED) == [
        "untaped.sdk.sample.size_words: under TYPE_CHECKING it is size_text, not the current "
        "name of the object it resolves to"
    ]


# --- what a strict type checker makes of each form ------------------------------------------

_PACKAGE = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from {name}._words import size_text
{old}

__all__ = ["size_text"]
"""


def test_a_strict_checker_accepts_the_assignment_and_rejects_an_aliased_import(
    tmp_path: Path,
) -> None:
    """The form the check asks for is the one mypy (strict, as this repo runs it) accepts."""
    for name, old in [
        ("assigned", "    size_words = size_text"),
        ("aliased", "    from aliased._words import size_text as size_words"),
    ]:
        package = tmp_path / name
        package.mkdir()
        (package / "_words.py").write_text("def size_text(n: int) -> str:\n    return str(n)\n")
        (package / "__init__.py").write_text(_PACKAGE.format(name=name, old=old))
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "from assigned import size_words\n"
        "from aliased import size_words as other\n"
        "reveal_type(size_words)\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(plugin),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "MYPYPATH": str(tmp_path)},
    )
    errors = [line for line in proc.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 1, proc.stdout
    assert errors[0].startswith(f"{plugin}:2:"), proc.stdout
    assert 'does not explicitly export attribute "size_words"' in errors[0], proc.stdout
    assert 'Revealed type is "def (n: int) -> str"' in proc.stdout, proc.stdout
