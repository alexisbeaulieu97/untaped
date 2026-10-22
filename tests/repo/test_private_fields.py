"""git names no field of another plugin's private file in a store repo.

Each plugin's ``untaped-<plugin>.json`` holds that plugin's data, read and
written through ``RepoStore.read_private``/``write_private`` with its own
model. git decides when the file is created, copied (adopt) and deleted, never
what is in it: adopt once dropped github's ``fetched_at``/``pushed_at`` by
name, so a field github renamed would have kept its freshness through a copy.
So untaped_git uses no key that is a field of a model another first-party
plugin passes to ``read_private``, nor one of github's 11.0 freshness fields.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from repo.support import FIRST_PARTY, PACKAGES

#: Fields 11.0's private files held that no model names any more.
LEGACY = frozenset({"fetched_at", "pushed_at"})
#: Models the scan must find, so a change in how plugins call it can't empty it.
EXPECTED = frozenset({"BackfillMark", "CorpusMeta"})


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
    """The keys the ``models`` classes put on disk: each field's name and string aliases."""
    fields: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ClassDef) and node.name in models):
                continue
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
                    fields.update(_aliases(item.value))
    return fields


def _aliases(value: ast.expr | None) -> Iterator[str]:
    if isinstance(value, ast.Call):
        for keyword in value.keywords:
            if keyword.arg in {"alias", "serialization_alias", "validation_alias"} and (
                isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)
            ):
                yield keyword.value.value


def _keys(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Each string ``tree`` uses as a key, or names to use as one.

    ``x["k"]``, ``x.get("k")`` (``pop``, ``setdefault``), ``{"k": ...}``,
    ``"k" in x``, a loop over literal keys (``for key in ("a", "b")``), and any
    string a module or class constant holds (``_FRESHNESS_KEYS = ("fetched_at",
    "pushed_at")``, ``frozenset({...})``, ``KEY = "pushed_at"``). Other strings
    (messages, ``plural(n, "repo")``) are not keys.
    """
    for node in ast.walk(tree):
        found: list[ast.expr | None] = []
        if isinstance(node, ast.Subscript):
            found.append(node.slice)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "pop", "setdefault"}
            and node.args
        ):
            found.append(node.args[0])
        elif isinstance(node, ast.Dict):
            found.extend(node.keys)
        elif isinstance(node, ast.Compare) and any(
            isinstance(op, ast.In | ast.NotIn) for op in node.ops
        ):
            found.append(node.left)
        elif isinstance(node, ast.For | ast.comprehension) and isinstance(
            node.iter, ast.Tuple | ast.List | ast.Set
        ):
            found.extend(node.iter.elts)
        elif isinstance(node, ast.Module | ast.ClassDef):
            for statement in node.body:
                if isinstance(statement, ast.Assign | ast.AnnAssign) and statement.value:
                    found.extend(n for n in ast.walk(statement.value) if isinstance(n, ast.expr))
        for key in found:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.lineno, key.value


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
        for line, key in _keys(tree)
        if key in fields
    ]
    assert named == [], (
        "git names another plugin's private-file field; a private file's content is its "
        "owner's, and git copies it whole (copy_data_file):\n" + "\n".join(named)
    )


@pytest.mark.parametrize(
    "source",
    [
        '_FRESHNESS_KEYS = ("fetched_at", "pushed_at")',
        'data.pop("pushed_at", None)',
        'value = data["history"]',
        'payload = {"history": "partial"}',
        'def f(data):\n    for key in ("fetched_at", "pushed_at"):\n        data.pop(key)',
        'KEYS = frozenset({"pushed_at"})',
        'KEY = "pushed_at"',
        'def f(data):\n    return "pushed_at" in data',
    ],
)
def test_a_key_is_found_in_each_shape(source: str) -> None:
    assert [key for _, key in _keys(ast.parse(source))] != []


def test_a_word_in_a_message_is_not_a_key() -> None:
    source = 'def label(count):\n    return plural(count, "repo") + f"{count} repos"'
    assert list(_keys(ast.parse(source))) == []
