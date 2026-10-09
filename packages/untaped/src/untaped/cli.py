"""CLI helpers shared by every Cyclopts command in the suite."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, Protocol, get_args, overload

from cyclopts import App, ArgumentCollection, Parameter, ResultAction, Token
from cyclopts.exceptions import CycloptsError
from cyclopts.validators import Number
from pydantic import BaseModel
from rich.console import Console

from untaped.diagnostics import (
    ErrorInfo,
    diagnostics_scope,
    error_record,
    failure_exit_code,
    format_error,
    json_diagnostics,
    line_record,
    note_failure,
    note_output_format,
    render_record,
    write_record,
)
from untaped.errors import ExitCode, OperationCancelledError, UntapedError, UsageError
from untaped.records import TABLE_CONTEXT, TableGlyph, check_data_kind, kind_of, table_columns_of
from untaped.render import column_value
from untaped.stability import Stability, check_stability, mark_app
from untaped.theme import OutputFormat
from untaped.ui import UiContext, ui_context


def _format_default(value: object) -> str:
    """Help text for a ``--format`` default; ``table`` defers to the overrides."""
    shown = getattr(value, "value", value)
    return "table, or UNTAPED_FORMAT / ui.format" if shown == "table" else str(shown)


FormatOption = Annotated[
    OutputFormat,
    Parameter(name=["--format", "-f"], help="Output format.", show_default=_format_default),
]
"""Shared ``--format / -f`` option for any command that prints rows."""

#: Environment variable naming the default ``--format`` (wins over ``ui.format``).
FORMAT_ENV = "UNTAPED_FORMAT"
#: Root commands that diagnose the setup: an invalid ``UNTAPED_FORMAT`` is
#: ignored there instead of blocking them.
_FORMAT_ENV_LENIENT = frozenset({"doctor", "setup"})


def apply_default_format(
    app: App, commands: tuple[str, ...], arguments: ArgumentCollection
) -> None:
    """Cyclopts config source: default an omitted shared ``--format``.

    Installed on the root app. Only a ``FormatOption`` whose command default
    is ``table`` follows the user's choice (a command defaulting to ``raw``
    or ``yaml`` keeps it): ``UNTAPED_FORMAT`` first, then the ``ui.format``
    setting. An explicit ``--format`` has tokens already and always wins. A
    ``ui`` section that fails to load is ignored here so ``config set`` can
    still repair it; ``doctor`` reports it. An invalid ``UNTAPED_FORMAT`` is
    a usage error, except under ``doctor`` and ``setup``, which ignore it.
    A ``--format`` the user chose (the flag, ``UNTAPED_FORMAT`` or
    ``ui.format``; not a command's own default) is recorded for the stderr
    diagnostics (:func:`untaped.diagnostics.note_output_format`).
    """
    del app
    _default_format(commands, arguments)
    for argument in arguments:
        if "--format" in argument.names and argument.tokens:
            note_output_format(str(argument.tokens[-1].value))


def _default_format(commands: tuple[str, ...], arguments: ArgumentCollection) -> None:
    """Append the user's default format to an omitted table-default ``FormatOption``."""
    argument = next(
        (
            argument
            for argument in arguments
            if "--format" in argument.names
            and not argument.tokens
            and argument.hint == OutputFormat
            and argument.field_info.default == "table"
        ),
        None,
    )
    if argument is None:
        return
    choices = get_args(OutputFormat)
    value = os.environ.get(FORMAT_ENV) or None
    source = FORMAT_ENV
    if value is not None and value not in choices:
        if commands[:1] and commands[0] in _FORMAT_ENV_LENIENT:
            value = None
        else:
            raise_usage(f"{FORMAT_ENV} must be one of {', '.join(choices)}; got {value!r}")
    if value is None:
        # Keep settings lazy: only commands printing rows need them here.
        from untaped.settings import load_settings_section  # noqa: PLC0415

        try:
            value = load_settings_section("ui").format
        except UntapedError:
            return
        source = "ui.format"
    if value is not None:
        argument.append(Token(keyword=source, value=value, source=source))


ColumnsOption = Annotated[
    list[str] | None,
    Parameter(
        name=["--columns", "-c"],
        negative="",
        help=(
            "Columns to include (repeatable or comma-separated); +name adds to and "
            "-name removes from the table's default columns (? lists them)."
        ),
        consume_multiple=False,
    ),
]
"""Shared ``--columns / -c`` option for any command that prints rows."""

YesOption = Annotated[
    bool,
    Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
]
"""Shared ``--yes / -y``: skip only the prompt (``--dry-run`` still wins)."""

DryRunOption = Annotated[
    bool,
    Parameter(name="--dry-run", negative="", help="Preview the changes without applying them."),
]
"""Shared ``--dry-run``: preview and exit ``0`` without side effects."""

StdinOption = Annotated[
    bool,
    Parameter(
        name="--stdin",
        negative="",
        help="Read identifiers, or --format pipe records, from stdin.",
    ),
]
"""Shared ``--stdin``: pair with :func:`untaped.stdin.read_identifiers`."""

ParallelOption = Annotated[
    int,
    Parameter(
        name=["--parallel", "-j"],
        help="Maximum number of operations to run at once.",
        validator=Number(gte=1),
    ),
]
"""Shared ``--parallel / -j`` (``>= 1``; cap it with :func:`clamp_parallel`)."""

LimitOption = Annotated[
    int | None,
    Parameter(
        name="--limit",
        help="Return at most this many results.",
        validator=Number(gte=1),
    ),
]
"""Shared ``--limit N`` (``>= 1``; ``None`` means no limit)."""


_WRITES_ATTR = "__untaped_writes__"
WriteKind = Literal["write", "destructive"]


@overload
def writes[F: Callable[..., Any]](func: F, /) -> F: ...
@overload
def writes[F: Callable[..., Any]](*, destructive: bool = False) -> Callable[[F], F]: ...
def writes[F: Callable[..., Any]](
    func: F | None = None, /, *, destructive: bool = False
) -> F | Callable[[F], F]:
    """Declare that a command writes; ``destructive=True`` for deletes and cancels.

    A declared write must take ``--format``; a destructive one must also take
    ``--yes`` and ``--dry-run``. A command exposing ``--yes`` or ``--dry-run``
    must be declared. ``untaped.testing.check_conventions`` enforces all three.
    Apply it under ``@app.command`` so the registered function carries the mark.
    """
    kind: WriteKind = "destructive" if destructive else "write"

    def mark(target: F) -> F:
        setattr(target, _WRITES_ATTR, kind)
        return target

    return mark(func) if func is not None else mark


def write_kind(func: object) -> WriteKind | None:
    """The ``writes`` declaration on a command function, if any."""
    return getattr(func, _WRITES_ATTR, None)


def create_app(*, name: str, help: str = "", stability: Stability | None = None) -> App:
    """Create a Cyclopts app with the suite's default command-group settings.

    ``stability`` marks the group experimental (``stability=experimental``) or
    deprecated (``stability=deprecated(replacement=...)``): core lists it in
    its stability panel and ends its ``--help`` with the matching line. Mark a
    whole plugin on its ``PluginSpec`` instead, never on the app its
    factory returns.
    """
    mark = check_stability(stability, where=f"create_app({name!r})")
    app = App(name=name, help=help)
    if mark is not None:
        mark_app(app, mark, source="own")
    return app


def echo(message: object = "", *, err: bool = False, nl: bool = True) -> None:
    """Print a CLI message to stdout or stderr.

    Under JSON diagnostics a stderr message becomes one JSON line, its level
    taken from an ``error:``, ``warning:`` or ``hint:`` prefix (else
    ``info``); a blank one is dropped.
    """
    if err and json_diagnostics():
        record = line_record(str(message))
        if record is not None:
            write_record(record)
        return
    end = "\n" if nl else ""
    print(message, file=sys.stderr if err else sys.stdout, end=end)


def report_error(
    exc: UntapedError | ErrorInfo,
    *,
    item: str | None = None,
    write: Callable[[str], None] | None = None,
) -> None:
    """Print one failure on stderr and count it toward the run's exit code.

    Text is ``error: <msg>`` (``error: <item>: <msg>`` for a per-item
    failure, as :func:`format_error` renders it); under JSON diagnostics it
    is one JSON line with the error's category, system and hint. A failed
    row's :class:`~untaped.diagnostics.ErrorInfo` reports the same way.
    ``write`` replaces the stderr print (e.g. a progress handle's ``log``).
    """
    note_failure(exc)
    if json_diagnostics():
        line = render_record(error_record(exc, item=item))
    else:
        prefix = "" if item is None else f"{item}: "
        line = f"error: {prefix}{_error_text(exc)}"
    if write is not None:
        write(line)
    else:
        print(line, file=sys.stderr)


def _error_text(failure: UntapedError | ErrorInfo) -> str:
    if isinstance(failure, UntapedError):
        return format_error(failure)
    return failure.message if failure.hint is None else f"{failure.message}\nhint: {failure.hint}"


class _FailableRow(Protocol):
    """A row that may carry the :class:`ErrorInfo` of its failure."""

    @property
    def error(self) -> ErrorInfo | None: ...


def report_row_errors[RowT: _FailableRow](
    rows: Iterable[RowT], *, item: Callable[[RowT], str]
) -> None:
    """:func:`report_error` each row that carries an ``error``, labelled ``item(row)``."""
    for row in rows:
        if row.error is not None:
            report_error(row.error, item=item(row))


def note_requested_format(tokens: Sequence[str]) -> None:
    """Record the ``--format`` raw ``tokens`` ask for, for an error found before parsing.

    The last ``--format``/``-f`` before ``--`` wins over ``UNTAPED_FORMAT``;
    only a structured value switches the error to a JSON line.
    """
    value = os.environ.get(FORMAT_ENV) or None
    for index, token in enumerate(tokens):
        if token == "--":
            break
        name, separator, inline = token.partition("=")
        if name in ("--format", "-f"):
            if separator:
                value = inline
            elif index + 1 < len(tokens):
                value = tokens[index + 1]
    note_output_format(value)


def report_declined(exc: OperationCancelledError) -> None:
    """Print a declined confirmation: its bare message (a JSON error line under JSON)."""
    if json_diagnostics():
        report_error(exc)
        return
    # A declined confirmation is the user's choice, not an error: no prefix.
    note_failure(exc)
    echo(str(exc), err=True)


def raise_usage(message: str) -> NoReturn:
    """Print ``error: <message>`` and exit ``2`` (usage) immediately.

    For code outside :func:`report_errors` (cyclopts validators, option
    parsing). Inside a ``report_errors`` block prefer
    ``raise UsageError(message)``, which exits the same way.
    """
    report_error(UsageError(message))
    raise SystemExit(ExitCode.USAGE)


def _validate_kind(kind: str | None) -> None:
    """Reject emit kinds that break the ``<plugin>.<noun>[.summary]`` grammar.

    Raises ``ValueError`` (not ``UntapedError``): a bad kind is a programming
    error that must surface at development time, not a user-facing condition
    for ``report_errors`` to soften.
    """
    if kind is not None:
        check_data_kind(kind)


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

    Only ``table`` goes through the settings-resolved :func:`ui_context` —
    structured formats (json, raw, pipe, ...) must stay byte-stable regardless of
    the active theme, so they render through a bare :class:`UiContext`. ``empty``
    is a human hint printed to stderr only when ``table`` output has no rows.
    ``kind`` tags ``--format pipe`` records with a producer hint (ignored by
    every other format).

    ``table_columns`` are the columns a ``table`` shows by default (every
    other format keeps the whole record); ``--columns +name`` / ``-name``
    edit them. A ``table`` never shows a failed row's structured ``error``
    unless asked (its ``detail`` says the same for humans), and, with
    ``ui.hide_empty_columns`` (the default), leaves out a column empty on
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
    glyphs: Sequence[Mapping[str, TableGlyph]] | None,
    ui: UiContext | None,
) -> str:
    """Render ``rows`` as a collection, or its one row as a detail view (``single``).

    ``glyphs`` holds each row's :class:`TableGlyph` fields, applied in a
    ``table`` only, after empty columns are hidden.
    """
    if columns == ["?"]:
        known = schema or _row_keys(rows) or table_columns or ()
        _print_available_columns(known, defaults=table_columns)
        return ""
    selection, named = _selected_columns(
        columns, rows, fmt=fmt, schema=schema, table_columns=table_columns
    )
    if fmt == "table":
        ui = ui or ui_context()
        shown = _table_columns(rows, selection, named, hide_empty=ui.theme.hide_empty_columns)
        if selection is None and ui.theme.collection_view == "list" and shown is not None:
            # A record list shows each row's own keys, not the union of them all.
            kept = set(shown)
            rows = [{key: value for key, value in row.items() if key in kept} for row in rows]
            shown = None
        selection = shown
        if glyphs:
            rows = [_glyphed(row, glyph) for row, glyph in zip(rows, glyphs, strict=True)]
    else:
        ui = UiContext()
    if single:
        return ui.detail(rows[0], fmt=fmt, columns=selection, kind=kind)
    return ui.collection(rows, fmt=fmt, columns=selection, empty=empty, kind=kind)


