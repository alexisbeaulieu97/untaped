"""``untaped …`` commands quoted in Markdown and YAML, and whether each parses.

Shared by the skill-file and docs tests: a command in a ``bash`` block must
parse in full, one named in inline code may leave out required arguments.
Whether one parses is core's :mod:`untaped.conventions.commands`, which the
``Examples`` convention reads too.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from untaped.conventions.commands import PLACEHOLDER, argv_of, parse_problem, segments

_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
INLINE = re.compile(r"`(untaped(?: [^`]*)?)`")


def commands(text: str) -> Iterator[tuple[str, bool]]:
    """Every ``untaped …`` command in Markdown, and whether it is inline code.

    Commands in ``bash`` blocks come first, then those in inline code spans.
    """
    for language, block in _FENCE.findall(text):
        if language in {"bash", "sh", "shell"}:
            for line in block.replace("\\\n", " ").splitlines():
                yield from ((command, False) for command in segments(line.split(" #", 1)[0]))
    for span in INLINE.findall(_FENCE.sub("", text)):
        yield from ((command, True) for command in segments(span))


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
