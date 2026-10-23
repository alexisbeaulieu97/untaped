"""stderr has one writer: the sink in ``untaped/runtime/sink.py``.

A line written around the sink can garble the spinner, skip ``--quiet``, miss
the exit code and break JSON Lines. So no shipped module but
the sink calls ``sys.stderr.write``, passes ``file=sys.stderr``
(an ``IfExp`` choosing it included), or calls ``json_diagnostics`` or
``write_record``. ``scripts/`` are dev tools, not the CLI, and are out of scope.
"""

from __future__ import annotations

import ast
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT

#: The sink, relative to its ``src``.
OWNER = "untaped/runtime/sink.py"
#: The recipe hook worker is a child process: its stderr is a pipe the parent
#: reads and reports through the sink, so its traceback is not a CLI line.
EXEMPT = {"untaped_recipe/_worker/hook_worker.py": 1}
#: Helpers that wrote stderr JSON lines around the sink before it existed.
_RETIRED = frozenset({"json_diagnostics", "write_record"})


def _is_stderr(node: ast.AST) -> bool:
    """Whether ``node`` mentions ``sys.stderr`` anywhere (``a if c else sys.stderr`` too)."""
    return any(
        isinstance(each, ast.Attribute)
        and each.attr == "stderr"
        and isinstance(each.value, ast.Name)
        and each.value.id == "sys"
        for each in ast.walk(node)
    )


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def writers(source: str) -> list[int]:
    """The lines of ``source`` that write stderr around the sink."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            (isinstance(func, ast.Attribute) and func.attr == "write" and _is_stderr(func.value))
            or any(each.arg == "file" and _is_stderr(each.value) for each in node.keywords)
            or _called_name(node) in _RETIRED
        ):
            found.append(node.lineno)
    return sorted(found)


def _modules() -> list[tuple[str, Path]]:
    roots = [*PACKAGES.glob("*/src"), *(REPO_ROOT / "examples").glob("*/src")]
    return [
        (path.relative_to(root).as_posix(), path)
        for root in roots
        for path in sorted(root.rglob("*.py"))
    ]


def test_only_the_sink_writes_stderr() -> None:
    stray: list[str] = []
    for name, path in _modules():
        if name.startswith(OWNER):
            continue
        lines = writers(path.read_text(encoding="utf-8"))
        if name in EXEMPT and len(lines) == EXEMPT[name]:
            continue
        stray += [f"{name}:{line}" for line in lines]
    assert not stray, f"{stray}: write stderr through the sink (untaped.runtime.sink) instead"


def test_the_exemption_is_still_needed() -> None:
    for name, count in EXEMPT.items():
        [path] = [path for each, path in _modules() if each == name]
        assert len(writers(path.read_text(encoding="utf-8"))) == count, name


def test_the_check_catches_every_form() -> None:
    bad = """\
import sys
sys.stderr.write("x")
print("x", file=sys.stderr)
print("x", file=sys.stderr if err else sys.stdout)
json_diagnostics()
records.write_record(row)
"""
    assert writers(bad) == [2, 3, 4, 5, 6]


def test_the_check_leaves_other_streams_alone() -> None:
    good = """\
import sys
sys.stdout.write("x")
print("x")
print("x", file=sys.stdout)
stream = open("log")
stream.write("x")
"""
    assert writers(good) == []