def _selected_columns(
    columns: list[str] | None,
    rows: Sequence[Mapping[str, object]],
    *,
    fmt: OutputFormat,
    schema: Sequence[str] | None,
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
    names = [part.strip() for entry in columns for part in entry.split(",") if part.strip()]
    edits = [name for name in names if name[0] in "+-"]
    if edits and len(edits) != len(names):
        raise_usage(
            "--columns takes either column names or +name/-name edits of the defaults, "
            f"not both: {', '.join(names)}"
        )
    _check_columns([name.lstrip("+-") for name in names], rows, fmt=fmt, schema=schema)
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


def _check_columns(
    names: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    *,
    fmt: OutputFormat,
    schema: Sequence[str] | None,
) -> None:
    """Check ``--columns`` names against the record fields.

    With a known ``schema`` (pydantic records) a name whose first dotted
    segment is not a field is a usage error (exit 2) listing the valid
    columns. Plain mapping rows can be sparse (an API may omit a field on
    some records), so a name absent from every row only warns. ``pipe``
    ignores columns and is never checked.
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
    if schema is not None:
        raise_usage(message)
    echo(f"warning: {message}", err=True)


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
    """Whether ``key`` is the ``error`` of failed rows (an ``ErrorInfo`` mapping)."""
    return key == "error" and all(
        row.get(key) is None or isinstance(row.get(key), Mapping) for row in rows
    )


def _is_empty(value: object) -> bool:
    return value is None or value == "" or (isinstance(value, list | tuple | dict) and not value)


def _print_available_columns(keys: Iterable[str], *, defaults: Sequence[str] | None) -> None:
    """Print the addressable top-level column names to stderr (for ``--columns ?``).

    With a command's default ``table`` columns, those are marked ``*``.
    """
    names = list(dict.fromkeys(keys))
    if not names:
        echo("no columns available (no records to inspect)", err=True)
        return
    marked = set(defaults or ())
    echo("available columns (* = shown by default):" if marked else "available columns:", err=True)
    for name in names:
        echo(f"  {name} *" if name in marked else f"  {name}", err=True)


def emit(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
) -> None:
    """Render records to stdout, dispatching by shape.

    A single model or mapping renders as a vertical ``key: value`` detail view
    (a bare object under structured formats); a sequence renders as a collection
    (themed table for humans, array/NDJSON for pipes). Accepts pydantic models
    directly — no manual ``model_dump()`` — and writes the result itself, so
    there is no "forgot to ``echo``" silent-no-output trap. ``empty``,
    ``kind`` and ``table_columns`` behave as in :func:`render_rows`; ``empty``
    applies to a sequence only. Rows of a :class:`~untaped.records.Record`
    type that declares a kind carry it, so ``kind`` is only for plain models,
    mappings and kind-less records; one that names another kind than a row
    declares is an error. Without ``table_columns``, a collection of
    records shows their type's ``table_columns``; a table shows a field's
    :class:`~untaped.records.TableGlyph` instead of its value.
    """
    emit_with(
        records,
        ui=None,
        fmt=fmt,
        columns=columns,
        empty=empty,
        kind=kind,
        table_columns=table_columns,
    )


def emit_with(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    ui: UiContext | None,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
    table_columns: Sequence[str] | None = None,
) -> None:
    """:func:`emit` rendering a ``table`` through ``ui`` (``None``: the active theme).

    For root commands that must render even when the settings are broken.
    """
    _validate_kind(kind)
    single = isinstance(records, BaseModel | Mapping)
    items: Sequence[BaseModel | Mapping[str, object]] = (
        [records] if isinstance(records, BaseModel | Mapping) else records
    )
    kind = _rows_kind(items, kind)
    if table_columns is None and not single:
        table_columns = _record_table_columns(items)
    rendered = _render(
        [_as_row(item, table=fmt == "table") for item in items],
        single=single,
        fmt=fmt,
        columns=columns,
        empty=empty,
        kind=kind,
        table_columns=table_columns,
        schema=_model_schema(records),
        glyphs=_row_glyphs(items) if fmt == "table" else None,
        ui=ui,
    )
    if rendered:
        echo(rendered)


def _rows_kind(items: Sequence[BaseModel | Mapping[str, object]], kind: str | None) -> str | None:
    """The kind of an emit: the one its rows declare, else ``kind``.

    Raises ``ValueError`` (a programming error) when ``kind`` names another
    kind than a row declares, or rows declare different kinds, or only some
    rows declare one: each kind has exactly one schema.
    """
    declared = {kind_of(type(item)) if isinstance(item, BaseModel) else None for item in items}
    if kind is not None:
        if declared - {None, kind}:
            others = ", ".join(sorted(repr(each) for each in declared - {None, kind}))
            raise ValueError(f"emit(kind={kind!r}) on records of kind {others}")
        return kind
    if len(declared) > 1:
        shown = ", ".join(sorted("no kind" if each is None else repr(each) for each in declared))
        raise ValueError(f"emit() rows declare different kinds ({shown}); emit each kind apart")
    return declared.pop() if declared else None


def _model_schema(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
) -> list[str] | None:
    """Field names when every record is a pydantic model (a known schema)."""
    items = [records] if isinstance(records, BaseModel | Mapping) else list(records)
    if not items or not all(isinstance(item, BaseModel) for item in items):
        return None
    names: dict[str, None] = {}
    for item in items:
        model = type(item)
        assert issubclass(model, BaseModel)
        names.update(dict.fromkeys([*model.model_fields, *model.model_computed_fields]))
    return list(names)


def _record_table_columns(items: Sequence[BaseModel | Mapping[str, object]]) -> list[str] | None:
    """The default ``table`` columns every record's type declares (``None``: no shared ones)."""
    declared = {
        table_columns_of(type(item)) if isinstance(item, BaseModel) else () for item in items
    }
    return (list(declared.pop()) or None) if len(declared) == 1 else None


def _row_glyphs(
    items: Sequence[BaseModel | Mapping[str, object]],
) -> list[dict[str, TableGlyph]] | None:
    """Each record's :class:`TableGlyph` fields (``None``: no record has any)."""
    glyphs = [_glyph_fields(type(item)) if isinstance(item, BaseModel) else {} for item in items]
    return glyphs if any(glyphs) else None


@cache
def _glyph_fields(model: type[BaseModel]) -> dict[str, TableGlyph]:
    """The fields of ``model`` annotated with a :class:`TableGlyph` (also inside ``| None``)."""
    return {
        name: meta
        for name, field in model.model_fields.items()
        for meta in [
            *field.metadata,
            *(extra for arg in get_args(field.annotation) for extra in _metadata(arg)),
        ]
        if isinstance(meta, TableGlyph)
    }


def _metadata(annotation: object) -> tuple[object, ...]:
    """The ``Annotated`` metadata of ``annotation`` (none when not annotated)."""
    return tuple(getattr(annotation, "__metadata__", ()))


def _as_row(record: BaseModel | Mapping[str, object], *, table: bool) -> dict[str, object]:
    """Normalize a model or mapping into a plain row dict.

    Models dump in JSON mode so paths, enums, dates, etc. become plain
    JSON-compatible values every output format can encode; a ``table`` dump
    shows timestamps to the second.
    """
    if isinstance(record, BaseModel):
        return record.model_dump(mode="json", context=TABLE_CONTEXT if table else None)
    return dict(record)


def run_cyclopts_app(
    app: App,
    tokens: Iterable[str] | None,
    *,
    console: Console | None = None,
    error_console: Console | None = None,
    result_action: ResultAction | None = None,
) -> object:
    """Run a Cyclopts app while preserving untaped's usage-error contract.

    Ctrl-C anywhere exits ``130`` without a traceback. Also converts a broken
    downstream pipe — the consumer closed it early, e.g.
    ``untaped <plugin> list | head`` or a consumer that exits before reading all of
    its input — into a quiet ``SystemExit(0)`` (the standard CLI behaviour:
    the consumer chose to stop reading, which is not a failure). Without this the producer's
    buffered stdout flush fails at interpreter shutdown and Python prints a
    noisy ``Exception ignored while flushing sys.stdout: BrokenPipeError``.
    The invocation runs in its own :func:`~untaped.diagnostics.diagnostics_scope`;
    a parse error follows the ``--format`` its tokens ask for.
    """
    argv = list(tokens) if tokens is not None else sys.argv[1:]
    try:
        with diagnostics_scope():
            try:
                result = app(
                    argv,
                    console=console,
                    error_console=error_console,
                    exit_on_error=False,
                    print_error=False,
                    result_action=result_action,
                )
            except CycloptsError as exc:
                note_requested_format(argv)
                raise_usage(str(exc))
    except KeyboardInterrupt:
        _flush_stdout()
        raise SystemExit(ExitCode.INTERRUPTED) from None
    except BrokenPipeError:
        # Pipe broke mid-write (output large enough to flush before we got here).
        _exit_broken_pipe()
    except SystemExit as exc:
        if isinstance(exc.__context__, BrokenPipeError) and _stdout_is_devnull():
            # Rich's ``Console.on_broken_pipe`` (help output) points stdout at
            # /dev/null and exits 1 from inside its ``except BrokenPipeError``;
            # a closed pipe exits 0. Any other exit keeps its code.
            _exit_broken_pipe()
        # cyclopts exits (0 on success) rather than returning. Flush buffered
        # stdout now so a broken pipe surfaces here — catchable — instead of at
        # interpreter shutdown, where it can't be handled.
        _flush_stdout()
        raise
    _flush_stdout()
    return result


def _flush_stdout() -> None:
    """Flush stdout, converting a broken pipe into a clean exit."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _exit_broken_pipe()


def _stdout_is_devnull() -> bool:
    """Whether stdout's fd was redirected to ``/dev/null`` (Rich's broken-pipe hook)."""
    try:
        return os.path.samestat(os.fstat(sys.stdout.fileno()), os.stat(os.devnull))
    except AttributeError, OSError, ValueError:
        return False


def _exit_broken_pipe() -> NoReturn:
    """Silence the interpreter's final stdout flush, then exit 0 quietly.

    Redirecting the stdout fd to ``/dev/null`` stops Python re-raising the
    broken pipe when it flushes the standard streams on the way out. The guard
    covers streams with no real fd (a captured ``StringIO`` under tests)."""
    with suppress(OSError, ValueError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    raise SystemExit(0) from None


def existing_directory(type_: object, value: Path | None) -> None:
    """Cyclopts validator for an existing directory path."""
    if value is None:
        return
    if not value.exists():
        raise ValueError(f"path does not exist: {value}")
    if not value.is_dir():
        raise ValueError(f"path is not a directory: {value}")


def existing_file(type_: object, value: Path | None) -> None:
    """Cyclopts validator for an existing file path."""
    if value is None:
        return
    if not value.exists():
        raise ValueError(f"path does not exist: {value}")
    if not value.is_file():
        raise ValueError(f"path is not a file: {value}")


def parse_kv_pairs(values: Iterable[str] | None, *, flag: str) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` flag entries into a dict.

    Splits on the first ``=`` so values containing ``=`` survive intact.
    Malformed entries are rejected up front rather than passed through.
    """
    return dict(_split_pair(entry, flag=flag, shape="KEY=VALUE") for entry in values or ())


