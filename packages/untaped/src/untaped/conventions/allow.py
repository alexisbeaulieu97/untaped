"""The inline ``# untaped: allow <rule>[, <rule>...]`` marker.

A source-level convention check skips a violation whose line carries this
marker for its rule. Use it only where the code must do the flagged thing.
A rule is named bare (``error-case``) or with the key of whoever reports it
(``lint.error-case``), as ``untaped lint`` prints it.
"""

from __future__ import annotations

import re

_NAME = r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)?"
_MARKER = re.compile(rf"#\s*untaped:\s*allow\s+({_NAME}(?:\s*,\s*{_NAME})*)")


def allowed(source_lines: list[str], lineno: int, rule: str) -> bool:
    """Whether line ``lineno`` (1-based) of ``source_lines`` allows ``rule``.

    ``rule`` is the bare id of one of lint's conventions; the marker may name
    it bare or as ``lint.<rule>``.
    """
    if not 1 <= lineno <= len(source_lines):
        return False
    match = _MARKER.search(source_lines[lineno - 1])
    if match is None:
        return False
    names = {name.strip() for name in match.group(1).split(",")}
    return rule in names or f"lint.{rule}" in names
