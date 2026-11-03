"""A streamed Output: rows printed as they come, one line each, in the table's columns."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from untaped.sdk._output import Output, print_rendered
from untaped.sdk._run import note_printed

if TYPE_CHECKING:
    from untaped.sdk._render import OutputFormat


class _Stream:
    """One streamed run's printing: each row as it comes, the table's header once."""

    def __init__(self, output: Output[Any]) -> None:
        self.output = output
        self.rows = 0
        #: A streamed table's columns and their widths, from its first row.
        self.widths: dict[str, int] | None = None

    def row(self, record: BaseModel, *, kind: str | None) -> None:
        """Print ``record``, the next row ``run`` yielded, and flush it."""
        from untaped.sdk._emit import (  # noqa: PLC0415 - rendering is heavy
            count_failed_actions,
            count_row_failures,
            emit_with,
        )

        output = self.output
        note_printed()
        count_failed_actions([record])
        renderer = output.render.get(output.format)
        if output.format == "table" and output._requested is not None:
            renderer = None  # --columns asks for core's table, whatever replaced it
        if renderer is not None:
            count_row_failures([record])
            print_rendered(renderer(record))
        elif output.format == "table":
            count_row_failures([record])
            self._table_line(record)
        else:
            if output.format == "yaml" and self.rows:
                print("---")
            single = output.format in ("json", "yaml")
            emit_with(
                record if single else [record],
                ui=None,
                fmt=cast("OutputFormat", output.format),
                columns=output._requested,
                kind=kind,
                table_columns=list(output.columns) or None,
            )
        self.rows += 1
        sys.stdout.flush()

    def _table_line(self, record: BaseModel) -> None:
        """One row of a streamed table: its cells padded to the widths the first row set."""
        from rich.cells import cell_len  # noqa: PLC0415 - rendering is heavy

        from untaped.sdk._render import _selected_columns  # noqa: PLC0415 - heavy
        from untaped.sdk._rows import _as_row  # noqa: PLC0415 - heavy

        row = _as_row(record)
        if self.widths is None:
            names, _ = _selected_columns(
                self.output._requested,
                [row],
                fmt="table",
                schema=None,
                strict=False,
                table_columns=list(self.output.columns) or None,
            )
            names = names or list(row)
            self.widths = {
                name: max(cell_len(name), cell_len(_cell(row.get(name)))) for name in names
            }
            print(_line({name: name for name in self.widths}, self.widths))
        print(_line({name: _cell(row.get(name)) for name in self.widths}, self.widths))


def _line(cells: dict[str, str], widths: dict[str, int]) -> str:
    """A streamed table's line: each cell padded to its column's width in terminal cells."""
    from rich.cells import cell_len  # noqa: PLC0415 - rendering is heavy

    return "  ".join(
        text + " " * (widths[name] - cell_len(text)) for name, text in cells.items()
    ).rstrip()


def _cell(value: object) -> str:
    """A streamed table's cell: the value's text on one line (empty for ``None``)."""
    return "" if value is None else str(value).replace("\n", " ")