def _split_pair(entry: str, *, flag: str, shape: str) -> tuple[str, str]:
    key, sep, value = entry.partition("=")
    if not sep or not key.strip():
        raise_usage(f"{flag} expects {shape} (got {entry!r})")
    return key.strip(), value


def parse_json_pairs(values: Iterable[str] | None, *, flag: str) -> dict[str, Any]:
    """Parse repeated ``KEY=<json>`` flag entries into a dict.

    Sibling of :func:`parse_kv_pairs` for typed values: the part after the
    first ``=`` must be valid JSON (``labels=["a"]``, ``count=3``,
    ``name="x"``). Malformed entries are usage errors.
    """
    out: dict[str, Any] = {}
    for entry in values or ():
        key, raw = _split_pair(entry, flag=flag, shape="KEY=JSON")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise_usage(f"{flag} {key} contains invalid JSON: {exc}")
    return out


def resolve_each[R](ids: list[str], fn: Callable[[str], R]) -> tuple[list[R], bool]:
    """Resolve each identifier via ``fn``; aggregate per-id failures.

    Reports ``error: <id>: <exc>`` (:func:`report_error`) for any
    :class:`UntapedError` and
    returns ``(results, any_failed)`` so the caller decides exit code and
    aggregate rendering. Companion to :func:`read_identifiers` for stdin-fed
    list commands across domains.

    Only :class:`UntapedError` is caught: non-:class:`UntapedError` exceptions
    (including :class:`SystemExit` raised by interactive prompts) propagate
    immediately, aborting the loop. This is intentional — bugs and explicit
    user aborts must not be swallowed alongside per-id resolution failures.
    """
    results: list[R] = []
    any_failed = False
    for id_ in ids:
        try:
            results.append(fn(id_))
        except UntapedError as exc:
            report_error(exc, item=id_)
            any_failed = True
    return results, any_failed


