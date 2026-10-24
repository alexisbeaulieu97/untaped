"""``assert_surface``: a plugin's own surface snapshot, guarded like untaped's.

Nothing here imports :mod:`untaped.core.surface` until it is called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from pathlib import Path

    from untaped.runtime.registry import PluginSpec


@experimental
def assert_surface(spec: PluginSpec, path: Path, version: str | None = None) -> None:
    """Fail when ``spec``'s plugin no longer keeps a stable line of the file at ``path``.

    The file lists the plugin's public surface, one promise per line: its
    commands and flags, its settings and state keys, the contracts it owns with
    their payload models, and its ``api`` and ``testing`` modules. A missing
    file is written and the test passes. An addition, a mark change or a
    format upgrade rewrites it and passes. A removed stable line (a dropped
    option or choice, a changed default, a new required parameter) fails,
    listing each line; to accept the removals in your own major release,
    delete the file and run again.

    With ``version`` (your distribution's, such as ``"2.3.0"``,
    ``"3.0.0rc1"`` or ``"2.3.1.dev5+g1234"``), every deprecation must name its
    removal version (``removed_in=``), and nothing may still be marked removed
    in that version's major or earlier: a pre-release of ``3.0.0`` already
    fails a ``removed_in=3`` that is still there.

    Experimental, like the line format it writes (#830).
    """
    from untaped.core.surface import due, parse, plugin_lines, removed, render  # noqa: PLC0415

    current = plugin_lines(spec)
    if version is not None:
        from packaging.version import Version  # noqa: PLC0415

        major = Version(version).major
        undated = sorted(
            line
            for line in current
            if (mark := line.deprecation) is not None and mark.removed_in is None
        )
        assert not undated, (
            f"{spec.name}: every deprecation is a promise with a date; pass removed_in=<major> "
            "to these marks:\n" + "\n".join(f"  {line}" for line in undated)
        )
        overdue = due(current, major)
        assert not overdue, (
            f"{spec.name} {version} removes what is marked for {major}.0 or earlier; these are "
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
            f"{spec.name} no longer keeps these lines of {path.name} (delete the file and run "
            "again to accept these in a major release):\n" + "\n".join(f"  {line}" for line in gone)
        )
        if recorded == text:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
