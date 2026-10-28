"""CLI helpers shared by every Cyclopts command in the suite."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, get_args, overload

from cyclopts import App, Argument, ArgumentCollection, Parameter, ResultAction, Token
from cyclopts.exceptions import CycloptsError
from cyclopts.validators import Number
from pydantic import BaseModel
from rich.console import Console

from untaped.runtime.render import column_value, render_collection, render_document
from untaped.runtime.sink import (
    Diagnostics,
    current_sink,
    derived_exit_code,
    exit_on_counted_failure,
    failure_exit_code,
)

# re-exported: untaped.sdk.fields imports no runtime
from untaped.runtime.sink import (
    count_failed_actions as count_failed_actions,
)
from untaped.runtime.sink import (
    count_row_failures as count_row_failures,
)
from untaped.runtime.theme import (
    OutputFormat as OutputFormat,
)
from untaped.runtime.theme import TreeOutputFormat, resolve_theme_or_default
from untaped.sdk._run import Rendering, fixed_rendering, run_or_none
from untaped.sdk._theme import ThemeSpec
from untaped.sdk._ui import Ui, build_terminal, collection, detail
from untaped.sdk.diagnostic import Failure, dumped_names
from untaped.sdk.errors import (
    ExitCode,
    Failed,
    Matched,
    OperationCancelledError,
    UntapedError,
    UsageError,
)
from untaped.sdk.marks import (
    Stability,
    check_stability,
    mark_app,
)
from untaped.sdk.records import (
    OutcomeRecord,
    TableGlyph,
    check_data_kind,
    kind_of,
)
from untaped.sdk.tree import TreeGraph, print_tree


def _format_default(value: object) -> str:
    """Help text for a ``--format`` default; ``table`` defers to the overrides."""
    shown = getattr(value, "value", value)
    return "table, or UNTAPED_FORMAT / untaped.ui.format" if shown == "table" else str(shown)


FormatOption = Annotated[
    OutputFormat,
    Parameter(name=["--format", "-f"], help="Output format.", show_default=_format_default),
]
"""Shared ``--format / -f`` option for any command that prints rows."""

TreeFormatOption = Annotated[
    TreeOutputFormat,
    Parameter(name=["--format", "-f"], help="Output format.", show_default=_format_default),
]
"""``--format`` plus ``tree``, for a command that draws its rows as a tree (``emit(tree=)``)."""

#: Environment variable naming the default ``--format`` (wins over ``untaped.ui.format``).
FORMAT_ENV = "UNTAPED_FORMAT"


def apply_default_format(
    app: App, commands: tuple[str, ...], arguments: ArgumentCollection
) -> None:
    """Cyclopts config source: default an omitted shared ``--format``.

    Installed on the root app. Only a ``FormatOption`` (or another
    ``--format`` offering every shared format, such as ``TreeFormatOption``)
    whose command default is ``table`` follows the user's choice (a command
    defaulting to ``raw`` or ``yaml`` keeps it): ``UNTAPED_FORMAT`` first,
    then the ``untaped.ui.format`` setting (:func:`user_default_format`). An
    explicit ``--format`` has tokens already and always wins. A declared
    command under a run whose rendering the root fixed takes the default that
    rendering chose, read once, before anything was emitted. Otherwise (a
    command mounted through the 11.x seam) a ``--format`` the user chose (the
    flag, ``UNTAPED_FORMAT`` or ``untaped.ui.format``; not a command's own
    default) is recorded for the stderr diagnostics (:func:`_note_format`,
    phase 2), and, when they are text, the ``untaped.ui.theme`` messages
    render with (:func:`_note_theme`; a broken ``ui`` section degrades to the
    default theme).
    """
    argument = _table_default_format(arguments)
    rendering = fixed_rendering()
    if rendering is not None and _declared(app):
        _default_to(argument, _rendered_default(rendering))
        return
    _default_to(argument, None if argument is None else user_default_format(repairs=_repairs(app)))
    given = [
        str(argument.tokens[-1].value)
        for argument in arguments
        if "--format" in argument.names and argument.tokens
    ]
    _note_format(given[-1] if given else None)
    # Called for the root (no command) and again for the command parsed.
    run = run_or_none()
    text = run is not None and run.policy.diagnostics == "text"
    _note_theme(user_theme() if commands and text else None)


def _note_format(fmt: str | None) -> None:
    """Record the ``--format`` an 11.x seam command's invocation chose on its run's policy.

    Phase 1 records what argv asks for before parsing
    (:func:`note_requested_format`); phase 2 what the parsed command chose
    (:func:`apply_default_format`). Outside a run there is no invocation to
    record it for, and under a run whose rendering is fixed there is
    nothing left to choose: nothing happens. Owed: goes with the seam.
    """
    run = run_or_none()
    if run is not None and run.rendering is None:
        run.set_policy(replace(run.policy, format=fmt))


def _note_theme(theme: ThemeSpec | None) -> None:
    """Record the theme an 11.x seam command's text messages render with on its run's policy.

    Set once settings load (phase 2, beside :func:`_note_format`; ``None``
    under JSON diagnostics, which render no theme). Outside a run, or under
    a run whose rendering is fixed, nothing happens. Owed: goes with the seam.
    """
    run = run_or_none()
    if run is not None and run.rendering is None:
        run.set_policy(replace(run.policy, theme=theme))


def _rendered_default(rendering: Rendering) -> tuple[str, str] | None:
    """The user's default format the run's rendering chose, and its source; ``None`` for none."""
    fmt, source = rendering.format, rendering.source
    if fmt is None or source is None or source == "--format":
        return None
    return fmt, source


def _default_to(argument: Argument | None, chosen: tuple[str, str] | None) -> None:
    """Give the omitted ``argument`` the ``chosen`` default format, from its source."""
    if argument is not None and chosen is not None:
        value, source = chosen
        argument.append(Token(keyword=source, value=value, source=source))


def user_theme() -> ThemeSpec:
    """The user's ``untaped.ui.theme`` for stderr messages; the default on a broken ``ui``."""
    # Keep settings lazy, as ``user_default_format`` does.
    from untaped.runtime.settings import load_core_part  # noqa: PLC0415

    return resolve_theme_or_default(lambda: load_core_part("ui"), strict=False)