def clamp_parallel(requested: int, *, cap: int, policy: str) -> int:
    """Cap ``--parallel`` at ``cap`` with a uniform stderr warning.

    A friendly clamp rather than a usage error, so ``-j $(nproc)`` keeps
    working; ``policy`` is the short rationale shown in parentheses
    (``"2 * os.cpu_count()"``). ``ParallelOption`` already enforces ``>= 1``.
    """
    if requested <= cap:
        return requested
    echo(f"warning: --parallel {requested} clamped to {cap} ({policy})", err=True)
    return cap


@contextmanager
def report_errors() -> Iterator[None]:
    """Convert :class:`UntapedError` into a clean stderr message + its exit code.

    Wrap every Cyclopts command body in this so users see ``error: ...``
    instead of a Python traceback. The exit code follows the error's
    ``category`` (``2`` usage, ``4`` environment, ``5`` unavailable, ``130``
    interrupted, ``1`` otherwise), raised to the most severe failure already
    reported in the run (:func:`~untaped.diagnostics.failure_exit_code`). A
    declined confirmation (:class:`~untaped.errors.OperationCancelledError`)
    prints its message without the ``error:`` prefix. Under JSON diagnostics
    the error is one JSON line (:func:`report_error`). Non-:class:`UntapedError`
    exceptions are left to Cyclopts' default handling — those represent bugs
    we want to see.
    """
    try:
        yield
    except OperationCancelledError as exc:
        report_declined(exc)
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
    except UntapedError as exc:
        report_error(exc)
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
