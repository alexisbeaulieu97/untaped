"""``untaped …`` commands quoted in Markdown and YAML, and whether each parses.

Shared by the skill-file and docs tests: a command in a ``bash`` block must
parse in full, one named in inline code may leave out required arguments.
Synopsis notation is read the obvious way (``[--flag X]`` is optional text,
``a|b`` takes ``a``, ``...`` repeats) and placeholders (``NAME``,
``<recipe>``) bind without being converted.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator

from cyclopts import App
from cyclopts.exceptions import (
    CoercionError,
    CycloptsError,
    MissingArgumentError,
    ValidationError,
)

_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
INLINE = re.compile(r"`(untaped(?: [^`]*)?)`")
_SEGMENT_BREAK = re.compile(r"\s+\|\s+|\s*(?:&&|\|\||;)\s*")
_REDIRECT = re.compile(r"^\d*[<>]")
_SYNOPSIS_SUFFIX = re.compile(r"(?:\]|\.\.\.|…)+$")
PLACEHOLDER = re.compile(r"^[A-Z][A-Z0-9_]*(?:[/=:.-][A-Z0-9_]+)*$")
_ROOT_FLAGS = {"-q", "--quiet", "-v", "--verbose", "--deprecated"}
_HELP_FLAGS = {"--help", "-h"}


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


def segments(line: str) -> Iterator[str]:
    """The ``untaped`` commands of a shell line (pipes and ``&&``/``;`` lists split)."""
    for segment in _SEGMENT_BREAK.split(line.strip()):
        words = segment.split()
        while words and re.match(r"^[A-Z_]+=\S*$", words[0]):
            words.pop(0)  # an environment assignment
        if words[:1] == ["untaped"]:
            yield " ".join(words)


def argv_of(command: str) -> tuple[list[str], set[str]]:
    """``command`` as argv (without ``untaped``) plus the placeholder tokens in it."""
    argv: list[str] = []
    placeholders: set[str] = set()
    tokens = shlex.split(command)[1:]
    skip = False
    for raw in tokens:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(raw):
            skip = raw.rstrip("0123456789&") in {">", "<", ">>", "2>"}
            continue
        if raw in _ROOT_FLAGS:
            continue
        if raw == "--profile":
            skip = True
            continue
        token = _SYNOPSIS_SUFFIX.sub("", raw.removeprefix("["))
        token = token.split("|", 1)[0] if "|" in token and not token.startswith("{") else token
        if "<" in token:
            token = token.replace("<", "").replace(">", "")
            placeholders.add(token)
        if not token:
            continue
        if PLACEHOLDER.match(token.split("=", 1)[-1]):
            placeholders.add(token)
        argv.append(token)
    return argv, placeholders


def parse_problem(root: App, command: str, *, inline: bool) -> str | None:
    """Why ``command`` does not parse against ``root`` (``None`` when it does).

    ``inline``: a command named in prose, which may leave out required arguments.
    """
    argv, placeholders = argv_of(command)
    if not argv:
        return None
    if _HELP_FLAGS & set(argv):
        _, _, unused = root.parse_commands([token for token in argv if token not in _HELP_FLAGS])
        return f"not a command: {' '.join(unused)}" if unused else None
    try:
        root.parse_args(argv, exit_on_error=False, print_error=False, help_on_error=False)
    except ValidationError:
        return None
    except MissingArgumentError as exc:
        return None if inline else str(exc)
    except CoercionError as exc:
        if exc.token is not None and exc.token.value in placeholders:
            return None
        return str(exc)
    except CycloptsError as exc:
        return str(exc)
    return None
