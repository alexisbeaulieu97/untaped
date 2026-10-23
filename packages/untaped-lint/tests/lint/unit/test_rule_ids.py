"""Every rule id the convention checks can emit is one Conventions declares, and the reverse.

The ids are read from the code that writes them: the ``<where>::<rule>::<detail>``
lines of core's convention modules and lint's file rules (a literal, or a
module constant such as ``RULE``), the ``(rule, detail)`` tuples their helpers
yield, ``rule = "..."`` and a ``*_rule`` helper's return, ``rule=`` and
``_finding("<rule>", ...)`` in lint, and the check names of core's
``plugin_check/contracts.py`` rows (a ``conventions`` row's title is a rule).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from untaped_lint.providers.conventions import RULES
from untaped_lint.records import RULE_ID

PACKAGES = Path(__file__).resolve().parents[4]
CORE = PACKAGES / "untaped" / "src" / "untaped"
SOURCES = (
    *sorted((CORE / "core" / "plugin_check").glob("*.py")),
    *sorted((PACKAGES / "untaped-lint/src/untaped_lint/providers/conventions").glob("*.py")),
)
_ID = re.compile(RULE_ID)
_HOLE = "\0"


def _text(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _template(node: ast.JoinedStr, constants: dict[str, str]) -> str:
    """The f-string with module constants filled in and every other hole marked."""
    parts = []
    for value in node.values:
        if (text := _text(value)) is not None:
            parts.append(text)
        elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
            parts.append(constants.get(value.value.id, _HOLE))
        else:
            parts.append(_HOLE)
    return "".join(parts)


def _emitted(path: Path) -> Iterator[str | None]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {
        target.id: text
        for node in tree.body
        if isinstance(node, ast.Assign) and (text := _text(node.value)) is not None
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    pieces = {
        id(value)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for value in node.values
    }
    for node in ast.walk(tree):
        if id(node) in pieces:
            continue
        if isinstance(node, ast.JoinedStr | ast.Constant):
            line = _template(node, constants) if isinstance(node, ast.JoinedStr) else _text(node)
            fields = (line or "").split("::")
            if len(fields) > 1:  # <where>::<rule>[::<detail>]
                yield fields[1]
        elif isinstance(node, ast.Yield) and isinstance(node.value, ast.Tuple):
            yield next((t for e in node.value.elts if (t := _text(e)) is not None), None)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "rule" for target in node.targets
        ):
            yield _text(node.value)
        elif isinstance(node, ast.FunctionDef) and node.name.endswith("_rule"):
            for each in ast.walk(node):
                if isinstance(each, ast.Return):
                    yield _text(each.value)
        elif isinstance(node, ast.Call):
            yield from _call_ids(node)


def _call_ids(node: ast.Call) -> Iterator[str | None]:
    yield from (_text(kw.value) for kw in node.keywords if kw.arg == "rule")
    name = node.func.id if isinstance(node.func, ast.Name) else None
    first, second = [*(_text(arg) for arg in node.args[:2]), None, None][:2]
    if name == "row" and first == "conventions":  # plugin_check: the title is the rule
        yield second if second != "conventions" else None
    elif name in ("row", "_finding"):
        yield first


def _all_emitted() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in SOURCES:
        for rule in _emitted(path):
            if rule is not None and _ID.fullmatch(rule):
                found.setdefault(rule, []).append(path.name)
    return found


def test_the_extraction_sees_each_way_an_id_is_written() -> None:
    emitted = _all_emitted()
    for rule in ("import-boundary", "command-name", "reserved-short", "echo-error", "live"):
        assert rule in emitted
    assert "description" not in emitted  # a detail, not a rule


def test_every_emitted_id_is_declared() -> None:
    declared = {rule.id for rule in RULES}
    undeclared = {rule: files for rule, files in _all_emitted().items() if rule not in declared}
    assert not undeclared, f"emitted but not in Conventions' table: {undeclared}"


def test_every_declared_id_is_emitted() -> None:
    emitted = _all_emitted()
    assert not [rule.id for rule in RULES if rule.id not in emitted]
