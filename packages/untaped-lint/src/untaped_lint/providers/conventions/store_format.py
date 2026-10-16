"""Store-format lint: a database under a plugin's directory is opened through a ``Store``.

Two installs of untaped can share one ``~/.untaped``; a database file under
``plugin_dir(SPEC)`` is read by both, so either its layout is version-neutral
within the major or it is a ``Store`` opened through ``Store.open()``, which
keeps each layout under its own name.

Only the decidable case is flagged: a call to ``sqlite3.connect``,
``shelve.open`` or ``dbm.open`` (``dbm.<module>.open`` too, and the names
``from <module> import connect`` or ``open`` binds) one of whose arguments
itself contains a ``plugin_dir(...)`` or ``named_plugin_dir(...)`` call or a
``~/.untaped`` string. A path built elsewhere, kept on ``self`` or returned by
``Store.open()`` is not read. Lines are
``<file>:<line>::store-format::sqlite3.connect() on a path under plugin_dir() ...``.

``# untaped: allow store-format`` on the call's first line waives one, for
a file whose layout is version-neutral.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

from untaped.testing import allows
from untaped_lint.infrastructure.files import SourceFile, callee

RULE = "store-format"
#: The openers, by module: the function that opens a database file.
_OPENERS = {"sqlite3": "connect", "shelve": "open", "dbm": "open"}
_DIRS = frozenset({"plugin_dir", "named_plugin_dir"})
_HOME = "~/.untaped"


def store_format_violations(
    plugin: str, source_dir: Path, files: Sequence[SourceFile]
) -> list[str]:
    """Violations in ``files`` of ``plugin`` (code in ``source_dir``), relative to its parent."""
    found: list[str] = []
    for source in files:
        modules, functions = _bindings(source.tree)
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Call):
                continue
            opener = _opener(node.func, modules, functions)
            if opener is None:
                continue
            under = _under(node)
            if under is not None and not allows(source.lines, node.lineno, RULE):
                found.append(f"{rel}:{node.lineno}::{RULE}::{_message(opener, under, plugin)}")
    return found


def _bindings(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """Names bound to an opener's module, and names bound to an opener, as ``module.func``."""
    modules: dict[str, str] = {}
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _OPENERS:
                    continue
                if alias.asname is not None:
                    modules[alias.asname] = alias.name
                else:
                    # ``import dbm.gnu`` binds ``dbm``; the attribute chain names the rest.
                    top = alias.name.split(".")[0]
                    modules[top] = top
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top = node.module.split(".")[0]
            if top not in _OPENERS:
                continue
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == _OPENERS[top]:
                    functions[bound] = f"{node.module}.{alias.name}"
                elif top == "dbm":
                    modules[bound] = f"{node.module}.{alias.name}"
    return modules, functions


def _opener(func: ast.expr, modules: dict[str, str], functions: dict[str, str]) -> str | None:
    """``sqlite3.connect`` when ``func`` names an opener, else None."""
    if isinstance(func, ast.Name):
        return functions.get(func.id)
    if not isinstance(func, ast.Attribute):
        return None
    chain: list[str] = [func.attr]
    value = func.value
    while isinstance(value, ast.Attribute):
        chain.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name) or value.id not in modules:
        return None
    dotted = ".".join([modules[value.id], *reversed(chain)])
    module, _, name = dotted.rpartition(".")
    top = module.split(".")[0]
    if name != _OPENERS.get(top) or (top != "dbm" and module != top):
        return None
    return dotted


def _under(call: ast.Call) -> str | None:
    """What an argument of ``call`` is built from: ``plugin_dir()``, or None."""
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        for node in ast.walk(argument):
            if isinstance(node, ast.Call) and callee(node) in _DIRS:
                return f"{callee(node)}()"
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _HOME in node.value
            ):
                return _HOME
    return None


def _message(opener: str, under: str, plugin: str) -> str:
    return (
        f"{opener}() on a path under {under} that no Store governs; two versions of {plugin} "
        "sharing a home would rebuild or refuse each other's file; declare a Store and open "
        f"Store.open() (waive with `# untaped: allow {RULE}` when the file's layout is "
        "version-neutral)"
    )
