"""Compatibility helpers for stdout formatting.

Rendering is owned by :class:`untaped.ui.UiContext`; this module preserves the
older ``format_output`` entrypoint while keeping streams pipe-friendly.

Conventions:
- ``json`` and ``yaml`` produce structured output suitable for downstream parsing.
- ``raw`` produces newline-separated rows with tab-separated columns; this is the
  format you pipe into ``fzf``, ``cut``, or ``awk``.
- ``table`` produces a rich-rendered ASCII table for human consumption.
  Its width follows the ``COLUMNS`` env var (or the inherited TTY size);
  no hard-coded cap. Tests that need a stable render can pin ``COLUMNS``.
- ``pipe`` produces the self-describing NDJSON interchange stream (one
  ``{"untaped": ..., "kind": ..., "record": {...}}`` per line) for piping into
  another untaped command; pass ``kind`` to tag records. Ignores ``columns``.

If no ``columns`` are specified for ``raw``, the value of the first
key of each row is emitted — so the first key of every row is the
row's identifier (workspace name, job id, login, …) for the
``xargs``-into-the-next-command pipeline pattern. The full
default-column contract lives in the root ``AGENTS.md``.

Column names support dotted paths (``a.b.c``) to address nested dict
fields — e.g. ``--columns summary_fields.project.name``. Missing
intermediates resolve to ``None`` rather than erroring so a column
specification works uniformly across heterogeneous rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from untaped.render import OutputFormat
from untaped.theme import ThemeSpec
from untaped.ui import UiContext

Row = dict[str, object]

__all__ = ["OutputFormat", "format_output"]


def format_output(
    rows: Sequence[Row],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    theme: ThemeSpec | None = None,
    kind: str | None = None,
) -> str:
    """Render ``rows`` as a string in the requested format.

    ``kind`` tags ``--format pipe`` records with a producer hint; it is ignored
    by every other format.
    """
    return UiContext(theme=theme).collection(rows, fmt=fmt, columns=columns, kind=kind)
