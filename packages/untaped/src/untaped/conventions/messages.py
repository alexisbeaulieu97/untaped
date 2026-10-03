"""Message lint: stderr wording follows ``docs/reference/conventions.md#messages-stderr``.

An AST scan over a package's source flags:

- ``echo-error`` / ``echo-warning`` — ``echo("error: …")`` or
  ``echo("warning: …")``: raise an ``UntapedError`` (``report_errors``
  prints it) or use ``ui.message("warning", …)``;
- ``capital-warning`` — a literal starting with ``Warning:``;
- ``paren-s`` — ``(s)`` in a literal: use ``plural()``;
- ``error-case`` — an error message that starts with a capitalized word
  or ends with a period;
- ``usage-phrase`` — a usage-type phrase ("mutually exclusive",
  "must be >=", "not both", "cannot be combined") raised as anything but
  ``UsageError`` / ``raise_usage``;
- ``sys-stdin`` — direct ``sys.stdin`` access: use the core stdin helpers;
- ``local-plural`` — a local pluralize helper: use ``plural()``;
- ``print`` / ``rich-console`` — ``print(…)`` or a direct
  ``rich.console.Console``: use ``echo`` / ``UiContext``.

Lines are ``<path>::<rule>::<message snippet>``; ``# untaped: allow <rule>``
on the flagged line suppresses one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path

from untaped.conventions.allow import allowed
from untaped.conventions.source import SourceFile, callee

USAGE_PHRASES = ("mutually exclusive", "must be >=", "not both", "cannot be combined")
USAGE_RAISERS = frozenset({"UsageError", "raise_usage"})
PLURAL_HELPERS = frozenset({"plural", "_plural", "pluralize", "_pluralize", "plural_s", "_s"})
PROPER_NOUNS = frozenset({"GitHub", "Jira", "Ansible", "Git", "YAML", "JSON", "AWX", "SSH"})
_SNIPPET = 72


def _text(node: ast.expr) -> str | None:
    """The literal text of a str constant or f-string (``{}`` for fields)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        if left is not None or right is not None:
            return f"{left or '{}'}{right or '{}'}"
    return None


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_SNIPPET]


def _is_error_class(name: str) -> bool:
    return name.endswith(("Error", "Exception")) or name in USAGE_RAISERS


def _error_case(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.split()[0].rstrip(":,;")
    capitalized = (
        stripped[0].isupper()
        and len(stripped) > 1
        and stripped[1].islower()
        and first not in PROPER_NOUNS
    )
    return capitalized or (stripped.endswith(".") and not stripped.endswith("..."))


def tree_violations(tree: ast.Module) -> Iterator[tuple[int, str, str]]:
    """Yield ``(lineno, rule, detail)`` for one module."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in PLURAL_HELPERS:
            yield node.lineno, "local-plural", node.name
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "stdin"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            yield node.lineno, "sys-stdin", "sys.stdin"
        elif isinstance(node, ast.Call):
            for rule, detail in _call_violations(node):
                yield node.lineno, rule, detail
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            if node.value.lstrip().startswith("Warning:"):
                yield node.lineno, "capital-warning", _snippet(node.value)
            if "(s)" in node.value:
                yield node.lineno, "paren-s", _snippet(node.value)


def _call_violations(node: ast.Call) -> Iterator[tuple[str, str]]:
    name = callee(node)
    if name == "print":
        yield "print", "print()"
    if name == "Console" and not any(kw.arg == "file" for kw in node.keywords):
        yield "rich-console", "Console()"
    if not node.args:
        return
    text = _text(node.args[0])
    if text is None:
        return
    if name == "echo":
        lowered = text.lstrip()
        if lowered.startswith("error:"):
            yield "echo-error", _snippet(text)
        elif lowered.lower().startswith("warning:"):
            yield "echo-warning", _snippet(text)
    if _is_error_class(name):
        if _error_case(text):
            yield "error-case", _snippet(text)
        if name not in USAGE_RAISERS and any(phrase in text for phrase in USAGE_PHRASES):
            yield "usage-phrase", _snippet(text)


def message_violations(source_dir: Path, files: Sequence[SourceFile]) -> list[str]:
    """Violations in ``files`` (under ``source_dir``), with paths relative to its parent."""
    found: list[str] = []
    for source in files:
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for lineno, rule, detail in tree_violations(source.tree):
            if not allowed(source.lines, lineno, rule):
                found.append(f"{rel}::{rule}::{detail}")
    return found
