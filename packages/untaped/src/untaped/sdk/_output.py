"""The Output block: a command's rows, their formats, and what it prints instead of rows."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Self, cast

from pydantic import BaseModel

from untaped.sdk._block import FieldBlock, FieldOption, Invocation
from untaped.sdk._keys import SettingKey
from untaped.sdk._table import (
    Columns,
    _column_names_of,
    _type_argument,
    default_columns,
    following_fields,
    key_field,
    spreads,
)
from untaped.sdk.errors import (
    UsageError,
)

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.sdk._cli import OutputFormat
    from untaped.sdk.tree import TreeGraph


def _copy[B: FieldBlock](block: B) -> B:
    """A shallow copy of a field's default, which a filled field must never change."""
    return copy.copy(block)


OUTPUT_FORMATS: tuple[str, ...] = ("table", "json", "yaml", "pipe", "raw")
"""The formats every ``Output`` offers, before a plugin's own renderers."""


def _offered_formats(
    formats: Sequence[str] | None, render: Mapping[str, object]
) -> tuple[str, ...]:
    """The formats an ``Output`` offers: ``formats=``, else core's and the renderers'.

    Each one named is core's or has a renderer, and each renderer's format is
    named: a ``ValueError`` otherwise, raised when the plugin loads.
    """
    if formats is not None and not formats:
        raise ValueError("Output(formats=) names no format")
    if formats is None:
        return tuple(dict.fromkeys((*OUTPUT_FORMATS, *render)))
    offered = tuple(dict.fromkeys(formats))
    unknown = [name for name in offered if name not in OUTPUT_FORMATS and name not in render]
    if unknown:
        raise ValueError(
            f"Output(formats=) names {', '.join(unknown)}, which neither core nor render= prints"
        )
    unlisted = [name for name in render if name not in offered]
    if unlisted:
        raise ValueError(f"Output(render=) has {', '.join(unlisted)}, which formats= leaves out")
    return offered