def _declared(app: App) -> bool:
    """Whether the command parsed (``app``) is one the mount made from a declaration."""
    # Lazy: the mount imports this module.
    from untaped.runtime.mount import is_declared  # noqa: PLC0415

    return is_declared(app.default_command)


def _repairs(app: App) -> bool:
    """Whether the command parsed (``app``) is marked ``repairs``: it repairs the setup."""
    # Lazy: the mount imports this module.
    from untaped.runtime.mount import repairs_at  # noqa: PLC0415

    return repairs_at(app) is True


def _table_default_format(arguments: ArgumentCollection) -> Argument | None:
    """The omitted table-default ``--format`` among ``arguments``, if any.

    That is a ``--format`` whose choices include every shared format
    (``FormatOption``, ``TreeFormatOption``); the user's default is still
    one of the shared formats.
    """
    return next(
        (
            argument
            for argument in arguments
            if "--format" in argument.names
            and not argument.tokens
            and follows_user_format(get_args(argument.hint), argument.field_info.default)
        ),
        None,
    )


def follows_user_format(choices: Iterable[object], default: object) -> bool:
    """Whether a ``--format`` offering ``choices`` and defaulting to ``default`` follows the user's.

    It does when it offers every shared format and defaults to ``table``: only
    then do ``UNTAPED_FORMAT`` and ``untaped.ui.format`` set it. The parse
    asks it of the cyclopts argument, the root of the declaration
    (``untaped.runtime.root_options``), so the two never disagree.
    """
    return set(get_args(OutputFormat)) <= set(choices) and default == "table"


def user_default_format(*, repairs: bool) -> tuple[str, str] | None:
    """The user's default for a table-default ``--format``, and where it came from.

    ``UNTAPED_FORMAT``, else the ``untaped.ui.format`` setting; ``None`` when
    neither is set. A ``ui`` section that fails to load is ignored here so
    ``config set`` can still repair it; ``doctor`` reports it. An invalid
    ``UNTAPED_FORMAT`` is a usage error, except under a command marked
    ``repairs`` (``doctor``, ``setup``, ``skills``), which ignores it.
    """
    choices = get_args(OutputFormat)
    value = os.environ.get(FORMAT_ENV) or None
    if value is not None:
        if value in choices:
            return value, FORMAT_ENV
        if not repairs:
            raise_usage(f"{FORMAT_ENV} must be one of {', '.join(choices)}; got {value!r}")
    # Keep settings lazy: only commands printing rows need them here.
    from untaped.runtime.settings import load_core_part  # noqa: PLC0415

    try:
        setting = load_core_part("ui").format
    except UntapedError:
        return None
    return None if setting is None else (setting, "untaped.ui.format")


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
"""The 11.x ``--stdin`` flag; a 12.0 command declares a ``Stdin[R]`` field instead."""

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
    must be declared. ``untaped.sdk.testing.check_plugin`` enforces all three.
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


