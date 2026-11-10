"""How a command prints: its records (``emit``), a line (``echo``), a declined confirmation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import overload

from pydantic import BaseModel

# re-exported: untaped.sdk's fields import no runtime
from untaped.runtime.run_output import count_failed_actions as count_failed_actions
from untaped.runtime.run_output import count_row_failures as count_row_failures
from untaped.runtime.run_output import current_sink
from untaped.runtime.theme import OutputFormat, TreeOutputFormat
from untaped.sdk._render import _refuse_tree_columns, _render
from untaped.sdk._rows import (
    _as_row,
    _model_schema,
    _record_table_columns,
    _reports_change,
    _row_glyphs,
    _rows_kind,
    _validate_kind,
)
from untaped.sdk._run import note_printed
from untaped.sdk._ui import Ui
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import OperationCancelledError
from untaped.sdk.tree import TreeGraph, print_tree


def echo(message: object = "", *, nl: bool = True) -> None:
    """Print a CLI message to stdout; stderr goes through ``ctx.diag``.

    Each message is flushed, so lines a command prints as it goes stream
    through a pipe.
    """
    note_printed()
    print(message, end="\n" if nl else "", flush=True)


def report_declined(exc: OperationCancelledError) -> None:
    """Print a declined confirmation: its bare message (a JSON error line under JSON).

    It counts toward the exit code like any failure.
    """
    # A declined confirmation is the user's choice, not an error: no prefix.
    from untaped.runtime.render import printable  # noqa: PLC0415 - rendering is heavy

    current_sink().emit(Failure.from_exception(exc), rendered=printable(str(exc)))


@overload
def emit(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
    tree: TreeGraph | Sequence[TreeGraph] | None = None,
) -> None: ...
@overload
def emit(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    fmt: TreeOutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
    tree: TreeGraph | Sequence[TreeGraph],
) -> None: ...
def emit(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    fmt: TreeOutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
    tree: TreeGraph | Sequence[TreeGraph] | None = None,
) -> None:
    """Render records to stdout, dispatching by shape.

    A single model or mapping renders as a vertical ``key: value`` detail view
    (a bare object under structured formats); a sequence renders as a collection
    (themed table for humans, array/NDJSON for pipes). Accepts pydantic models
    directly — no manual ``model_dump()`` — and writes the result itself, so
    there is no "forgot to ``echo``" silent-no-output trap. ``empty``,
    ``kind`` and ``table_columns`` behave as in :func:`render_rows`; ``empty``
    applies to a sequence only. Rows of a :class:`~untaped.sdk.records.Record`
    type that declares a kind carry it, so ``kind`` is only for plain models,
    mappings and kind-less records; one that names another kind than a row
    declares is an error. Without ``table_columns``, a collection of
    records shows their type's ``Column`` fields; a table shows a field's
    :class:`~untaped.sdk.records.TableGlyph` instead of its value.

    ``tree`` is the same records as graphs (:class:`~untaped.sdk.tree.TreeGraph`),
    drawn by ``fmt="tree"`` and ignored by every other format, so a command
    always passes it; ``fmt="tree"`` without it is a ``ValueError``. Failed
    rows set the exit code in a tree too, and ``--columns`` does not apply
    to one (a usage error, exit 2).

    An unknown ``columns`` name on pydantic rows is a usage error (exit 2)
    listing the valid columns. On mapping rows, and on any
    :class:`~untaped.sdk.records.OutcomeRecord` row, it only warns and the rows
    still print: outcome rows come after the change they report, and exit 2
    means nothing ran (a ``--dry-run`` plan warns alike, as its run would).
    """
    _emit(
        records,
        ui=None,
        fmt=fmt,
        columns=columns,
        empty=empty,
        kind=kind,
        table_columns=table_columns,
        tree=tree,
    )


@overload
def emit_with(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    ui: Ui | None,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
    tree: TreeGraph | Sequence[TreeGraph] | None = None,
    spread_keys: Sequence[str] | None = None,
) -> None: ...
@overload
def emit_with(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    ui: Ui | None,
    fmt: TreeOutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
    tree: TreeGraph | Sequence[TreeGraph],
    spread_keys: Sequence[str] | None = None,
) -> None: ...
def emit_with(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    ui: Ui | None,
    fmt: TreeOutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
    tree: TreeGraph | Sequence[TreeGraph] | None = None,
    spread_keys: Sequence[str] | None = None,
) -> None:
    """:func:`emit` rendering a ``table`` or ``tree`` through ``ui`` (``None``: the active theme).

    For root commands that must render even when the settings are broken.
    ``spread_keys`` are the ``Spread`` keys the command named, which are the
    columns ``--columns`` picks among (``None``: the keys the rows have).
    """
    _emit(
        records,
        ui=ui,
        fmt=fmt,
        columns=columns,
        empty=empty,
        kind=kind,
        table_columns=table_columns,
        tree=tree,
        spread_keys=spread_keys,
    )


def _emit(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    ui: Ui | None,
    fmt: TreeOutputFormat,
    columns: list[str] | None,
    empty: str | bool | None,
    kind: str | None,
    table_columns: Sequence[str] | None,
    tree: TreeGraph | Sequence[TreeGraph] | None,
    spread_keys: Sequence[str] | None = None,
) -> None:
    _validate_kind(kind)
    single = isinstance(records, BaseModel | Mapping)
    items: Sequence[BaseModel | Mapping[str, object]] = (
        [records] if isinstance(records, BaseModel | Mapping) else records
    )
    if fmt == "tree":
        if tree is None:
            raise ValueError("emit(fmt='tree') needs tree=, the records drawn as graphs")
        _refuse_tree_columns(columns)
        count_row_failures(items)
        print_tree(tree, ui=ui)
        return
    count_row_failures(items)
    kind = _rows_kind(items, kind)
    if table_columns is None and not single:
        table_columns = _record_table_columns(items)
    schema = _model_schema(records)
    rows = [_as_row(item) for item in items]
    if fmt == "table":
        from untaped.sdk._table import table_rows  # noqa: PLC0415 - fields import this module

        rows, table_columns, spread = table_rows(items, rows, table_columns)
        # the keys a command named are its columns (a key no row has is an empty
        # cell); without them, the keys the rows have
        named = spread if spread_keys is None else spread_keys
        if schema is not None and named:
            schema = [*schema, *(key for key in dict.fromkeys(named) if key not in schema)]
    rendered = _render(
        rows,
        single=single,
        fmt=fmt,
        columns=columns,
        empty=empty,
        kind=kind,
        table_columns=table_columns,
        schema=schema,
        strict=schema is not None and not _reports_change(items),
        glyphs=_row_glyphs(items) if fmt == "table" else None,
        ui=ui,
    )
    if rendered:
        echo(rendered)
