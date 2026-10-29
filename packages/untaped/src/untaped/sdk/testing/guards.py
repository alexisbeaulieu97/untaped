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
        entered(build_run(result.mount.declaration, result.mount.settings)),
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
    parameter) fails, listing each line; to accept the removals in your own
    major release, delete the file and run again.

    With ``version`` (your distribution's, such as ``"2.3.0"``,
    ``"3.0.0rc1"`` or ``"2.3.1.dev5+g1234"``), every deprecation must name its
    removal version (``removed_in=``), and nothing may still be marked removed
    in that version's major or earlier: a pre-release of ``3.0.0`` already
    fails a ``removed_in=3`` that is still there.

    Experimental, like the line format it writes (#830).
    """
    from untaped.core.surface import due, parse, plugin_lines, removed, render  # noqa: PLC0415

    if path is None:
        path = _calling_test().parent / "snapshots" / "surface.txt"
    target = _candidate(plugin)
    name = target.name
    current = plugin_lines(target)
    if version is not None:
        from packaging.version import Version  # noqa: PLC0415

        major = Version(version).major
        undated = sorted(
            line
            for line in current
            if (mark := line.deprecation) is not None and mark.removed_in is None
        )
        assert not undated, (
            f"{name}: every deprecation is a promise with a date; pass removed_in=<major> "
            "to these marks:\n" + "\n".join(f"  {line}" for line in undated)
        )
        overdue = due(current, major)
        assert not overdue, (
            f"{name} {version} removes what is marked for {major}.0 or earlier; these are "
            "still here (remove them, or raise removed_in on purpose):\n"
            + "\n".join(f"  {line}" for line in overdue)
        )
    text = render(current)
    try:
        recorded = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        recorded = None
    if recorded is not None:
        gone = removed(parse(recorded), current)
        assert not gone, (
            f"{name} no longer keeps these lines of {path.name} (delete the file and run "
            "again to accept these in a major release):\n" + "\n".join(f"  {line}" for line in gone)
        )
        if recorded == text:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