def echo(message: object = "", *, nl: bool = True) -> None:
    """Print a CLI message to stdout; stderr goes through ``ctx.diag``.

    Each message is flushed, so lines a command prints as it goes stream
    through a pipe.
    """
    print(message, end="\n" if nl else "", flush=True)


def requested_format(tokens: Sequence[str]) -> str | None:
    """The value of the last ``--format``/``-f`` before ``--`` in raw ``tokens``; ``None``."""
    value = None
    for index, token in enumerate(tokens):
        if token == "--":
            break
        name, separator, inline = token.partition("=")
        if name in ("--format", "-f"):
            if separator:
                value = inline
            elif index + 1 < len(tokens):
                value = tokens[index + 1]
    return value


def note_requested_format(tokens: Sequence[str]) -> None:
    """Record the ``--format`` raw ``tokens`` ask for, for an error found before parsing.

    The last ``--format``/``-f`` before ``--`` (:func:`requested_format`)
    wins over ``UNTAPED_FORMAT``; only a structured value switches the error
    to a JSON line.
    """
    _note_format(requested_format(tokens) or os.environ.get(FORMAT_ENV) or None)


def report_declined(exc: OperationCancelledError) -> None:
    """Print a declined confirmation: its bare message (a JSON error line under JSON).

    It counts toward the exit code like any failure.
    """
    # A declined confirmation is the user's choice, not an error: no prefix.
    current_sink().emit(Failure.from_exception(exc), rendered=str(exc))


def raise_usage(message: str) -> NoReturn:
    """Print ``error: <message>`` and exit ``2`` (usage) immediately.

    For code outside core's error boundary (cyclopts validators, option
    parsing). Inside a command prefer ``raise UsageError(message)``, which
    core prints and exits on the same way.
    """
    Diagnostics().failure(UsageError(message))
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


def stderr_table(records: Sequence[BaseModel], *, colorize: bool) -> str:
    """``records`` as the table core prints on stdout, for stderr: their ``Column`` fields.

    The run's theme lays it out (empty columns hidden, the list view); a broken
    ``ui`` section draws the default.
    """
    from untaped.sdk.fields import default_columns  # noqa: PLC0415 - fields imports this module

    theme = build_terminal(strict=False).theme
    rows = [_as_row(record) for record in records]
    defaults = list(default_columns(type(records[0]))) or None
    shown, selection = _table_layout(rows, defaults, frozenset(), theme=theme)
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
    drawn by ``fmt="tree"`` (a command offering it takes
    ``TreeFormatOption``) and ignored by every other format, so a command
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
        from untaped.sdk.fields import table_rows  # noqa: PLC0415 - fields import this module

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


def _refuse_tree_columns(columns: list[str] | None) -> None:
    if columns is not None:
        raise_usage("--columns does not apply to --format tree")


def emit_document(data: Mapping[str, object], *, fmt: Literal["json", "yaml"]) -> None:
    """Print one document to stdout: a JSON Schema, a profile's settings, a config file.

    A document is not a row: it has no kind, no ``pipe`` envelope and no
    columns, so it goes through here, never :func:`emit`.
    """
    echo(render_document(data, fmt=fmt))


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


def _reports_change(items: Sequence[BaseModel | Mapping[str, object]]) -> bool:
    """Whether a row is an :class:`OutcomeRecord`: what a mutation did, after it ran."""
    return any(isinstance(item, OutcomeRecord) for item in items)


def _model_schema(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
) -> list[str] | None:
    """Field names when every record is a pydantic model (a known schema).

    A model with ``extra="allow"`` passes a service's answer through, so its
    schema is open, as a mapping row's is (``None``).
    """
    items = [records] if isinstance(records, BaseModel | Mapping) else list(records)
    if not items or not all(
        isinstance(item, BaseModel) and item.model_config.get("extra") != "allow" for item in items
    ):
        return None
    names: dict[str, None] = {}
    for item in items:
        model = type(item)
        assert issubclass(model, BaseModel)
        names.update(dict.fromkeys(_model_fields(model)))
    return list(names)


