"""Message helpers that keep stderr wording consistent across the suite.

Every plugin phrases counts, quoted names, "not found" errors, hints and
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
from datetime import UTC, datetime
from pathlib import Path

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


def split_profile(argv: Sequence[str]) -> tuple[str | None, list[str]]:
    """``argv``'s leading ``--profile NAME`` (or ``--profile=NAME``) and the rest.

    The profile is ``None`` when ``argv`` does not start with one.
    """
    first = argv[0] if argv else ""
    if first == "--profile" and len(argv) > 1:
        return argv[1], list(argv[2:])
    if first.startswith("--profile="):
        return first.removeprefix("--profile="), list(argv[1:])
    return None, list(argv)


def run_hint(command: str) -> str:
    """A hint's body naming a command to run: ``run `untaped <command>```.

    ``command`` may include or omit the leading ``untaped``. Print it with
    ``ctx.diag.hint(run_hint(...))``, or pass it as a failure's ``hint=``.
    """
    return f"run `{command_line(command)}`"


def hint(command: str) -> str:
    """A follow-up hint line: ``hint: run `untaped <command>``` (:func:`run_hint`'s body).

    Append it to an error's message on its own line
    (``f"{message}\\n{hint(...)}"``).
    """
    return f"hint: {run_hint(command)}"


_FUTURE_MAJOR = "a future major release"


def removal_phrase(removal: str | None = None) -> str:
    """``removed in <removal>``, or ``removed in a future major release`` when undated.

    ``removal`` is :func:`untaped.sdk.marks.removal_text`'s (``12.0``,
    ``untaped-awx 12.0``). For short notes such as ``config list``'s and
    doctor's; a sentence uses :func:`deprecated_message`.
    """
    return f"removed in {removal or _FUTURE_MAJOR}"


def deprecated_message(
    subject: str, replacement: str | None = None, removal: str | None = None
) -> str:
    """The deprecation sentence: ``<subject> is deprecated and will be removed in <removal>…``.

    One wording for every deprecated thing (commands, flags, config keys and
    their environment variables); callers pass the spellings as shown.
    ``removal`` is :func:`untaped.sdk.marks.removal_text`'s (``12.0``,
    ``untaped-awx 12.0``); without one the sentence names no date: "a future
    major release".
    """
    message = f"{subject} is deprecated and will be {removal_phrase(removal)}"
    return message if replacement is None else f"{message}; use {replacement}"


def deprecated_ignored_message(old: str, kept: str, new: str, removal: str | None = None) -> str:
    """An old key set beside the spelling that wins: ``<old> is deprecated and ignored …``.

    ``kept`` is the spelling whose value was read; when it is not ``new``,
    the sentence also says to use ``new``.
    """
    advice = "" if kept == new else f"; use {new}"
    return (
        f"{old} is deprecated and ignored because {kept} is also set{advice}; "
        f"it is {removal_phrase(removal)}"
    )


EXPERIMENTAL_LINE = "Experimental: may change in a minor release."
"""The help line every experimental command ends with."""


def deprecated_line(replacement: str | None = None, removal: str | None = None) -> str:
    """The help line every deprecated thing ends with: ``Deprecated: removed in <removal>….``"""
    suffix = "" if replacement is None else f"; use {replacement}"
    return f"Deprecated: {removal_phrase(removal)}{suffix}."


def shown_path(path: Path | str) -> str:
    """``path`` with the home directory written ``~``, as people read it."""
    path = Path(path)
    home = Path.home()
    if path == home:
        return "~"
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def size_text(size: int) -> str:
    """A byte count in binary units: ``512 B``, ``1.5 KiB``, ``2.0 GiB``."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{size} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def duration_text(seconds: float) -> str:
    """A length of time in its largest whole unit: ``45 s``, ``2 min``, ``26 h``, ``3 d``.

    Days start at two, so a day and a bit still reads in hours (``26 h``).
    """
    whole = max(int(seconds), 0)
    if whole >= 2 * 86400:
        return f"{whole // 86400} d"
    if whole >= 3600:
        return f"{whole // 3600} h"
    if whole >= 60:
        return f"{whole // 60} min"
    return f"{whole} s"


def elapsed_text(seconds: float) -> str:
    """A measured run time, to the tenth under a minute: ``42.1s``, ``1m42s``, ``2h05m``."""
    if round(seconds, 1) < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def ago_text(at: datetime, now: datetime) -> str:
    """How long before ``now`` ``at`` was: ``3 h ago``; a naive ``at`` is UTC."""
    aware = at if at.tzinfo else at.replace(tzinfo=UTC)
    return f"{duration_text((now - aware).total_seconds())} ago"


def summary(operation: str, counts: Mapping[str, int]) -> str:
    """A batch summary line: ``<operation>: 2 cloned, 1 failed``.

    Zero counts are dropped; when every count is zero the line reads
    ``<operation>: nothing to do``.
    """
    parts = [f"{count} {outcome}" for outcome, count in counts.items() if count]
    return f"{operation}: {', '.join(parts) if parts else 'nothing to do'}"


__all__ = [
    "EXPERIMENTAL_LINE",
    "ago_text",
    "command_argv",
    "command_line",
    "deprecated_ignored_message",
    "deprecated_line",
    "deprecated_message",
    "duration_text",
    "elapsed_text",
    "hint",
    "not_found",
    "plural",
    "q",
    "removal_phrase",
    "size_text",
    "split_profile",
    "summary",
]
