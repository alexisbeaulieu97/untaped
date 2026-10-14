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
