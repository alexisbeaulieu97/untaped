"""The inline ``# untaped: allow <rule>[, <rule>...]`` marker.

A source-level convention check skips a violation whose line carries this
marker for its rule. Use it only where the code must do the flagged thing.
"""

from __future__ import annotations

import re

_MARKER = re.compile(r"#\s*untaped:\s*allow\s+([a-z0-9-]+(?:\s*,\s*[a-z0-9-]+)*)")


def allowed(source_lines: list[str], lineno: int, rule: str) -> bool:
    """Whether line ``lineno`` (1-based) of ``source_lines`` allows ``rule``."""
    if not 1 <= lineno <= len(source_lines):
        return False
    match = _MARKER.search(source_lines[lineno - 1])
    return match is not None and rule in {name.strip() for name in match.group(1).split(",")}
