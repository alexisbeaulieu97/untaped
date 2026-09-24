"""Message helpers that keep stderr wording consistent across the suite.

Every capability phrases counts, quoted names, "not found" errors, hints and
summaries through these helpers instead of hand-rolled f-strings, so the
conventions in ``docs/conventions.md`` hold in one place:

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

from collections.abc import Iterable, Mapping

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


def hint(command: str) -> str:
    """A follow-up hint line: ``hint: run `untaped <command>```.

    ``command`` may include or omit the leading ``untaped``. Append it to an
    error on its own line (``f"{message}\\n{hint(...)}"``) or print it as a
    separate stderr line.
    """
    text = command.strip()
    if text != _ROOT_COMMAND and not text.startswith(f"{_ROOT_COMMAND} "):
        text = f"{_ROOT_COMMAND} {text}"
    return f"hint: run `{text}`"


def summary(operation: str, counts: Mapping[str, int]) -> str:
    """A batch summary line: ``<operation>: 2 cloned, 1 failed``.

    Zero counts are dropped; when every count is zero the line reads
    ``<operation>: nothing to do``.
    """
    parts = [f"{count} {outcome}" for outcome, count in counts.items() if count]
    return f"{operation}: {', '.join(parts) if parts else 'nothing to do'}"


__all__ = ["hint", "not_found", "plural", "q", "summary"]
