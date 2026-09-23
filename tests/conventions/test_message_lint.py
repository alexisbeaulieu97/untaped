"""Message lint: stderr wording follows ``docs/conventions.md``.

An AST scan over ``src/untaped/capabilities/**`` and
``src/untaped/management/**`` flags:

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

Entries are ``<path>::<rule>::<message snippet>``; existing ones live in
``baselines/messages/<owner>.txt``.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "untaped"
SCANNED = (SRC / "capabilities", SRC / "management")
SKIPPED = frozenset({SRC / "capabilities" / "registry.py", SRC / "capabilities" / "__init__.py"})

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


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


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


def _violations(tree: ast.Module) -> Iterator[tuple[str, str]]:
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
            yield "local-plural", node.name
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "stdin"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            yield "sys-stdin", "sys.stdin"
        elif isinstance(node, ast.Call):
            yield from _call_violations(node)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            if node.value.lstrip().startswith("Warning:"):
                yield "capital-warning", _snippet(node.value)
            if "(s)" in node.value:
                yield "paren-s", _snippet(node.value)


def _call_violations(node: ast.Call) -> Iterator[tuple[str, str]]:
    name = _callee(node)
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


def _owner(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[1] if rel.parts[0] == "capabilities" else "root"


def collect_violations() -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for base in SCANNED:
        for path in sorted(base.rglob("*.py")):
            if path in SKIPPED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(REPO_ROOT).as_posix()
            for rule, detail in _violations(tree):
                found[_owner(path)].append(f"{rel}::{rule}::{detail}")
    return found


def test_messages_follow_the_wording_conventions(baseline: Any) -> None:
    baseline("messages", collect_violations())


def test_lint_rules_catch_each_banned_shape() -> None:
    source = """
import sys
def pluralize(n, noun): ...
echo(f"error: {x} failed", err=True)
echo("warning: stale", err=True)
raise ConfigError("Something broke.")
raise ConfigError("--a and --b are mutually exclusive")
raise UsageError("--a and --b are mutually exclusive")
label = "Deleted 3 repo(s)"
note = "Warning: careful"
sys.stdin.read()
print("x")
"""
    rules = sorted(rule for rule, _ in _violations(ast.parse(source)))
    assert rules == sorted(
        [
            "local-plural",
            "echo-error",
            "echo-warning",
            "error-case",
            "usage-phrase",
            "paren-s",
            "capital-warning",
            "sys-stdin",
            "print",
        ]
    )
