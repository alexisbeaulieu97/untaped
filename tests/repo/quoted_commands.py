"""``untaped …`` commands quoted in Markdown and YAML, and whether each parses.

Shared by the skill-file and docs tests: a command in a ``bash`` block must
parse in full, one named in inline code may leave out required arguments.
Finding them in Markdown and whether one parses is core's
:mod:`untaped.conventions.commands`, which the ``Examples`` and guide
conventions read too.
"""

from __future__ import annotations

from untaped.conventions.commands import (
    INLINE,
    PLACEHOLDER,
    argv_of,
    commands,
    comment_commands,
    parse_problem,
    segments,
)

__all__ = [
    "INLINE",
    "PLACEHOLDER",
    "argv_of",
    "commands",
    "comment_commands",
    "parse_problem",
    "segments",
]
