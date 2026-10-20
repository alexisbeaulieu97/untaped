"""``untaped …`` commands quoted in Markdown and YAML, and whether each parses.

Shared by the skill-file and docs tests: a command in a ``bash`` block must
parse in full, one named in inline code may leave out required arguments.
Finding them in Markdown and whether one parses is core's
:mod:`untaped.conventions.commands`, which the ``Examples`` and guide
conventions read too.
"""

from __future__ import annotations

from collections.abc import Iterator

from untaped.conventions.commands import (
    INLINE,
    PLACEHOLDER,
    argv_of,
    commands,
    parse_problem,
    segments,
)


def comment_commands(text: str) -> Iterator[tuple[str, bool]]:
    """Every ``untaped …`` command in YAML comment lines (inline code, or the whole line)."""
    for line in text.splitlines():
        comment = line.lstrip()
        if not comment.startswith("#"):
            continue
        spans = INLINE.findall(comment)
        if spans:
            yield from ((command, True) for span in spans for command in segments(span))
        else:
            yield from ((command, False) for command in segments(comment.lstrip("# ")))


__all__ = [
    "INLINE",
    "PLACEHOLDER",
    "argv_of",
    "commands",
    "comment_commands",
    "parse_problem",
    "segments",
]
