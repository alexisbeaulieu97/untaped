"""``assert_surface``: a plugin's own surface snapshot, guarded like untaped's.

Nothing here imports :mod:`untaped.surface` until it is called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.stability import experimental

if TYPE_CHECKING:
    from pathlib import Path

    from untaped.plugins.registry import PluginSpec


@experimental
def assert_surface(spec: PluginSpec, path: Path) -> None:
    """Fail when ``spec``'s plugin no longer keeps a stable line of the file at ``path``.

    The file lists the plugin's public surface, one promise per line: its
    commands and flags, its settings and state keys, the contracts it owns with
    their payload models, and its ``api`` and ``testing`` modules. A missing
    file is written and the test passes. An addition, a mark change or a
    format upgrade rewrites it and passes. A removed stable line (a dropped
    option or choice, a changed default, a new required parameter) fails,
    listing each line; to accept the removals in your own major release,
    delete the file and run again.

    Experimental, like the line format it writes, until untaped's record
    lines join it (#631) or 12.0, whichever comes first.
    """
    from untaped.surface import parse, plugin_lines, removed, render  # noqa: PLC0415

    current = plugin_lines(spec)
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
