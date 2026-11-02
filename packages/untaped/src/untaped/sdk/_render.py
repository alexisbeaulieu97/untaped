"""How core renders rows: a table or a detail view, and the columns ``--columns`` picks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel

from untaped.runtime.render import column_value, render_collection
from untaped.runtime.run_output import Diagnostics, count_row_failures, raise_usage

# re-exported: untaped.sdk's fields import no runtime
from untaped.runtime.theme import OutputFormat as OutputFormat
from untaped.runtime.theme import TreeOutputFormat
from untaped.sdk._rows import (
    _as_row,
    _model_fields,
    _record_table_columns,
    _row_glyphs,
    _validate_kind,
)
from untaped.sdk._theme import ThemeSpec
from untaped.sdk._ui import Ui, build_terminal, collection, detail
from untaped.sdk.records import TableGlyph


def render_rows(
    rows: Sequence[dict[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
) -> str:
    """Render a row collection: themed table for humans, plain output for pipes.

    Only ``table`` goes through the settings-resolved :func:`build_terminal` —
    structured formats (json, raw, pipe, ...) must stay byte-stable regardless of
    the active theme, so they render through a bare :class:`~untaped.sdk._ui.Ui`.
    ``empty`` is a human hint printed to stderr only when ``table`` output has no rows.
    ``kind`` tags ``--format pipe`` records with a producer hint (ignored by
    every other format).

    ``table_columns`` are the columns a ``table`` shows by default (every
    other format keeps the whole record); ``--columns +name`` / ``-name``
    edit them. A ``table`` never shows a failed row's structured ``error``
    unless asked (its ``detail`` says the same for humans), and, with
    ``untaped.ui.hide_empty_columns`` (the default), leaves out a column empty on
    every row unless ``--columns`` names it.
    """
    _validate_kind(kind)
    return _render(
        rows,
        single=False,
        fmt=fmt,
        columns=columns,
        empty=empty,
        kind=kind,
        table_columns=table_columns,
        schema=None,
        strict=False,
        glyphs=None,
        ui=None,
    )


def _render(
    rows: Sequence[dict[str, object]],
    *,
    single: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
    empty: str | bool | None,
    kind: str | None,
    table_columns: Sequence[str] | None,
    schema: Sequence[str] | None,
    strict: bool,
    glyphs: Sequence[Mapping[str, TableGlyph]] | None,
    ui: Ui | None,
) -> str:
    """Render ``rows`` as a collection, or its one row as a detail view (``single``).

    ``glyphs`` holds each row's :class:`TableGlyph` fields, applied in a
    ``table`` only, after empty columns are hidden. ``schema`` and ``strict``
    check ``columns`` as :func:`_check_columns` says.
    """
    if columns == ["?"]:
        known = schema or _row_keys(rows) or table_columns or ()
        _print_available_columns(known, defaults=table_columns)
        return ""
    # Before a table's list view trims each row to its shown keys, ``error`` included.
    count_row_failures(rows)
    selection, named = _selected_columns(
        columns, rows, fmt=fmt, schema=schema, strict=strict, table_columns=table_columns
    )
    if fmt == "table":
        ui = ui or build_terminal()
        rows, selection = _table_layout(rows, selection, named, theme=ui.theme)
        if glyphs:
            rows = [_glyphed(row, glyph) for row, glyph in zip(rows, glyphs, strict=True)]
    else:
        ui = Ui()
    if single:
        return detail(ui, rows[0], fmt=fmt, columns=selection, kind=kind)
    return collection(ui, rows, fmt=fmt, columns=selection, empty=empty, kind=kind)


def _table_layout(
    rows: Sequence[dict[str, object]],
    selection: list[str] | None,
    named: frozenset[str],
    *,
    theme: ThemeSpec,
) -> tuple[Sequence[dict[str, object]], list[str] | None]:
    """A table's rows and columns in ``theme``: empty columns hidden, the list view's own keys."""
    shown = _table_columns(rows, selection, named, hide_empty=theme.hide_empty_columns)
    if selection is None and theme.collection_view == "list" and shown is not None:
        # A record list shows each row's own keys, not the union of them all.
        kept = set(shown)
        return [{key: value for key, value in row.items() if key in kept} for row in rows], None
    return rows, shown


def stderr_table(records: Sequence[BaseModel], *, colorize: bool, plain: bool = False) -> str:
    """``records`` as the table core prints on stdout, for stderr: their ``Column`` fields.

    The run's theme lays it out (empty columns hidden, the list view, glyphs);
    a broken ``ui`` section draws the default. ``plain`` gives the text a
    structured diagnostic carries instead: one ``key: value`` block per record.
    """
    theme = ThemeSpec(collection_view="list") if plain else build_terminal(strict=False).theme
    rows = [_as_row(record) for record in records]
    shown, selection = _table_layout(rows, _record_table_columns(records), frozenset(), theme=theme)
    glyphs = _row_glyphs(records)
    if glyphs:
        shown = [_glyphed(row, glyph) for row, glyph in zip(shown, glyphs, strict=True)]
    return render_collection(shown, fmt="table", columns=selection, theme=theme, colorize=colorize)


def _selected_columns(
    columns: list[str] | None,
    rows: Sequence[Mapping[str, object]],
    *,
    fmt: OutputFormat,
    schema: Sequence[str] | None,
    strict: bool,
    table_columns: Sequence[str] | None,
) -> tuple[list[str] | None, frozenset[str]]:
    """The columns to render (``None``: the whole record) and those the user named.

    ``-c a,b`` and ``-c a -c b`` select exactly those columns. ``-c +a``
    adds to the default columns and ``-c=-a`` (or ``-c +b,-a``) removes
    from them; the defaults are ``table_columns`` for a ``table`` or ``raw``
    (a line of tab-separated columns), else the whole record. Mixing names
    and edits is a usage error.
    """
    default = list(table_columns) if fmt == "table" and table_columns else None
    if columns is None:
        return default, frozenset()
    if fmt == "raw" and table_columns:
        default = list(table_columns)  # edits start from the defaults in raw too
    names = _column_names(columns)
    edits = [name for name in names if name[0] in "+-"]
    _check_columns(
        [name.lstrip("+-") for name in names], rows, fmt=fmt, schema=schema, strict=strict
    )
    if not edits:
        return names or None, frozenset(names)
    added = [name[1:] for name in edits if name[0] == "+"]
    removed = {name[1:] for name in edits if name[0] == "-"}
    base = default or [
        name
        for name in (_row_keys(rows) or schema or ())
        if fmt != "table" or not _is_error_column(rows, name)
    ]
    kept = [name for name in base if name not in removed]
    selection = kept + [name for name in added if name not in kept]
    if not selection:
        raise_usage(f"--columns removes every column: {', '.join(names)}")
    return selection, frozenset(added)


def _column_names(columns: list[str]) -> list[str]:
    """The names ``--columns`` gives, split on commas; names and edits never mix."""
    names = [part.strip() for entry in columns for part in entry.split(",") if part.strip()]
    edits = [name for name in names if name[0] in "+-"]
    if edits and len(edits) != len(names):
        raise_usage(
            "--columns takes either column names or +name/-name edits of the defaults, "
            f"not both: {', '.join(names)}"
        )
    return names


def check_columns(
    model: type[BaseModel], *, columns: list[str] | None, fmt: TreeOutputFormat
) -> None:
    """Refuse a ``--columns`` name no ``model`` field has, before the command acts.

    :func:`emit` checks columns only when it renders. A command whose rows
    follow a side effect but are not :class:`~untaped.sdk.records.OutcomeRecord`
    rows (which only warn) calls this first, so the usage error (exit 2) comes
    before anything changed, as the exit code promises. ``+name``/``-name``
    edits are checked by name; ``pipe`` and ``--columns ?`` are left alone.
    A ``tree`` has no columns: any ``--columns`` is a usage error. A model
    with ``extra="allow"`` has an open schema, so its rows are checked when
    they render, as mapping rows are.
    """
    if fmt == "tree":
        _refuse_tree_columns(columns)
        return
    if columns is None or columns == ["?"] or fmt == "pipe":
        return
    if model.model_config.get("extra") == "allow":
        return
    names = [name.lstrip("+-") for name in _column_names(columns)]
    _check_columns(names, [], fmt=fmt, schema=_model_fields(model), strict=True)


def _check_columns(
    names: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    *,
    fmt: OutputFormat,
    schema: Sequence[str] | None,
    strict: bool,
) -> None:
    """Check ``--columns`` names against the record fields.

    A name whose first dotted segment is not one of the ``schema`` fields
    (pydantic records), or else of any row's keys, is unknown. It is a usage
    error (exit 2) listing the valid columns when ``strict``, else a warning:
    plain mapping rows can be sparse (an API may omit a field on some
    records), and rows reporting a change come after it (see :func:`emit`).
    ``pipe`` ignores columns and is never checked.
    """
    if fmt == "pipe" or (schema is None and not rows):
        return
    known = list(schema) if schema is not None else _row_keys(rows)
    unknown = [name for name in names if name not in known and name.split(".", 1)[0] not in known]
    if not unknown:
        return
    plural = "s" if len(unknown) > 1 else ""
    message = (
        f"unknown column{plural} {', '.join(repr(name) for name in unknown)}; "
        f"valid columns: {', '.join(known)}"
    )
    if strict:
        raise_usage(message)
    Diagnostics().warning(message)


def _table_columns(
    rows: Sequence[Mapping[str, object]],
    selection: list[str] | None,
    named: frozenset[str],
    *,
    hide_empty: bool,
) -> list[str] | None:
    """The columns a ``table`` shows (``None``: every key of every row).

    Without a selection that is the union of the rows' keys, less a failed
    row's structured ``error``. ``hide_empty`` then drops a column that is
    empty on every row, unless the user ``named`` it.
    """
    if not rows:
        return selection
    if selection is None:
        selection = [key for key in _row_keys(rows) if not _is_error_column(rows, key)]
    if hide_empty:
        shown = [
            name
            for name in selection
            if name in named or any(not _is_empty(column_value(row, name)) for row in rows)
        ]
        selection = shown or selection
    return selection


def _glyphed(row: dict[str, object], glyphs: Mapping[str, TableGlyph]) -> dict[str, object]:
    """``row`` with each :class:`TableGlyph` field shown as its glyph."""
    return {key: glyphs[key].show(value) if key in glyphs else value for key, value in row.items()}


def _row_keys(rows: Iterable[Mapping[str, object]]) -> list[str]:
    return list(dict.fromkeys(key for row in rows for key in row))


def _is_error_column(rows: Sequence[Mapping[str, object]], key: str) -> bool:
    """Whether ``key`` is the ``error`` of failed rows (a ``Failure`` mapping)."""
    return key == "error" and all(
        row.get(key) is None or isinstance(row.get(key), Mapping) for row in rows
    )


def _is_empty(value: object) -> bool:
    return value is None or value == "" or (isinstance(value, list | tuple | dict) and not value)


def _print_available_columns(keys: Iterable[str], *, defaults: Sequence[str] | None) -> None:
    """Print the addressable top-level column names to stderr (for ``--columns ?``).

    With a command's default ``table`` columns, those are marked ``*``.
    """
    diag = Diagnostics()
    names = list(dict.fromkeys(keys))
    if not names:
        diag.info("no columns available (no records to inspect)", unmuted=True)
        return
    marked = set(defaults or ())
    diag.info(
        "available columns (* = shown by default):" if marked else "available columns:",
        unmuted=True,
    )
    for name in names:
        diag.info(f"  {name} *" if name in marked else f"  {name}", unmuted=True)


def _refuse_tree_columns(columns: list[str] | None) -> None:
    if columns is not None:
        raise_usage("--columns does not apply to --format tree")
