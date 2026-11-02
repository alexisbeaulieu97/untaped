"""Process-boundary lint: child process groups through ``untaped.sdk.process``, signals in core.

Core's ``untaped.sdk.process`` starts every child in its own process group,
stops the whole group on a timeout or interrupt and passes untaped's own
SIGINT, SIGTERM and SIGHUP on to it; core's entry point owns those signals.
Two rules keep a plugin from going around it, read from the syntax tree:

- ``process-group``: a group or session made or signalled by hand
  (``start_new_session=``, ``process_group=``, ``preexec_fn=os.setsid``,
  ``posix_spawn``'s ``setsid=``/``setpgroup=``, ``os.setsid``, ``os.setpgid``,
  ``os.setpgrp``, ``os.killpg``, ``os.kill`` with a negative pid, ``pty.fork``,
  ``os.forkpty``), which untaped's interrupt would leave running;
- ``signal-handler``: ``signal.signal``, a loop's ``add_signal_handler`` or
  ``signal.pthread_sigmask``, which replaces or blocks the handler that stops
  every plugin's children.

``# untaped: allow process-group`` (or ``signal-handler``) on the line waives one.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import SourceFile

GROUP_RULE = "process-group"
SIGNAL_RULE = "signal-handler"
GROUP_MESSAGE = (
    "starts or signals a process group by hand; start the child with untaped.sdk.process, "
    "which stops its whole group on a timeout or interrupt (a group made by hand is left "
    "running when untaped is interrupted)"
)
SIGNAL_MESSAGE = (
    "installs a signal handler or blocks signals; untaped's entry point owns SIGINT, SIGTERM "
    "and SIGHUP, and a plugin's handler replaces the one that stops every plugin's child "
    "processes"
)
_GROUP_KEYWORDS = frozenset({"start_new_session", "process_group"})
_SPAWN_KEYWORDS = frozenset({"setsid", "setpgroup"})  # os.posix_spawn's
_SPAWN_CALLS = frozenset({"os.posix_spawn", "os.posix_spawnp"})
_GROUP_CALLS = frozenset(
    {"os.setsid", "os.setpgid", "os.setpgrp", "os.killpg", "os.forkpty", "pty.fork"}
)
_MODULES = frozenset({"os", "pty", "signal"})


def process_boundary_violations(source_dir: Path, files: Sequence[SourceFile]) -> list[str]:
    """Both rules' violations in ``files`` (code in ``source_dir``), relative to its parent."""
    found: list[str] = []
    for source in files:
        rel = source.path.relative_to(source_dir.parent).as_posix()
        names = _imported(source.tree)
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Call):
                continue
            called = _called(node.func, names)
            line = node.lineno
            if _makes_group(node, called, names):
                if not allows(source.lines, line, GROUP_RULE):
                    found.append(f"{rel}:{line}::{GROUP_RULE}::{GROUP_MESSAGE}")
            elif _handles_signal(node, called) and not allows(source.lines, line, SIGNAL_RULE):
                found.append(f"{rel}:{line}::{SIGNAL_RULE}::{SIGNAL_MESSAGE}")
    return found


def _handles_signal(node: ast.Call, called: str | None) -> bool:
    if called in {"signal.signal", "signal.pthread_sigmask"}:
        return True
    return isinstance(node.func, ast.Attribute) and node.func.attr == "add_signal_handler"


def _makes_group(node: ast.Call, called: str | None, names: dict[str, str]) -> bool:
    if called in _GROUP_CALLS or _group_keyword(node, called, names):
        return True
    return called == "os.kill" and bool(node.args) and _negative(node.args[0])


def _imported(tree: ast.Module) -> dict[str, str]:
    """Local names bound to ``os``, ``pty``, ``signal`` or a member: ``{"killpg": "os.killpg"}``."""
    names = {module: module for module in _MODULES}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _MODULES:
                    names[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in _MODULES:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _called(func: ast.expr, names: dict[str, str]) -> str | None:
    """``os.killpg`` for ``os.killpg``, ``killpg`` (from os) or ``_os.killpg`` (aliased)."""
    if isinstance(func, ast.Name):
        return names.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = names.get(func.value.id)
        return None if module is None else f"{module}.{func.attr}"
    return None


def _group_keyword(node: ast.Call, called: str | None, names: dict[str, str]) -> bool:
    keywords = _GROUP_KEYWORDS | _SPAWN_KEYWORDS if called in _SPAWN_CALLS else _GROUP_KEYWORDS
    for keyword in node.keywords:
        if keyword.arg in keywords and not _false(keyword.value):
            return True
        if keyword.arg == "preexec_fn" and _called(keyword.value, names) in {
            "os.setsid",
            "os.setpgrp",
        }:
            return True
    return False


def _false(value: ast.expr) -> bool:
    """``False``/``None`` written out: asking for no group is no violation."""
    return isinstance(value, ast.Constant) and (value.value is False or value.value is None)


def _negative(value: ast.expr) -> bool:
    return isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub)
