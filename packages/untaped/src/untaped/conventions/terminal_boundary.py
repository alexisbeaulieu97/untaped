"""Terminal-boundary lint: a plugin builds screens with ``untaped.sdk``, not prompt_toolkit.

Core's one terminal adapter (``untaped.screen.terminal``) is the only module
that imports prompt_toolkit, so a screen never depends on a terminal library
and the adapter can be replaced. Any import of ``prompt_toolkit`` or one of its
submodules in a plugin's source (function-level and ``TYPE_CHECKING``
imports included) is flagged as
``<file>:<line>::terminal-boundary::imports prompt_toolkit; build screens with untaped.sdk``.

``# untaped: allow terminal-boundary`` on the import line waives one.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

from untaped.conventions.allow import allowed
from untaped.conventions.source import SourceFile, import_targets

RULE = "terminal-boundary"
_LIBRARY = "prompt_toolkit"


def terminal_boundary_violations(source_dir: Path, files: Sequence[SourceFile]) -> list[str]:
    """Violations in ``files`` (code in ``source_dir``); paths are relative to its parent."""
    found: list[str] = []
    for source in files:
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            if not any(
                target == _LIBRARY or target.startswith(f"{_LIBRARY}.")
                for target in import_targets(node, "")
            ):
                continue
            if not allowed(source.lines, node.lineno, RULE):
                found.append(
                    f"{rel}:{node.lineno}::{RULE}::imports {_LIBRARY}; "
                    "build screens with untaped.sdk"
                )
    return found
