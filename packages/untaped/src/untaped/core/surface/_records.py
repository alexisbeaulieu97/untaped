"""``record`` lines: the kinds untaped prints and reads, and their fields (#631).

A 12.0 plugin's kinds come from its composed declaration: a kind some
command reads on ``--stdin`` is written by scripts, so its owner line carries
its required fields and its field lines their defaults; a kind it only
prints has a bare owner line. Core's diagnostic kinds (``untaped.failure``,
``untaped.progress``...) are printed on stderr and parsed like records. An
11.x plugin's records join when the plugin moves to the 12.0 shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from untaped.core.surface._contracts import model_lines

if TYPE_CHECKING:
    from untaped.core.surface._lines import Line, LineMark
    from untaped.runtime.registry import CompositionResult


def record_lines(result: CompositionResult, marks: Mapping[str, LineMark | None]) -> list[Line]:
    """The ``record`` lines of core's diagnostic kinds and of ``result``'s declared kinds."""
    lines = _diagnostic_lines()
    mount = result.mount
    if mount is None:
        return lines
    declaration = mount.declaration
    read = {target for command in declaration.commands() for target in command.stdin}
    for each in declaration.kinds:
        model = mount.kinds[each.kind]
        lines.extend(
            model_lines(
                f"record {each.kind}", model, marks.get(each.plugin), written=each.target in read
            )
        )
    return lines


def _diagnostic_lines() -> list[Line]:
    from untaped.sdk.diagnostic import Diagnostic, record_kinds  # noqa: PLC0415

    return [
        line
        for kind, model in sorted(record_kinds().items())
        if kind.startswith("untaped.") and issubclass(model, Diagnostic)
        for line in model_lines(f"record {kind}", model, None, written=False)
    ]
