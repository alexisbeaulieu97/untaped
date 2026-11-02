"""Guards that take a plugin: ``check(install)`` and ``assert_surface(Plugin)``."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from untaped.runtime.registry import PluginCandidate
    from untaped.sdk.plugin import Plugin
    from untaped.sdk.testing.install import Install


def check(install: Install) -> None:
    """Fail with every failing plugin check row (:func:`check_plugin`) of the plugins installed.

    The rows are those only a composed install can answer (registration,
    setup marks, contracts, conformance, live); all of them run
    and every failure is listed, so an author fixes them in one pass. The
    rules that read only a plugin's files are ``untaped-lint``'s.
    """
    from untaped.core.plugin_check.check import check_plugins  # noqa: PLC0415
    from untaped.runtime import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.runtime.run import build_run, entered  # noqa: PLC0415

    found = list(install.candidates)
    with (
        bootstrap.composed_with(found) as result,
        # the live rows ask providers as a command of the install would, under its run
        entered(build_run(result.mount.declaration, result.mount.settings, result.mount.plugins)),
    ):
        rows = [row for candidate in found for row in check_plugins(result, found, candidate.name)]
    failed = [row for row in rows if row.status in ("fail", "error")]
    assert not failed, "plugin checks failed:\n" + "\n".join(
        f"  {row.plugin} {row.check} {row.title}: {row.detail}" for row in failed
    )


@experimental
def assert_surface(
    plugin: type[Plugin], path: Path | None = None, version: str | None = None
) -> None:
    """Fail when ``plugin`` no longer keeps a stable line of its surface snapshot.

    The snapshot is ``snapshots/surface.txt`` beside the calling test file
    (through any helpers it calls) unless ``path`` names another. It lists
    the plugin's public surface, one promise per line: its commands and
    flags, its settings and state keys, its record kinds, the contracts it
    owns with their payload models, and its ``api`` and ``testing`` modules.
    A missing file is written and the test passes. An addition, a mark
    change or a format upgrade rewrites it and passes. A removed stable line
    (a dropped option or choice, a changed default, a new required
    parameter) fails, listing each line; pass ``version`` to accept what your
    release may remove (without it, deleting the file accepts everything and
    forgets every date).

    With ``version`` (your distribution's, such as ``"2.3.0"``,
    ``"3.0.0rc1"`` or ``"2.3.1.dev5+g1234"``), every deprecation must name its
    removal version (``removed_in=``), and nothing may still be marked removed
    in that version's major or earlier: a pre-release of ``3.0.0`` already
    fails a ``removed_in=3`` that is still there. Against the file, a shown
    removal version never falls, and the names plugins use (your ``api`` and
    ``testing`` modules, your contracts) keep two majors: a new deprecation
    names ``removed_in`` at least two past ``version``'s major, such a name
    goes only when the file marks it due, an experimental one changes only by
    a new name, and a parameter goes only with its function (a field of a
    model with ``extra="forbid"`` or ``"allow"`` may go when due). Any other
    line may go when the file marks it due, or at a major's first release
    (``3.0.0``, ``3.0.0rc1``); the file is then rewritten without it.

    Experimental, like the line format it writes (#830).
    """
    from untaped.core.surface import (  # noqa: PLC0415
        broken,
        due,
        parse,
        plugin_lines,
        postponements,
        removed,
        render,
    )

    if path is None:
        path = _calling_test().parent / "snapshots" / "surface.txt"
    target = _candidate(plugin)
    name = target.name
    current = plugin_lines(target)
    major = None if version is None else _major(version)
    if major is not None:
        undated = sorted(
            line
            for line in current
            if (mark := line.deprecation) is not None and mark.removed_in is None
        )
        assert not undated, (
            f"{name}: every deprecation is a promise with a date; pass removed_in=<major> "
            "to these marks:\n" + "\n".join(f"  {line}" for line in undated)
        )
        overdue = due(current, major[0])
        assert not overdue, (
            f"{name} {version} removes what is marked for {major[0]}.0 or earlier; these are "
            "still here (remove them, or raise removed_in on purpose):\n"
            + "\n".join(f"  {line}" for line in overdue)
        )
    text = render(current)
    try:
        recorded = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        recorded = None
    if recorded is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    before = parse(recorded)
    gone = removed(before, current)
    if major is None:
        assert not gone, (
            f"{name} no longer keeps these lines of {path.name} (pass version= to accept a "
            "due line or a major's removals, or delete the file and run again):\n"
            + "\n".join(f"  {line}" for line in gone)
        )
    else:
        number, at_major = major
        found = [
            f"{moved.line}: {path.name} promised {moved.was}.0; a shown promise never shortens"
            for moved in postponements(before, current)
            if moved.now < moved.was
        ]
        found.extend(broken(before, current, major=number))
        # a name plugins use was judged above; anything else goes when due, or at a major
        found.extend(
            f"{line}: a minor keeps it; "
            + (
                f"it goes at {mark.removed_in}.0"
                if (mark := line.deprecation) is not None and mark.removed_in is not None
                else f"deprecate it with removed_in={number + 1} and remove it then"
            )
            for line in gone
            if not line.plugin_facing and not at_major and not due([line], number)
        )
        assert not found, f"{name} {version}:\n" + "\n".join(f"  {each}" for each in found)
    if recorded != text:
        path.write_text(text, encoding="utf-8")


def _major(version: str) -> tuple[int, bool]:
    """``version``'s major, and whether it is that major's first release (``3.0.0rc1`` too)."""
    from packaging.version import Version  # noqa: PLC0415

    parsed = Version(version)
    return parsed.major, all(part == 0 for part in parsed.release[1:])


def _calling_test() -> Path:
    """The test module calling (through any helpers), else the direct caller's file."""
    here = inspect.currentframe()
    assert here is not None and here.f_back is not None  # CPython keeps frames
    caller = here.f_back.f_back  # past assert_surface
    first: Path | None = None
    while caller is not None:
        file = Path(caller.f_code.co_filename)
        first = first or file
        if file.name.startswith("test_") or file.stem.endswith("_test"):
            return file
        caller = caller.f_back
    assert first is not None
    return first


def _candidate(plugin: type[Plugin]) -> PluginCandidate:
    """``plugin`` as ``install`` names it: its entry point's name, else its class name."""
    from untaped.runtime.registry import discover_candidates  # noqa: PLC0415
    from untaped.sdk.testing.install import candidate_of  # noqa: PLC0415

    return candidate_of(plugin, None, discover_candidates())
