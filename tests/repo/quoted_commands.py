"""``untaped …`` commands quoted in Markdown, and whether each parses.

Read by the docs tests: a command in a ``bash`` block must
parse in full, one named in inline code may leave out required arguments.
Finding them in Markdown and whether one parses is core's
:mod:`untaped.runtime.command_lines`, which the ``Examples`` and guide
conventions read too.
"""

from __future__ import annotations

from untaped.runtime.command_lines import (
    INLINE,
    PLACEHOLDER,
    argv_of,
    commands,
    parse_problem,
    segments,
)

__all__ = [
    "INLINE",
    "PLACEHOLDER",
    "argv_of",
    "commands",
    "parse_problem",
    "segments",
]
