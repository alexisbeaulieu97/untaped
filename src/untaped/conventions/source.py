"""Source walking shared by the AST-based convention checks."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    """One parsed module: its path, its lines and its syntax tree."""

    path: Path
    lines: list[str]
    tree: ast.Module


def source_files(directory: Path) -> Iterator[SourceFile]:
    """Every ``*.py`` file under ``directory``, sorted, parsed once."""
    for path in sorted(directory.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        yield SourceFile(path, text.splitlines(), ast.parse(text))


def callee(node: ast.Call) -> str:
    """The called name: ``f`` for ``f(...)`` and ``obj.f(...)``, else ``""``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""