class Output[R: BaseModel](FieldBlock):
    """What a command prints: ``output: Output[Salutation]`` adds ``--format`` and ``--columns``.

    ``R`` is the record ``run`` yields or returns (the return type must
    agree); its ``Column`` marks are a list's
    default columns (a single record's table shows every field), and
    ``--columns`` names its fields (checked before ``run``, so a subclass's
    own fields are not columns). The default configures it:
    ``Output(columns=(key(Issue).key, key(Issue).summary))`` gives this
    command its own default columns (a one-record table shows them even when empty),
    ``Output(render={"tree": render_tree})`` adds a format of the plugin's own
    (one named after a core format replaces it, though ``--columns`` still
    prints core's table): the renderer returns what to print and core prints
    it (:func:`print_rendered`), ``Output(format="raw")`` makes another
    format the default, and ``Output(formats=("tree", "json"))`` offers only
    those formats, each core's or a renderer's, the first the default (only
    a ``table`` default offering every core format follows ``UNTAPED_FORMAT``
    and ``untaped.ui.format``). ``Output(stream=True)`` prints each row ``run``
    yields as it comes (a log being followed): json one object per line, yaml
    one document per row, pipe and raw one line per row, a table one row per
    line under its header, and a plugin renderer once per row; a stream with
    no row prints nothing.
    """

    field_kind = "output"
    panel = "Output"
    options = (
        FieldOption("--format", "str", "The output format.", short="-f"),
        FieldOption(
            "--columns", "list[str]", "Table columns: a list, or +NAME to add.", short="-c"
        ),
    )

    def __init__(
        self,
        *,
        columns: Sequence[SettingKey | str] = (),
        render: Mapping[str, Callable[..., Rendered]] | None = None,
        format: str | None = None,
        formats: Sequence[str] | None = None,
        stream: bool = False,
    ) -> None:
        self._given = tuple(columns)
        self._declared = tuple(_column_names_of(columns, None))
        #: Each row prints as ``run`` yields it, not once the run ends.
        self.stream = stream
        self.render: Mapping[str, Callable[..., Rendered]] = dict(render or {})
        self._offered = _offered_formats(formats, self.render)
        format = format or self._offered[0]
        if format not in self.formats:
            raise ValueError(f"Output(format={format!r}) is not one of {', '.join(self.formats)}")
        #: The format a run without ``--format`` prints.
        self.default_format = format
        #: The format ``--format`` chose (the default when unfilled).
        self.format = format
        #: The table's default columns this run: the declared ones, or ``R``'s
        #: (:func:`default_columns`); ``.add``, ``.drop`` and ``.show`` change them.
        #: ``--columns`` applies on top.
        self.columns = Columns(self._declared)
        self._requested: list[str] | None = None
        self._empty: str | None = None
        self._model: type[BaseModel] | None = None

    @property
    def formats(self) -> tuple[str, ...]:
        """Every value ``--format`` takes for this command."""
        return self._offered

    @property
    def requested(self) -> list[str] | None:
        """The ``--columns`` this run asked for, as given; ``None`` without the flag."""
        return self._requested

    def empty(self, text: str) -> None:
        """The text a table shows when ``run`` printed no row."""
        self._empty = text

    def _check_declared(self, model: type[BaseModel]) -> None:
        """Refuse a declared column ``model`` has no field or key for (a ``TypeError``).

        Composition calls it when the plugin loads, so a bad column fails there.
        """
        _column_names_of(self._given, model)

    def _option_default(self, option: FieldOption) -> str | int | None:
        # ``table`` is every command's, so only another default is recorded
        if option.flag == "--format" and self.default_format != "table":
            return self.default_format
        return None

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Output[R]:
        """A copy for one invocation, from ``--format`` and ``--columns``.

        A ``--columns`` name ``R`` has no field for is a usage error here, before
        ``run`` starts; a plugin renderer's format takes no ``--columns``.
        """
        from untaped.sdk._cli import check_columns  # noqa: PLC0415 - rendering is heavy

        model = _type_argument(call.annotation)
        if model is not None:
            self._check_declared(model)
        requested = flags.get("--columns")
        filled = _copy(self)
        filled.format = str(flags.get("--format") or self.default_format)
        filled._requested = list(requested) if isinstance(requested, list) else None
        filled.columns = Columns(
            self._declared or (default_columns(model) if model else ()), model=model
        )
        filled._empty = None
        filled._model = model
        if filled.format in self.render and filled.format != "table":
            if filled._requested is not None:
                raise UsageError(f"--columns does not apply to --format {filled.format}")
        elif model is not None and not (filled.format == "table" and spreads(model)):
            # a ``Spread`` field's keys are columns the command names: checked once it is built
            fmt = cast("OutputFormat", filled.format)
            check_columns(model, columns=filled._requested, fmt=fmt)
        return filled

    def _check_named(self) -> None:
        """Refuse a ``--columns`` name a ``Spread`` table neither has nor was given, before ``run``.

        The command names its ``Spread`` keys as columns when it is built (a
        validator calling ``self.output.columns.add("grep:x")`` or ``.offer``),
        so they count as ``R``'s fields; a key no row then has is an empty cell.
        """
        from untaped.sdk._cli import _check_columns, _column_names, _model_fields  # noqa: PLC0415

        model = self._model
        requested = self._requested
        if model is None or self.format != "table" or not spreads(model):
            return
        if requested is None or requested == ["?"]:
            return
        schema = [*_model_fields(model), *(self._spread_keys() or ())]
        names = [name.lstrip("+-") for name in _column_names(requested)]
        _check_columns(names, [], fmt="table", schema=schema, strict=True)

    def _spread_keys(self) -> list[str] | None:
        """The ``Spread`` keys the command named: its columns and offers past ``R``'s fields."""
        if self._model is None or not spreads(self._model):
            return None
        from untaped.sdk._cli import _model_fields  # noqa: PLC0415 - rendering is heavy

        known = set(_model_fields(self._model))
        named = dict.fromkeys([*self.columns, *self.columns.offered])
        return [name for name in named if name not in known]

    def _print(self, records: BaseModel | list[BaseModel], *, kind: str | None) -> None:
        """Print ``records`` as this run asked: a plugin renderer's format, or core's."""
        from untaped.sdk._cli import (  # noqa: PLC0415 - rendering is heavy
            _as_row,
            _model_fields,
            _print_available_columns,
            count_failed_actions,
            count_row_failures,
            emit_with,
        )
        from untaped.sdk._ui import build_terminal  # noqa: PLC0415 - rendering is heavy

        count_failed_actions([records] if isinstance(records, BaseModel) else records)
        renderer = self.render.get(self.format)
        if self.format == "table" and self._requested is not None:
            renderer = None  # --columns asks for core's table, whatever replaced it
        if renderer is not None:
            count_row_failures([records] if isinstance(records, BaseModel) else records)
            print_rendered(renderer(records))
            return
        if self._requested == ["?"] and records == [] and self._model is not None:
            # no row to read the columns from: ``R`` names them
            _print_available_columns(_model_fields(self._model), defaults=list(self.columns))
            return
        # The run is over: a broken ``ui`` section draws the default theme rather
        # than losing what it printed.
        themed = self.format in ("table", "tree")
        ui = build_terminal(strict=False) if themed else None
        columns = self._requested
        detail = isinstance(records, BaseModel) and self.format == "table"
        if columns is None and self._declared and detail:
            columns = list(self.columns)  # a detail view would hide the empty ones
        following = following_fields(self._model) if self._model and self.format == "table" else {}
        rows = [records] if isinstance(records, BaseModel) else records
        table_columns = list(self.columns) or None
        if detail and not self._declared:
            table_columns = None  # a single record shows every field: R's marks are a list's
        if table_columns is None and following:
            # a field printed as its own table is not a column of the first
            shown = dict.fromkeys(name for row in rows for name in _as_row(row))
            table_columns = [name for name in shown if name not in following]
        emit_with(
            records,
            ui=ui,
            fmt=cast("OutputFormat", self.format),  # a plugin renderer's format returned above
            columns=columns,
            empty=self._empty,
            kind=kind,
            table_columns=table_columns,
            spread_keys=self._spread_keys(),
        )
        for name, item in following.items():
            words = name.replace("_", " ")
            for number, row in enumerate(rows, 1):
                value = getattr(row, name, None)
                if value is None:
                    continue  # not carried this run (``issues get`` without ``--comments``)
                # one heading per table, naming its row among several: two bare tables read as one
                of = f" of {_named(row, number)}" if len(rows) > 1 else ""
                print(f"\n{words}{of}")
                emit_with(
                    list(value),
                    ui=ui,
                    fmt="table",
                    empty=f"No {words}.",
                    table_columns=default_columns(item) or None,
                )


