"""Message lint: stderr wording follows ``docs/reference/conventions.md#messages-stderr``.

An AST scan over a package's source flags:

- ``echo-error`` / ``echo-warning`` — ``echo("error: …")`` or
  ``echo("warning: …")``: raise an ``UntapedError`` (core's error
  boundary prints it) or use ``ctx.diag.warning(…)``;
- ``echo-failed`` — ``echo("failed: …")``: ``ctx.diag.failure(exc, item=…)``
  reports a per-item failure (an error line, with its category under JSON
  diagnostics);
- ``warnings-warn`` — ``warnings.warn(…)`` bypasses stderr diagnostics:
  take a ``warn`` callback or use ``ctx.diag.warning(…)``; a Python API
  deprecates through ``@deprecated_parameters`` or a behaviour's ``warn``;
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
  ``rich.console.Console``: return what to print from an ``Output`` renderer,
  ``ctx.diag`` for stderr;
- ``hand-typed-deprecation`` — a string, f-string or docstring shaped like a
  deprecation sentence (:data:`DEPRECATION_SHAPES`): every deprecation is a
  promise with a date, so its sentence comes from its mark, never typed by
  hand (``warnings.deprecated``'s own literal excepted: type checkers read
  only a literal). A marker anywhere on a multi-line string allows it.

Lines are ``<path>::<rule>::<message snippet>``; ``# untaped: allow <rule>``
on the flagged line suppresses one.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import SourceFile, callee

USAGE_PHRASES = ("mutually exclusive", "must be >=", "not both", "cannot be combined")
USAGE_RAISERS = frozenset({"UsageError", "raise_usage"})
PLURAL_HELPERS = frozenset({"plural", "_plural", "pluralize", "_pluralize", "plural_s", "_s"})
PROPER_NOUNS = frozenset({"GitHub", "Jira", "Ansible", "Git", "YAML", "JSON", "AWX", "SSH"})
_SNIPPET = 72

DEPRECATION_SHAPES = re.compile(
    "|".join(
        (
            r"\bis deprecated\b",  # untaped: allow hand-typed-deprecation
            r"\bwill be removed\b",  # untaped: allow hand-typed-deprecation
            r"\bremoved in \d",  # untaped: allow hand-typed-deprecation
            r"\bfuture major\b",  # untaped: allow hand-typed-deprecation
            r"\buntil \d+\.0\b",  # untaped: allow hand-typed-deprecation
            r"\bnext major\b",  # untaped: allow hand-typed-deprecation
            r"\A\s*Deprecated:",  # untaped: allow hand-typed-deprecation
        )
    )
)
"""The shapes of a deprecation sentence, which only untaped's message helpers build."""

_DEPRECATION_HINT = (
    "build it from the mark: deprecated(removed_in=…), deprecated_alias(…, removed_in=…), "
    "Renamed(…, removed_in=…), deprecated_behaviour(…, removed_in=…)"
)


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


def deprecation_sentences(tree: ast.Module) -> Iterator[tuple[int, int, str]]:
    """Yield ``(first line, last line, snippet)`` for each hand-typed deprecation sentence."""
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and callee(node) == "deprecated" and node.args:
            skipped.update(id(inner) for inner in ast.walk(node.args[0]))
        elif isinstance(node, ast.JoinedStr):
            skipped.update(id(value) for value in node.values)
    for node in ast.walk(tree):
        if id(node) in skipped or not isinstance(node, ast.Constant | ast.JoinedStr):
            continue
        text = _text(node)
        if text is not None and DEPRECATION_SHAPES.search(text):
            yield node.lineno, node.end_lineno or node.lineno, _snippet(text)


def tree_violations(tree: ast.Module) -> Iterator[tuple[int, str, str]]:
    """Yield ``(lineno, rule, detail)`` for one module."""
    for lineno, _, snippet in deprecation_sentences(tree):
        yield lineno, "hand-typed-deprecation", f"{snippet}: {_DEPRECATION_HINT}"
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
            if node.value.lstrip().startswith("Warning:"):  # untaped: allow capital-warning
                yield node.lineno, "capital-warning", _snippet(node.value)
            if "(s)" in node.value:  # untaped: allow paren-s
                yield node.lineno, "paren-s", _snippet(node.value)


def _echo_rule(text: str) -> str | None:
    """The rule an ``echo`` of ``text`` breaks: a status prefix only a helper prints."""
    stripped = text.lstrip()
    if stripped.startswith("error:"):
        return "echo-error"
    if stripped.lower().startswith("warning:"):
        return "echo-warning"
    if stripped.startswith("failed:"):
        return "echo-failed"
    return None


def _is_warnings_warn(node: ast.Call) -> bool:
    """A ``warnings.warn()`` call."""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "warn"
        and isinstance(func.value, ast.Name)
        and func.value.id == "warnings"
    )


def _call_violations(node: ast.Call) -> Iterator[tuple[str, str]]:
    name = callee(node)
    if _is_warnings_warn(node):
        yield "warnings-warn", "warnings.warn()"
    if name == "print":
        yield "print", "print()"
    if name == "Console" and not any(kw.arg == "file" for kw in node.keywords):
        yield "rich-console", "Console()"
    if not node.args:
        return
    text = _text(node.args[0])
    if text is None:
        return
    rule = _echo_rule(text) if name == "echo" else None
    if rule is not None:
        yield rule, _snippet(text)
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
        # A sentence's marker may sit on any of its lines (a docstring's last, say).
        spans = {first: last for first, last, _ in deprecation_sentences(source.tree)}
        for lineno, rule, detail in tree_violations(source.tree):
            last = spans.get(lineno, lineno) if rule == "hand-typed-deprecation" else lineno
            if not any(allows(source.lines, line, rule) for line in range(lineno, last + 1)):
                found.append(f"{rel}::{rule}::{detail}")
    return found