def _model_fields(model: type[BaseModel]) -> list[str]:
    """The columns ``model`` rows have: their dumped field names, then computed fields."""
    return [*dumped_names(model).values(), *model.model_computed_fields]


def _record_table_columns(items: Sequence[BaseModel | Mapping[str, object]]) -> list[str] | None:
    """The default ``table`` columns every record's type declares (``None``: no shared ones)."""
    from untaped.sdk.fields import default_columns  # noqa: PLC0415 - fields import this module

    declared = {
        default_columns(type(item)) if isinstance(item, BaseModel) else () for item in items
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


def _as_row(record: BaseModel | Mapping[str, object]) -> dict[str, object]:
    """Normalize a model or mapping into a plain row dict.

    Models dump in JSON mode so paths, enums, dates, etc. become plain
    JSON-compatible values every output format can encode.
    """
    if isinstance(record, BaseModel):
        return record.model_dump(mode="json")
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
    The invocation writes to the current run's output (the root starts one
    first; the testing kit runs each invocation under its own); a parse error
    follows the ``--format`` its tokens ask for. A command that emitted a
    failure and returned normally exits with the worst code it counted.
    While it runs, :func:`given_tokens` hands ``app``'s command the tokens
    as given.
    """
    argv = list(tokens) if tokens is not None else sys.argv[1:]
    running = _RUNNING.set((app, tuple(argv)))
    try:
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
        except SystemExit as exc:
            exc.code = derived_exit_code(exc.code)
            raise
        exit_on_counted_failure()
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
    finally:
        _RUNNING.reset(running)
    _flush_stdout()
    return result


#: The app :func:`run_cyclopts_app` runs, and the tokens it was given.
_RUNNING: ContextVar[tuple[App, tuple[str, ...]] | None] = ContextVar(
    "untaped_running_app", default=None
)


@contextmanager
def given_tokens(app: App) -> Iterator[list[str] | None]:
    """The tokens :func:`run_cyclopts_app` was given to run ``app`` on; ``None`` otherwise.

    Cyclopts' parse rewrites a short cluster before a command sees its tokens
    (``-n5`` becomes ``-n -5``), so a command that reads the line itself
    reads these. A call nested in the block sees none, unless run anew.
    """
    running = _RUNNING.get()
    marker = _RUNNING.set(None)
    try:
        yield list(running[1]) if running is not None and running[0] is app else None
    finally:
        _RUNNING.reset(marker)


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

    Reports ``error: <id>: <exc>`` (``Diagnostics.failure``) for any
    :class:`UntapedError` and
    returns ``(results, any_failed)`` so the caller decides exit code and
    aggregate rendering, for commands acting on many identifiers.

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
            Diagnostics().failure(exc, item=id_)
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
    Diagnostics().warning(f"--parallel {requested} clamped to {cap} ({policy})")
    return cap


@contextmanager
def report_errors() -> Iterator[None]:
    """Convert :class:`UntapedError` into a clean stderr message + its exit code.

    Core's error boundary: the mount and the root dispatch run commands in
    it, so users see ``error: ...`` instead of a Python traceback. The exit code follows the error's
    ``category`` (``2`` usage, ``4`` environment, ``5`` unavailable, ``130``
    interrupted, ``1`` otherwise), raised to the most severe failure already
    reported in the run (:func:`~untaped.runtime.sink.failure_exit_code`). A
    declined confirmation (:class:`~untaped.sdk.errors.OperationCancelledError`)
    prints its message without the ``error:`` prefix. :class:`~untaped.sdk.errors.Failed`
    and :class:`~untaped.sdk.errors.Matched` print nothing, since the rows
    said it: ``1`` and ``3``, a counted failure winning over either. Under JSON
    diagnostics the error is one JSON line (``Diagnostics.failure``).
    Non-:class:`UntapedError` exceptions are left to Cyclopts' default
    handling — those represent bugs we want to see.
    """
    try:
        yield
    except Failed as exc:
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
    except Matched as exc:
        raise SystemExit(derived_exit_code(exc.exit_code)) from exc
    except OperationCancelledError as exc:
        report_declined(exc)
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
    except UntapedError as exc:
        Diagnostics().failure(exc)
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
