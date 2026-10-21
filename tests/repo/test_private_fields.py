"""git names no field of another plugin's private file in a store repo.

Each plugin's ``untaped-<plugin>.json`` holds that plugin's data, read and
written through ``RepoStore.read_private``/``write_private`` with its own
model. git decides when the file is created, copied (adopt) and deleted, never
what is in it: adopt once dropped github's ``fetched_at``/``pushed_at`` by
name, so a field github renamed would have kept its freshness through a copy.
So no string in untaped_git's code is a field of a model another first-party
plugin passes to ``read_private``, nor one of github's 11.0 freshness fields.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from repo.support import FIRST_PARTY, PACKAGES

#: Fields 11.0's private files held that no model names any more.
LEGACY = frozenset({"fetched_at", "pushed_at"})
#: Models the scan must find, so a change in how plugins call it can't empty it.
EXPECTED = frozenset({"BackfillMark"})


def _trees(plugin: str) -> dict[Path, ast.Module]:
    return {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted((PACKAGES / f"untaped-{plugin}" / "src").rglob("*.py"))
    }


def _private_models(trees: list[ast.Module]) -> set[str]:
    """The classes passed to ``.read_private(...)`` in ``trees``."""
    return {
        node.args[0].id
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_private"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }


def _fields(trees: list[ast.Module], models: set[str]) -> set[str]:
    return {
        item.target.id
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name in models
        for item in node.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    }


def _strings(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Each string constant in ``tree`` but docstrings: a key, a tuple of keys, an argument."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def test_git_names_no_field_of_another_plugins_private_file() -> None:
    models: set[str] = set()
    fields = set(LEGACY)
    for plugin in FIRST_PARTY:
        if plugin == "git":
            continue
        trees = list(_trees(plugin).values())
        found = _private_models(trees)
        models |= found
        fields |= _fields(trees, found)
    assert models >= EXPECTED, f"the scan found {sorted(models)}, not {sorted(EXPECTED)}"

    named = [
        f"{path.relative_to(PACKAGES)}:{line}: {key!r}"
        for path, tree in _trees("git").items()
        for line, key in _strings(tree)
        if key in fields
    ]
    assert named == [], (
        "git names another plugin's private-file field; a private file's content is its "
        "owner's, and git copies it whole (copy_data_file):\n" + "\n".join(named)
    )
