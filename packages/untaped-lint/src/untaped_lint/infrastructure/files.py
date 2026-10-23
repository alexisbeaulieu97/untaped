"""Reading a plugin's Python files for the source rules: each parsed once.

A file that isn't UTF-8 or isn't valid Python can't be checked by the source
rules; :func:`source_files` raises for it with its path, and the caller turns
that into one finding instead of a crash.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from untaped_lint.errors import UnreadableSourceError


@dataclass(frozen=True)
class SourceFile:
    """One parsed module: its path, its lines and its syntax tree."""

    path: Path
    lines: list[str]
    tree: ast.Module


def source_files(directory: Path) -> Iterator[SourceFile]:
    """Every ``*.py`` file under ``directory``, sorted, parsed once."""
    for path in sorted(directory.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except UnicodeDecodeError:
            raise UnreadableSourceError(path, "is not UTF-8") from None
        except SyntaxError as exc:
            raise UnreadableSourceError(path, f"is not valid Python (line {exc.lineno})") from None
        yield SourceFile(path, text.splitlines(), tree)


def callee(node: ast.Call) -> str:
    """The called name: ``f`` for ``f(...)`` and ``obj.f(...)``, else ``""``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def import_targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """The absolute modules ``node`` imports, resolving a relative import against ``package``.

    ``package`` is the importing module's package. ``from . import x`` binds
    the submodule ``<base>.x``; a relative import reaching above the
    top-level package resolves to nothing.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        return [node.module] if node.module else []
    parts = package.split(".")
    if node.level > len(parts):
        return []
    base = ".".join(parts[: len(parts) - node.level + 1])
    if node.module:
        return [f"{base}.{node.module}"]
    return [base if alias.name == "*" else f"{base}.{alias.name}" for alias in node.names]