class Verbatim(str):
    """Text a renderer returns to print byte for byte, with no newline added.

    For content a script writes to a file, where a newline the content does not
    end with would change it: ``return Verbatim(document)``.
    """

    __slots__ = ()

    def __new__(cls, text: str) -> Self:
        return super().__new__(cls, text)


type Printable = str | Text | TreeGraph
"""One thing a renderer returns for core to print: a line or block, styled text, a tree."""


type Rendered = Printable | Sequence[Printable] | None
"""What an ``Output`` renderer returns: one printable, several in order, or nothing."""


type PreviewItem = str | Text | Sequence[BaseModel]
"""What a preview yields: a line, a styled line, or records printed as one table on stderr."""


def print_rendered(rendered: Rendered) -> None:
    """Print what a plugin renderer returned, in the run's theme.

    A string prints as it is, then a newline; a :class:`Verbatim` prints with
    nothing added. Styled text prints in colour where stdout supports it.
    Trees next to each other print as one block, a blank line between them,
    themed, a line too wide for the terminal ending in an ellipsis. ``None``
    prints nothing.
    """
    from itertools import groupby  # noqa: PLC0415

    from rich.text import Text  # noqa: PLC0415 - rendering is heavy

    from untaped.sdk._ui import build_terminal, print_styled  # noqa: PLC0415
    from untaped.sdk.tree import TreeGraph, print_tree  # noqa: PLC0415

    if rendered is None:
        return
    items = [rendered] if isinstance(rendered, str | Text | TreeGraph) else list(rendered)
    terminal = build_terminal(strict=False)  # the run is over: a broken ``ui`` draws the default
    for trees, run in groupby(items, key=lambda item: isinstance(item, TreeGraph)):
        if trees:
            print_tree(cast("list[TreeGraph]", list(run)), ui=terminal)
            continue
        for item in run:
            if isinstance(item, str):
                print(item, end="" if isinstance(item, Verbatim) else "\n", flush=True)
            else:
                print_styled(terminal, cast("Text", item))


def _named(row: BaseModel, number: int) -> str:
    """The row a following table belongs to: its ``Key`` value, else its place."""
    field = key_field(type(row))
    return str(getattr(row, field)) if field is not None else f"row {number}"
