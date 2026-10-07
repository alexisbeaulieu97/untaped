"""Message helpers that keep stderr wording consistent across the suite.

Every capability phrases counts, quoted names, "not found" errors, hints and
summaries through these helpers instead of hand-rolled f-strings, so the
conventions in ``docs/reference/conventions.md#messages-stderr`` hold in one place:

- ``plural(3, "repo")`` → ``3 repos`` (never ``repo(s)``);
- ``q("name")`` → ``'name'``;
- ``not_found("profile", "prod", known=["default"])`` →
  ``profile not found: 'prod'; known: default``;
- ``hint("config set awx.token --prompt")`` →
  ``hint: run `untaped config set awx.token --prompt```;
- ``summary("sync", {"cloned": 2, "failed": 1})`` → ``sync: 2 cloned, 1 failed``.

All helpers are pure string builders; callers choose the stream and the
exception type.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping, Sequence

_ROOT_COMMAND = "untaped"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """Render ``count`` with the right noun form: ``1 repo``, ``2 repos``.

    ``plural_form`` covers irregular nouns (``plural(2, "index", "indexes")``);
    the default appends ``s``.
    """
    noun = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {noun}"


def q(value: object) -> str:
    """Quote a user-supplied name for a message: ``'name'``.

    Uses Python string quoting of ``str(value)``, so embedded quotes and
    control characters are escaped rather than breaking the line, and a
    non-string value never leaks its ``repr`` (``Path('x')``, ``{...}``).
    """
    return repr(str(value))


def not_found(noun: str, name: object, *, known: Iterable[object] | None = None) -> str:
    """The standard "not found" sentence: ``<noun> not found: 'x'[; known: a, b]``.

    Pass ``known`` to list the valid names (an empty iterable renders
    ``known: none``); omit it when the set is too large or unknowable.
    """
    message = f"{noun} not found: {q(name)}"
    if known is None:
        return message
    names = [str(item) for item in known]
    return f"{message}; known: {', '.join(names) if names else 'none'}"


def command_line(command: str) -> str:
    """``command`` as an ``untaped …`` command line (a leading ``untaped`` is kept once)."""
    text = command.strip()
    if text != _ROOT_COMMAND and not text.startswith(f"{_ROOT_COMMAND} "):
        text = f"{_ROOT_COMMAND} {text}"
    return text


def command_argv(command: str | Sequence[str], *, profile: str) -> list[str]:
    """``command`` as the argv an agent runs after ``untaped``.

    A string is split like a shell would; a leading ``untaped`` is dropped.
    ``--profile <profile>`` leads the argv unless the command already names
    one, so it acts on the profile it was made for.
    """
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if argv[:1] == [_ROOT_COMMAND]:
        argv = argv[1:]
    named = any(arg == "--profile" or arg.startswith("--profile=") for arg in argv)
    if not named:
        argv = ["--profile", profile, *argv]
    return argv


def hint(command: str) -> str:
    """A follow-up hint line: ``hint: run `untaped <command>```.

    ``command`` may include or omit the leading ``untaped``. Append it to an
    error on its own line (``f"{message}\\n{hint(...)}"``) or print it as a
    separate stderr line.
    """
    return f"hint: run `{command_line(command)}`"


def deprecated_message(old: str, new: str | None = None) -> str:
    """The deprecation sentence: ``<old> is deprecated and will be removed …[; use <new>]``.

    One wording for every deprecated spelling (commands, flags, config keys
    and their environment variables); callers pass the spellings as shown.
    Without a replacement the sentence ends at "next major release".
    """
    message = f"{old} is deprecated and will be removed in the next major release"
    return message if new is None else f"{message}; use {new}"


EXPERIMENTAL_LINE = "Experimental: may change in a minor release."
"""The help line every experimental command ends with."""


def deprecated_line(replacement: str | None = None) -> str:
    """The help line every deprecated command ends with: ``Deprecated: removed …[; use <new>].``"""
    suffix = "" if replacement is None else f"; use {replacement}"
    return f"Deprecated: removed in the next major release{suffix}."


def summary(operation: str, counts: Mapping[str, int]) -> str:
    """A batch summary line: ``<operation>: 2 cloned, 1 failed``.

    Zero counts are dropped; when every count is zero the line reads
    ``<operation>: nothing to do``.
    """
    parts = [f"{count} {outcome}" for outcome, count in counts.items() if count]
    return f"{operation}: {', '.join(parts) if parts else 'nothing to do'}"


__all__ = [
    "EXPERIMENTAL_LINE",
    "command_argv",
    "command_line",
    "deprecated_line",
    "deprecated_message",
    "hint",
    "not_found",
    "plural",
    "q",
    "summary",
]
