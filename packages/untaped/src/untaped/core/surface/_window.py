"""The two-major window: what a release keeps of the lines plugins build on.

A plugin built on one major keeps working on the next, so a plugin-facing line
(:attr:`Line.plugin_facing`) deprecated for the first time names a removal at
least two majors past the one being released, and leaves the file only when a
recorded release marked it due. A changed meaning gets a new name: respelling
such a line removes it, so it needs a deprecation first. Within those rules:

- a parameter goes only with its callable, since a caller still passing it
  gets a ``TypeError`` core can't name; a field of a pydantic model that says
  ``extra=allow`` may go when due, since the caller keeps the value as an
  extra. On an ``extra=forbid`` model it goes only with its model, as a
  parameter does: a caller still passing it gets pydantic's validation
  error, which untaped can't tell from a user's mistyped key;
- an experimental line may go without warning, but never change shape under
  the same name: a plugin then meets a missing name rather than a value it
  misreads.

The release check (``tests/repo/test_surface.py``) and
:func:`untaped.sdk.testing.assert_surface` with ``version=`` both read
:func:`broken`.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from untaped.core.surface._guard import removed
from untaped.core.surface._lines import Line

#: Model settings that let a deprecated field go at its major: the caller keeps the value.
_FIELDS_MAY_GO = ("extra=allow",)


def broken(
    recorded: Sequence[Line],
    current: Sequence[Line],
    *,
    major: int,
    removals: bool = True,
    waived: Collection[str] = (),
) -> list[str]:
    """Each way ``current`` breaks the window ``recorded`` promised, one message each.

    ``major`` is the major being released. ``removals`` off checks only the
    window of new deprecations (a baseline from before the window existed);
    ``waived`` names lines that may go early (a reviewed security removal).
    """
    found = [
        f"{line}: plugins built on {major}.x must keep working on {major + 1}.x; "
        f"deprecate with removed_in={major + 2} and add a new name"
        for line in short_windows(recorded, current, major)
    ]
    if removals:
        found.extend(_gone(recorded, current, major, waived))
        found.extend(
            f"{line}: an experimental name plugins use changes only by a new name; "
            "add one beside it and remove this one"
            for line in respelled_in_place(recorded, current)
            if line.text not in waived
        )
    return found


def short_windows(recorded: Sequence[Line], current: Sequence[Line], major: int) -> list[Line]:
    """Plugin-facing lines first deprecated since ``recorded``, dated before ``major + 2``.

    A line ``recorded`` marked experimental promised nothing, so it may go at
    any date.
    """
    marked = {
        line.identity
        for line in recorded
        if line.deprecation is not None or line.mark == "experimental"
    }
    return sorted(
        line
        for line in current
        if line.plugin_facing
        and (mark := line.deprecation) is not None
        and mark.removed_in is not None
        and mark.removed_in < major + 2
        and line.identity not in marked
    )


def _gone(
    recorded: Sequence[Line], current: Sequence[Line], major: int, waived: Collection[str]
) -> list[str]:
    # a callable's owner line: its required parameters, keyed by its subject
    owners = {
        line.key
        for line in current
        if line.parameter is None and line.text.startswith(f"{line.key}(")
    }
    texts = {line.text for line in current}
    found: list[str] = []
    for line in removed(recorded, current):
        if not line.plugin_facing or line.text in waived:
            continue
        mark = line.deprecation
        if mark is None or mark.removed_in is None or mark.removed_in > major:
            found.append(
                f"{line}: a name plugins use goes only at the major its deprecation named; "
                f"keep it, or deprecate it with removed_in={major + 2} and add a new name"
            )
            continue
        parameter = line.parameter
        if parameter is None:
            continue
        callable_, name = parameter
        if f"{line.area} {callable_}" not in owners:
            continue  # it went with its callable
        if any(f"{line.area} {callable_} {extra}" in texts for extra in _FIELDS_MAY_GO):
            continue  # a model field: the caller keeps the value as an extra
        found.append(
            f"{line}: {callable_} stays, so a plugin still passing {name} gets a TypeError; "
            "a parameter goes only with its callable (deprecate the callable for a new one)"
        )
    return found


def respelled_in_place(recorded: Sequence[Line], current: Sequence[Line]) -> list[Line]:
    """Recorded experimental plugin-facing lines gone while a new line reuses their name."""
    present = {line.identity for line in current}
    old = [
        line
        for line in recorded
        if line.mark == "experimental" and line.plugin_facing and line.identity not in present
    ]
    if not old:
        return []
    before = {line.identity for line in recorded}
    new_keys = {line.key for line in current if line.identity not in before}
    # As stable lines, so the guard's relaxations (a required item made optional) apply.
    unmarked = [Line(line.text) for line in current]
    changed = {line.text for line in removed([Line(line.text) for line in old], unmarked)}
    return sorted(line for line in old if line.text in changed and line.key in new_keys)
