"""First-party code writes stderr through ``ctx.diag``, never the deprecated helpers.

``UiContext.message``, ``.success`` and ``.progress``, ``report_error``,
``report_row_errors``, and ``echo``/``UiContext.styled`` with ``err=True`` are
deprecated until 12.0. No shipped module outside ``untaped/output/`` calls
them: a call is flagged by name, and ``success``/``progress`` only when the
receiver is not a ``Diagnostics`` handle (``diag``, ``_diag``, ``<x>.diag``,
``Diagnostics()``) or core's sink (``current_sink()``), since they have
methods of those names. A bound
helper passed as a callback (``partial(ui.message, "warning")``) is flagged
too. mypy's ``deprecated`` error code is the type-aware check behind this one.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT

#: The sink's own package, relative to its ``src``.
OWNER = "untaped/output/"
#: Modules allowed to keep a flagged call, with how many.
EXEMPT: dict[str, int] = {}
#: Handle spellings whose ``success``/``progress`` are ``Diagnostics`` methods.
_HANDLES = frozenset({"diag", "_diag"})
#: Calls returning a handle or the sink.
_HANDLE_CALLS = frozenset({"Diagnostics", "current_sink"})


def _is_handle(receiver: ast.expr) -> bool:
    if isinstance(receiver, ast.Name):
        return receiver.id in _HANDLES
    if isinstance(receiver, ast.Attribute):
        return receiver.attr == "diag"
    return (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id in _HANDLE_CALLS
    )


def _err(call: ast.Call) -> bool:
    """Whether ``call`` passes ``err=`` with anything but the literal ``False``."""
    return any(
        each.arg == "err"
        and not (isinstance(each.value, ast.Constant) and each.value.value is False)
        for each in call.keywords
    )


def _flagged(node: ast.expr) -> bool:
    """Whether ``node``, called or passed as a callback, is a deprecated helper."""
    if isinstance(node, ast.Attribute):
        if node.attr == "message":
            return True
        return node.attr in ("success", "progress") and not _is_handle(node.value)
    return False


def helper_calls(source: str) -> list[int]:
    """The lines of ``source`` that call (or pass on) a deprecated stderr helper."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            _flagged(func)
            or (isinstance(func, ast.Name) and func.id in ("report_error", "report_row_errors"))
            or (isinstance(func, ast.Name) and func.id == "echo" and _err(node))
            or (isinstance(func, ast.Attribute) and func.attr == "styled" and _err(node))
            or (
                isinstance(func, ast.Name)
                and func.id == "partial"
                and bool(node.args)
                and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr in ("message", "success")
                and _flagged(node.args[0])
            )
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


def test_no_first_party_module_calls_a_deprecated_helper() -> None:
    stray: list[str] = []
    for name, path in _modules():
        if name.startswith(OWNER):
            continue
        lines = helper_calls(path.read_text(encoding="utf-8"))
        if name in EXEMPT and len(lines) == EXEMPT[name]:
            continue
        stray += [f"{name}:{line}" for line in lines]
    assert not stray, f"{stray}: write stderr through ctx.diag (untaped.sdk.Diagnostics) instead"


def test_the_exemption_is_still_needed() -> None:
    for name, count in EXEMPT.items():
        [path] = [path for each, path in _modules() if each == name]
        assert len(helper_calls(path.read_text(encoding="utf-8"))) == count, name


def test_the_check_catches_every_form() -> None:
    bad = """\
ui.message("info", x)
ui_context(strict=False).message("warning", x)
recipe_ui().message("info", x)
ctx.progress_ui().success(x)
ui.progress("x")
ui.styled(t, err=True)
echo("x", err=True)
report_error(e)
report_row_errors(rows, item=f)
partial(ctx.progress_ui().message, "warning")
echo("x", err=flag)
"""
    assert helper_calls(bad) == list(range(1, 12))


def test_the_check_leaves_the_handle_alone() -> None:
    good = """\
ctx.diag.success(x)
Diagnostics().progress("x")
current_sink().progress("x", quiet=True)
_diag.info(x)
diag.success(x)
exc.message
ui.styled(t)
echo(x)
echo(x, err=False)
partial(diag.warning)
"""
    assert helper_calls(good) == []


#: A plugin calling each decorated helper: mypy reports each one (two at their import).
OLD = """\
from untaped.sdk import UiContext, UntapedError, report_error, report_row_errors

ui = UiContext()
ui.message("info", "x")
ui.success("x")
ui.progress("x")
report_error(UntapedError("x"))
report_row_errors([], item=str)
"""
#: The same plugin on the handle: nothing to report.
NEW = """\
from untaped.sdk import Diagnostics, UntapedError

diag = Diagnostics()
diag.info("x")
diag.success("x")
diag.progress("x")
diag.failure(UntapedError("x"))
"""


def test_mypy_reports_each_deprecated_helper_and_not_the_handle(tmp_path: Path) -> None:
    (tmp_path / "old_plugin.py").write_text(OLD, encoding="utf-8")
    (tmp_path / "new_plugin.py").write_text(NEW, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(tmp_path / "old_plugin.py"),
            str(tmp_path / "new_plugin.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    errors = [line for line in proc.stdout.splitlines() if ": error:" in line]
    assert all("old_plugin.py" in line and "[deprecated]" in line for line in errors), proc.stdout
    assert len(errors) == 5, proc.stdout
