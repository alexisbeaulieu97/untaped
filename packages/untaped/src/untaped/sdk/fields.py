"""What a command's fields can be: positionals and marks, core's value types, core-filled blocks.

A command is a pydantic model (:class:`untaped.sdk.plugin.Command`); this
module is the vocabulary its fields use beyond plain Python types:

- ``Positional[T]`` makes an argument; ``AllowHyphen`` and ``Short("-x")``
  shape one; ``Column``, ``Key``, ``Following`` and ``Written`` mark a
  record's or a file option's fields for core.
- ``File[M]``, ``ExistingFile``, ``TextOrStdin``, ``PathOrStdin``,
  ``KeyValue`` and ``KeyJson`` are value types core parses.
- ``Output[R]``, ``Stdin[R]``, ``Lines``, ``Confirm``, ``DryRun``, ``Refresh``,
  ``Parallel`` and ``Limit`` are fields core fills: each adds its fixed flags,
  configured by the field's default value.
- ``key(Model).field`` is a typed reference to one settings field.

The declaration walk (``untaped.runtime.composition``) reads all of it; a
command never parses a token itself.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from datetime import datetime
from enum import Enum
from functools import cache
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel, BeforeValidator, JsonValue, ValidationError
from pydantic.types import PathType
from pydantic_core import core_schema

from untaped.sdk._block import OR_STDIN, POSITIONAL, FieldBlock, FieldOption, Invocation, Mark
from untaped.sdk.errors import (
    ConfigError,
    ErrorCategory,
    NeedsYesError,
    OperationCancelledError,
    UntapedError,
    UsageError,
    first_validation_error,
)

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
    from pydantic.fields import FieldInfo
    from pydantic.json_schema import JsonSchemaValue
    from rich.text import Text

    from untaped.sdk._cli import OutputFormat
    from untaped.sdk._pipe import PipeEnvelope

# --- positionals and marks ------------------------------------------------------

if TYPE_CHECKING:
    type Positional[T, *Marks] = T
    """An argument rather than an option: ``names: Positional[list[str]]``."""
else:

    class Positional:
        """An argument rather than an option: ``names: Positional[list[str]]``.

        Arguments come in declaration order, a bundle's flattened in place.
        Further marks follow the type: ``Positional[str, AllowHyphen]``.
        """

        def __class_getitem__(cls, item: object) -> object:
            items = item if isinstance(item, tuple) else (item,)
            return Annotated[(items[0], POSITIONAL, *items[1:])]


AllowHyphen = Mark("AllowHyphen")
"""An argument that may start with ``-`` (``HEAD~1``, ``-1``): ``Positional[str, AllowHyphen]``."""

Column = Mark("Column")
"""A record field a list's table shows by default: ``name: Annotated[str, Column]``.

None marked: every field. A single record's table shows every field either way.
"""

Key = Mark("Key")
"""The record field a bare identifier stands for, on stdin and in state rows."""

Following = Mark("Following")
"""A list-of-models field a table prints as a second table after the first."""

Bytes = Mark("Bytes")
"""An ``int`` count of bytes a table shows in words (``1.5 KiB``): ``Annotated[int, Bytes]``.

Only the table changes: json, yaml, raw and pipe keep the number.
"""

Age = Mark("Age")
"""A ``datetime`` a table shows as how long ago it was (``3 h ago``), by the run's clock.

Only the table changes: json, yaml, raw and pipe keep the timestamp.
"""

Duration = Mark("Duration")
"""An ``int`` or ``float`` of seconds a table shows as a run time (``1m42s``).

Only the table changes: json, yaml, raw and pipe keep the number.
"""

ObjectName = Mark("ObjectName")
"""A ``str`` git object name a table shortens to 10 characters when it is a full 40-hex one.

Only the table changes: json, yaml, raw and pipe keep the whole name.
"""


class Status:
    """The roles a table draws a field's words in: ``Status(success=("cloned",))``.

    The roles are the theme's (``success``, ``warning``, ``error``), so a user
    theme recolours them; a value no role names stays plain. A subclass that
    redeclares the field adds to its base's roles, the nearest class winning for
    a value. Only the table changes: json, yaml, raw and pipe keep the word.
    """

    __slots__ = ("roles",)

    def __init__(
        self,
        *,
        success: Iterable[object] = (),
        warning: Iterable[object] = (),
        error: Iterable[object] = (),
    ) -> None:
        given = (("success", success), ("warning", warning), ("error", error))
        for role, values in given:
            if isinstance(values, str):
                raise TypeError(f"Status({role}=...) takes a tuple of words, as ({values!r},)")
        self.roles: dict[str, str] = {
            _word(value): role for role, values in given for value in values
        }

    def __repr__(self) -> str:
        return f"Status({self.roles!r})"


def _word(value: object) -> str:
    """A status value as the word a row holds: a ``str``, or a member of an enum of words.

    Anything else (an enum of numbers) is refused: its cell holds no word to draw.
    """
    word = _plain(value)
    if not isinstance(word, str):
        raise TypeError(f"Status takes words (str values), not {value!r}")
    return word


def _plain(value: object) -> object:
    """``value``, or an enum member's value: what a row holds."""
    return value.value if isinstance(value, Enum) else value


class Roled(str):
    """A table cell's text and the theme role its field's ``Status`` gave its word."""

    __slots__ = ("role",)
    role: str

    def __new__(cls, text: str, role: str) -> Roled:
        cell = super().__new__(cls, text)
        cell.role = role
        return cell


Spread = Mark("Spread")
"""A ``dict[str, scalar]`` field a table shows as one column per key, named by the key.

``--columns KEY`` picks one in a table; where the field is a default column its
keys are. The command names its keys as columns once it is built (a model
validator calling ``self.output.columns.add(...)``, or ``.offer(...)`` for a key
hidden by default), so ``--columns`` is checked before ``run``; a named key a
row lacks is an empty cell, and a key it did not name shows by default but is
not a column ``--columns`` picks. Only the table changes: json, yaml, raw and
pipe keep the mapping.
"""

Written = Mark("Written")
"""A ``File[M]`` option the command writes rather than reads: the model's "Starter" line."""


class Document:
    """A value naming a document of ``M`` that the plugin finds and reads itself.

    ``recipe: Annotated[Positional[str], Document(RecipeFile)]`` takes a
    library name or a path, as the plugin resolves them; core checks nothing
    and reads nothing (a path read as it is given is ``File[M]``'s), it only
    learns the model, so ``M`` is one of the command's inputs, documented as a
    ``File[M]`` of it is.
    """

    __slots__ = ("model",)

    def __init__(self, model: type[BaseModel]) -> None:
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError(f"Document takes a pydantic model class, not {model!r}")
        self.model = model

    def __repr__(self) -> str:
        return f"Document({self.model.__name__})"


class Short:
    """A one-letter flag for an option: ``repo: Annotated[str, Short("-r")]``.

    The letters core's own blocks use (``-f``, ``-c``, ``-y``, ``-j``, ``-h``)
    are a composition error.
    """

    __slots__ = ("flag",)

    def __init__(self, flag: str) -> None:
        if len(flag) != 2 or flag[0] != "-" or not flag[1].isalpha():
            raise ValueError(f"Short takes one letter after a dash, as Short('-r'), not {flag!r}")
        self.flag = flag

    def __repr__(self) -> str:
        return f"Short({self.flag!r})"


# --- value types --------------------------------------------------------------


class File[M: BaseModel]:
    """A path to a document of ``M``, YAML or JSON by its suffix: ``list[File[Suite]]``.

    A path the command reads must exist when core fills the command (exit 2
    otherwise); a directory passes, for a plugin that searches one. ``.path`` is the path,
    for a plugin that parses itself; ``.load()`` reads
    and validates the document; ``.save(model)`` writes one and refuses to
    replace an existing file unless ``replace=True``. The model's docstring is
    the input's page; ``Annotated[File[M], Written]`` is a file the command
    writes, which need not exist yet.
    """

    __slots__ = ("__orig_class__", "_model", "path")

    def __init__(self, path: str | Path, *, model: type[M] | None = None) -> None:
        self.path = Path(path)
        self._model = model

    def __repr__(self) -> str:
        return f"File({str(self.path)!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, File) and (self.path, self.model) == (other.path, other.model)

    def __hash__(self) -> int:
        return hash(self.path)

    @property
    def model(self) -> type[M]:
        """The model the document is read as."""
        if self._model is not None:
            return self._model
        alias = getattr(self, "__orig_class__", None)
        if alias is None:
            raise TypeError(f"{self!r} has no model: write File[Model](path)")
        return cast("type[M]", get_args(alias)[0])

    def load(self) -> M:
        """The document, validated as ``M``; a ``ConfigError`` naming the file otherwise."""
        from untaped.sdk.storage._fs import read_structured_file  # noqa: PLC0415 - a group

        raw = read_structured_file(self.path)
        try:
            return self.model.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(
                f"{self.path}: {first_validation_error(exc)}", category="invalid", system="local"
            ) from exc

    def save(self, model: M, *, replace: bool = False) -> None:
        """Write ``model`` as YAML or JSON by the suffix; replace a file only if ``replace``."""
        import yaml  # noqa: PLC0415 - only a command that writes pays for it

        from untaped.sdk.storage._fs import atomic_write  # noqa: PLC0415 - storage is a group

        if self.path.exists() and not replace:
            raise UntapedError(
                f"{self.path} already exists; remove it or choose another path",
                category=ErrorCategory.FAILED,
                system="local",
            )
        data = model.model_dump(mode="json", exclude_none=True)
        if self.path.suffix.lower() == ".json":
            text = json.dumps(data, indent=2) + "\n"
        else:
            text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        atomic_write(self.path, text)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        args = get_args(source)
        model = args[0] if args else None

        def build(value: object) -> File[Any]:
            if isinstance(value, File):
                return value
            if isinstance(value, str | Path):
                return File(value, model=model)
            raise ValueError("expected a path")

        return core_schema.no_info_plain_validator_function(
            build,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: str(value.path)
            ),
            json_schema_input_schema=core_schema.str_schema(),
        )


ExistingFile = Annotated[Path, PathType("file")]
"""A path that must name an existing file (exit 2 otherwise)."""

TextOrStdin = Annotated[str, OR_STDIN]
"""A value, or ``-`` to read it from stdin."""

PathOrStdin = Annotated[Path, OR_STDIN]
"""A path, or ``-`` to read the content from stdin."""


def _pair(value: object, *, shape: str) -> tuple[str, str]:
    if isinstance(value, tuple):
        return cast("tuple[str, str]", value)
    if not isinstance(value, str):
        raise ValueError(f"expected {shape}")
    key, sep, rest = value.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"expected {shape}, got {value!r}")
    return key, rest


def _key_value(value: object) -> tuple[str, str]:
    return _pair(value, shape="KEY=VALUE")


def _key_json(value: object) -> tuple[str, JsonValue]:
    if isinstance(value, tuple):
        return cast("tuple[str, JsonValue]", value)
    key, raw = _pair(value, shape="KEY=JSON")
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the value of {key!r} is not JSON: {exc.msg}") from exc


KeyValue = Annotated[tuple[str, str], BeforeValidator(_key_value)]
"""``KEY=VALUE``, split on the first ``=``: ``labels: list[KeyValue]`` repeats."""

KeyJson = Annotated[tuple[str, JsonValue], BeforeValidator(_key_json)]
"""``KEY=<json>``: the part after the first ``=`` is JSON (``count=3``, ``tags=["a"]``)."""


# --- typed references to settings ------------------------------------------------


class SettingKey:
    """A typed reference to one field of a settings model: ``key(HelloSettings).greeting``.

    Created by :func:`key`; each attribute step is checked against the
    model's fields, so a typo fails where it is written, and a nested model
    is followed (``key(AcmeSettings).server.url``). The key knows its model
    and its path; core spells the full dotted key (with the plugin's section)
    from the composition. ``str()`` is the path inside the section.
    """

    __slots__ = ("_model", "_parts")

    def __init__(self, model: type[BaseModel], parts: tuple[str, ...] = ()) -> None:
        self._model = model
        self._parts = parts

    def __getattr__(self, name: str) -> SettingKey:
        if name.startswith("_"):
            raise AttributeError(name)
        current = self._field_model()
        if name not in current.model_fields:
            raise AttributeError(
                f"{current.__name__} has no field {name!r} "
                f"(fields: {', '.join(current.model_fields) or 'none'})"
            )
        return SettingKey(self._model, (*self._parts, name))

    def _field_model(self) -> type[BaseModel]:
        """The model the next attribute step reads: the settings model, or the nested one."""
        current: type[BaseModel] = self._model
        for part in self._parts:
            annotation = current.model_fields[part].annotation
            nested = _model_in(annotation)
            if nested is None:
                raise AttributeError(
                    f"{'.'.join(self._parts)} of {self._model.__name__} is a value, not a model"
                )
            current = nested
        return current

    @staticmethod
    def model_of(setting: SettingKey) -> type[BaseModel]:
        """The settings model ``setting`` starts from (what composition maps to a section).

        A static method, read on the class, so no settings field can shadow it.
        """
        return setting._model

    @staticmethod
    def path_of(setting: SettingKey) -> tuple[str, ...]:
        """The field names from the settings model down to ``setting``."""
        return setting._parts

    def __str__(self) -> str:
        return ".".join(self._parts)

    def __repr__(self) -> str:
        return (
            f"key({self._model.__name__}).{self!s}"
            if self._parts
            else f"key({self._model.__name__})"
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, SettingKey)
            and self._model is other._model
            and self._parts == other._parts
        )

    def __hash__(self) -> int:
        return hash((self._model, self._parts))

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.is_instance_schema(cls, serialization=core_schema.to_string_ser_schema())

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """A key serialises as its dotted path."""
        del schema, handler
        return {"type": "string"}


def _model_in(annotation: object) -> type[BaseModel] | None:
    """The one pydantic model ``annotation`` is (``Model`` or ``Model | None``), if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    models = [
        arg for arg in get_args(annotation) if isinstance(arg, type) and issubclass(arg, BaseModel)
    ]
    return models[0] if len(models) == 1 else None


def key(model: type[BaseModel]) -> SettingKey:
    """A typed reference to a settings field: ``key(HelloSettings).greeting``."""
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"key() takes a settings model class, not {model!r}")
    return SettingKey(model)


# --- the fields core fills ---------------------------------------------------------


def _copy[B: FieldBlock](block: B) -> B:
    """A shallow copy of a field's default, which a filled field must never change."""
    return copy.copy(block)


OUTPUT_FORMATS: tuple[str, ...] = ("table", "json", "yaml", "pipe", "raw")
"""The formats every ``Output`` offers, before a plugin's own renderers."""


class Output[R: BaseModel](FieldBlock):
    """What a command prints: ``output: Output[Salutation]`` adds ``--format`` and ``--columns``.

    ``R`` is the record ``run`` yields or returns (the return type must
    agree); its ``Column`` marks, else its ``table_columns``, are a list's
    default columns (a single record's table shows every field), and
    ``--columns`` names its fields (checked before ``run``, so a subclass's
    own fields are not columns). The default configures it:
    ``Output(columns=("key", "summary"))`` gives this command its own default
    columns (a one-record table shows them even when empty),
    ``Output(render={"tree": render_tree})`` adds a format of the plugin's own
    (one named after a core format replaces it, though ``--columns`` still
    prints core's table), and ``Output(format="raw")`` makes another
    format the default (only a ``table`` default follows ``UNTAPED_FORMAT`` and
    ``untaped.ui.format``).
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
        columns: Sequence[str] = (),
        render: Mapping[str, Callable[..., Any]] | None = None,
        format: str = "table",
    ) -> None:
        self._declared = tuple(columns)
        self.render: Mapping[str, Callable[..., Any]] = dict(render or {})
        if format not in self.formats:
            raise ValueError(f"Output(format={format!r}) is not one of {', '.join(self.formats)}")
        #: The format a run without ``--format`` prints.
        self.default_format = format
        #: The format ``--format`` chose (the default when unfilled).
        self.format = format
        #: The table's default columns this run: the declared ones, or ``R``'s
        #: (:func:`default_columns`); ``.add(...)`` adds more. ``--columns`` applies on top.
        self.columns = Columns(self._declared)
        self._requested: list[str] | None = None
        self._empty: str | None = None
        self._model: type[BaseModel] | None = None

    @property
    def formats(self) -> tuple[str, ...]:
        """Every value ``--format`` takes for this command."""
        return tuple(dict.fromkeys((*OUTPUT_FORMATS, *self.render)))

    @property
    def requested(self) -> list[str] | None:
        """The ``--columns`` this run asked for, as given; ``None`` without the flag."""
        return self._requested

    def empty(self, text: str) -> None:
        """The text a table shows when ``run`` printed no row."""
        self._empty = text

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
        requested = flags.get("--columns")
        filled = _copy(self)
        filled.format = str(flags.get("--format") or self.default_format)
        filled._requested = list(requested) if isinstance(requested, list) else None
        filled.columns = Columns(self._declared or (default_columns(model) if model else ()))
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
        from untaped.sdk._ui import ui_context  # noqa: PLC0415 - rendering is heavy

        count_failed_actions([records] if isinstance(records, BaseModel) else records)
        renderer = self.render.get(self.format)
        if self.format == "table" and self._requested is not None:
            renderer = None  # --columns asks for core's table, whatever replaced it
        if renderer is not None:
            count_row_failures([records] if isinstance(records, BaseModel) else records)
            renderer(records)
            return
        if self._requested == ["?"] and records == [] and self._model is not None:
            # no row to read the columns from: ``R`` names them
            _print_available_columns(_model_fields(self._model), defaults=list(self.columns))
            return
        # The run is over: a broken ``ui`` section draws the default theme rather
        # than losing what it printed.
        themed = self.format in ("table", "tree")
        ui = ui_context(strict=False) if themed else None
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


def _named(row: BaseModel, number: int) -> str:
    """The row a following table belongs to: its ``Key`` value, else its place."""
    field = key_field(type(row))
    return str(getattr(row, field)) if field is not None else f"row {number}"


class Columns(list[str]):
    """A command's default table columns for one run: ``self.output.columns.add("url")``."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        super().__init__(names)
        #: ``Spread`` keys ``--columns`` may pick that the table hides by default.
        self.offered: list[str] = []

    def add(self, *names: str) -> None:
        """Show ``names`` too, after the columns already shown."""
        self.extend(name for name in names if name not in self)

    def offer(self, *names: str) -> None:
        """Let ``--columns`` pick the ``Spread`` keys ``names`` without showing them by default."""
        self.offered.extend(name for name in names if name not in self.offered)


def table_rows(
    records: Sequence[BaseModel | Mapping[str, object]],
    rows: list[dict[str, object]],
    columns: Sequence[str] | None,
) -> tuple[list[dict[str, object]], list[str] | None, list[str]]:
    """``rows`` as a table shows them, its default ``columns``, and the keys ``Spread`` added.

    A ``Bytes`` field's cell is its size in words, an ``Age`` field's how long
    ago it was by :func:`untaped.sdk.now`'s clock, a ``Duration`` field's its
    run time, an ``ObjectName`` field's its short name, a :class:`TableText` model's
    its ``table_text()``, and a ``Spread`` field gives
    way to a column per key (a key a row already has keeps the row's value). A
    word its field's ``Status`` names is a :class:`Roled` cell.
    """
    from untaped.sdk._run import run_clock  # noqa: PLC0415 - no cycle at import
    from untaped.sdk.diagnostic import dumped_names, kind_of  # noqa: PLC0415

    now = run_clock().now()
    spread_keys: dict[str, dict[str, None]] = {}
    shown: list[dict[str, object]] = []
    for record, row in zip(records, rows, strict=True):
        if not isinstance(record, BaseModel):
            shown.append(row)
            continue
        fields = {
            dumped: (name, _marks(type(record).model_fields[name]))
            for name, dumped in dumped_names(type(record)).items()
        }
        roles = status_roles(type(record))
        spelled = kind_of(type(record)) is not None  # 11.x ``kind=``: the seam below
        out: dict[str, object] = {}
        for dumped, cell in row.items():  # the dump's order is the table's
            name, marks = fields.get(dumped, (None, []))
            value = getattr(record, name) if name is not None else cell
            if Spread in marks and isinstance(value, Mapping):
                keys = spread_keys.setdefault(dumped, {})
                for key, item in value.items():
                    keys[str(key)] = None
                    if str(key) not in row:
                        out[str(key)] = item
            else:
                out[dumped] = _marked_cell(marks, value, cell, now)
            role = _role(roles.get(name or ""), value, seam=spelled and dumped in _SEAM_COLUMNS)
            if role is not None and isinstance(text := out.get(dumped), str):
                out[dumped] = Roled(text, role)
        shown.append(out)
    if columns is not None and spread_keys:
        columns = [key for name in columns for key in (spread_keys.get(name) or [name])]
    added = [key for keys in spread_keys.values() for key in keys]
    return shown, None if columns is None else list(columns), added


def table_row(record: BaseModel, row: Mapping[str, object] | None = None) -> dict[str, object]:
    """``row`` (``record``'s dump by default) as a table shows it: marked cells, roled words.

    For a view a command builds from a record (a ``fix`` argv as the line to
    type, warnings joined into one cell) and prints itself: a plain ``dict`` row
    has no fields, so a table draws its words in no role. A key ``record`` has
    no field for stays as it is.
    """
    shown = dict(record.model_dump(mode="json") if row is None else row)
    [out], _, _ = table_rows([record], [shown], None)
    return out


def _marked_cell(marks: list[object], value: object, cell: object, now: datetime) -> object:
    """A cell as its field's table mark shows it (``cell``, the dumped value, when unmarked)."""
    from untaped.sdk.words import ago_text, elapsed_text, size_text  # noqa: PLC0415

    if Bytes in marks and isinstance(value, int):
        return size_text(value)
    if Age in marks and isinstance(value, datetime):
        return ago_text(value, now)
    if Duration in marks and isinstance(value, int | float) and not isinstance(value, bool):
        return elapsed_text(value)
    if ObjectName in marks and isinstance(value, str) and _FULL_NAME.fullmatch(value):
        return value[:_SHORT_NAME]
    from untaped.sdk.records import TableText  # noqa: PLC0415 - records import fields

    if isinstance(value, BaseModel) and isinstance(value, TableText):
        return value.table_text()
    return cell


def _role(words: Mapping[str, str] | None, value: object, *, seam: bool) -> str | None:
    """The role ``value``'s word is drawn in: its field's ``Status``, else the 11.x seam's."""
    word = _plain(value)
    if not isinstance(word, str):
        return None  # a number, a list, ``None``: no word to draw
    role = words.get(word) if words else None
    if role is None and seam:
        role = _SEAM_ROLES.get(word.lower())
    return role


#: The 11.x seam, removed with ``kind=``: a record of a plugin not yet moved
#: keeps colouring these columns by these words where its field has no ``Status``.
_SEAM_COLUMNS = frozenset({"status", "action", "result", "state", "job_status"})
_SEAM_ROLES: dict[str, str] = {
    **dict.fromkeys(
        ("failed", "failure", "fail", "error", "errored", "conflict", "partial"), "error"
    ),
    **dict.fromkeys(
        (
            "warn",
            "warning",
            "skipped",
            "pending",
            "waiting",
            "running",
            "planned",
            "stale",
            "outdated",
            "unavailable",
            "canceled",
            "cancelled",
            "cancel_requested",
        ),
        "warning",
    ),
    **dict.fromkeys(
        (
            "successful",
            "success",
            "pass",
            "passed",
            "ok",
            "ready",
            "created",
            "updated",
            "deleted",
            "synced",
            "cloned",
            "added",
            "removed",
            "completed",
        ),
        "success",
    ),
}

#: A full git object name, and how much of it a table shows.
_FULL_NAME = re.compile(r"[0-9a-f]{40}")
_SHORT_NAME = 10


def spreads(model: type[BaseModel]) -> bool:
    """Whether ``model`` has a ``Spread`` field, whose keys a run names as columns."""
    return any(Spread in _marks(info) for info in model.model_fields.values())


def default_columns(model: type[BaseModel]) -> tuple[str, ...]:
    """A table's default columns for ``model``: its ``Column`` fields, else ``table_columns``."""
    return column_fields(model) or tuple(getattr(model, "table_columns", None) or ())


def column_fields(model: type[BaseModel]) -> tuple[str, ...]:
    """The fields of ``model`` marked ``Column``: a table's default columns (none: every field)."""
    return tuple(name for name, info in model.model_fields.items() if Column in _marks(info))


def following_fields(model: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """The fields of ``model`` marked ``Following``, each with the model of its items.

    A table prints each as a second table after the first; a field that is
    not a list of models holds nothing to print (composition refuses it).
    """
    found: dict[str, type[BaseModel]] = {}
    for name, info in model.model_fields.items():
        if Following in _marks(info) and (item := list_item_model(info.annotation)) is not None:
            found[name] = item
    return found


def unprintable_following(model: type[BaseModel]) -> list[str]:
    """The fields of ``model`` marked ``Following`` that are not a list of models."""
    return [
        name
        for name, info in model.model_fields.items()
        if Following in _marks(info) and list_item_model(info.annotation) is None
    ]


def list_item_model(annotation: object) -> type[BaseModel] | None:
    """The model ``annotation`` lists (``list[M]``, ``list[M] | None``), if it lists one."""
    for each in (annotation, *get_args(annotation)):
        if get_origin(each) is Annotated:
            each = get_args(each)[0]
        if get_origin(each) is list:
            [item] = get_args(each) or (None,)
            if isinstance(item, type) and issubclass(item, BaseModel):
                return item
    return None


def key_field(model: type[BaseModel]) -> str | None:
    """The field of ``model`` marked ``Key``: what a bare identifier stands for."""
    return next((name for name, info in model.model_fields.items() if Key in _marks(info)), None)


def _marks(info: FieldInfo) -> list[object]:
    """A field's marks, those inside ``X | None`` too (pydantic lifts only top-level ones)."""
    marks = list(info.metadata)
    for arg in get_args(info.annotation):
        if get_origin(arg) is Annotated:
            marks.extend(get_args(arg)[1:])
    return marks


@cache
def status_roles(model: type[BaseModel]) -> dict[str, dict[str, str]]:
    """Each field of ``model`` with a ``Status``, its word → role map merged along the MRO."""
    found: dict[str, dict[str, str]] = {}
    for cls in reversed(model.__mro__):
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            continue
        for name, info in cls.model_fields.items():  # an inherited field repeats its base's
            for mark in _marks(info):
                if isinstance(mark, Status):
                    found[name] = {**found.get(name, {}), **mark.roles}
    return found


def _type_argument(annotation: object) -> type[BaseModel] | None:
    """The model a generic field names (``Output[R]`` gives ``R``), if it is one model."""
    args = get_args(annotation)
    model = args[0] if args else None
    return model if isinstance(model, type) and issubclass(model, BaseModel) else None


class Stdin[R: BaseModel](FieldBlock):
    """What a command accepts on stdin: ``stdin: Stdin[Salutation]`` adds ``--stdin``.

    ``R`` is the record kind (or a union, or a base every accepted kind
    subclasses; ``Stdin[Record]`` takes any installed kind, and reads a
    row whose kind is ``null`` with every field it carries); a bare line
    stands for ``R``'s ``Key`` field. Accepting a kind never claims it.

    ``Stdin(via=RepoSource.to_repo)`` also takes the rows of any provider's
    own kind and reads each through that provider's bridge, so ``R`` is the
    owner's model; a row ``R`` takes reads as it would without ``via``. The
    contract's providers are built, and a provider's settings read, only
    when a row of a provider's kind arrives. A kind no provider reads, or a
    kind-less line, exits 2; a provider's row that doesn't validate as its
    record exits 1; an unready provider, or a bridge returning an invalid
    owner model, exits 4.
    """

    field_kind = "stdin"
    options = (FieldOption("--stdin", "bool", "Read the input from stdin."),)

    def __init__(self, *, via: Callable[..., Any] | None = None) -> None:
        self.via = via
        self._requested = False
        self._models: tuple[type[BaseModel], ...] = ()
        #: Each kind read as its model; with ``_any`` the kinds core knows.
        self._kinds: Mapping[str, type[BaseModel]] = {}
        #: Every kind core knows, for a row ``via`` reads through its provider.
        self._known: Mapping[str, type[BaseModel]] = {}
        self._any = False

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Stdin[R]:
        """A copy for one invocation: ``--stdin``, and the kinds ``R`` accepts.

        A kind is accepted when its model is one of ``R``'s models or a subclass;
        a model that is a record base (``Stdin[Record]``) accepts any kind.
        """
        from untaped.sdk.diagnostic import WireModel  # noqa: PLC0415 - reading only
        from untaped.sdk.records import Record  # noqa: PLC0415 - reading only

        args = get_args(call.annotation)
        accepted = args[0] if args else None
        members = get_args(accepted) if get_origin(accepted) in (UnionType, Union) else (accepted,)
        models = tuple(m for m in members if isinstance(m, type) and issubclass(m, BaseModel))
        filled = _copy(self)
        filled._requested = bool(flags.get("--stdin"))
        filled._models = models
        filled._any = any(model in (BaseModel, WireModel, Record) for model in models)
        known = call.kinds()
        filled._kinds = {
            kind: model for kind, model in known.items() if filled._any or issubclass(model, models)
        }
        if self.via is not None:
            filled._known = known  # composition checked that ``via`` is a bridge returning R
        return filled

    @property
    def requested(self) -> bool:
        """``--stdin`` was passed: what to read comes from stdin, whatever it carries."""
        return self._requested

    def identifiers(self, given: Sequence[str] = (), *, allow_empty: bool = False) -> list[str]:
        """The identifiers given, or read from stdin: bare lines, or each row's ``Key`` field.

        Both at once, or neither (unless ``allow_empty``), is a usage error.
        """
        return [
            value if isinstance(value, str) else self._identifier(value)
            for value in self._read(given, allow_empty=allow_empty)
        ]

    def values(self, given: Sequence[str] = (), *, allow_empty: bool = False) -> list[str | R]:
        """The arguments as given, or what stdin carried: each a bare line or a validated row."""
        return [
            value if isinstance(value, str) else self._row(value)
            for value in self._read(given, allow_empty=allow_empty)
        ]

    def envelopes(self, *, allow_empty: bool = False) -> list[str | PipeEnvelope]:
        """What stdin carried, unvalidated: each a bare line or a row's kind, record and line.

        For a command whose readers validate each row by its kind (lint's
        providers), so a row no installed plugin declares, or one its model
        refuses, is that reader's to report instead of ending the read.
        """
        return self._read((), allow_empty=allow_empty, what="records")

    def kind(self, row: BaseModel) -> str | None:
        """The kind of ``row``'s model among those this command reads (``None``: none of them).

        A row a ``via=`` bridge turned into ``R`` has ``R``'s kind, not the provider's.
        """
        return next((kind for kind, model in self._kinds.items() if model is type(row)), None)

    def rows(self, given: Sequence[str] = (), *, allow_empty: bool = False) -> list[R]:
        """Whole validated rows from stdin; a bare identifier is a usage error."""
        rows: list[R] = []
        for value in self.values(given, allow_empty=allow_empty):
            if isinstance(value, str):
                raise UsageError(f"expected rows on stdin, got the bare line {value!r}")
            rows.append(value)
        return rows

    def _read(
        self, given: Sequence[str], *, allow_empty: bool, what: str = "identifiers"
    ) -> list[str | PipeEnvelope]:
        from untaped.sdk._stdin import read_stdin_input  # noqa: PLC0415 - only a reader pays

        if not self._requested:
            if not given and not allow_empty:
                raise UsageError("at least one identifier is required (or use --stdin)")
            return list(given)
        if given:
            raise UsageError("provide identifiers as positional args or via --stdin, not both")
        kinds = None if self._any or self.via is not None else tuple(self._kinds)
        piped = read_stdin_input(accept_kinds=kinds, what=what, allow_empty=allow_empty)
        return list(piped.values) if piped.records is None else list(piped.records)

    def _model(self, envelope: PipeEnvelope) -> type[BaseModel]:
        if envelope.kind is not None:
            model = self._kinds.get(envelope.kind)
            if model is not None:
                return model
            raise ConfigError(
                f"line {envelope.lineno}: no installed plugin declares the kind "
                f"{envelope.kind!r}, so its rows cannot be read",
                category="invalid",
            )
        if not self._models:
            raise TypeError("Stdin was not filled with the models it accepts")
        if self._any:
            from untaped.sdk.records import KindlessRow  # noqa: PLC0415 - reading only

            return KindlessRow
        return self._models[0]

    def _through(self, envelope: PipeEnvelope) -> bool:
        """Whether ``via`` reads this row: a provider's kind or none, never one ``R`` takes."""
        return self.via is not None and envelope.kind not in self._kinds

    def _identifier(self, envelope: PipeEnvelope) -> str:
        if self._through(envelope):
            row = self._row(envelope)
            model: type[BaseModel] = type(row)
            record: Mapping[str, object] = row.model_dump()
        else:
            model, record = self._model(envelope), envelope.record
        field = key_field(model)
        if field is None:
            raise ConfigError(
                "stdin is untaped pipe format but this command cannot map records to "
                "identifiers: no field of the accepted model is marked Key",
                category="invalid",
            )
        value = record.get(field)
        identifier = "" if value is None else str(value).strip()
        if not identifier:
            raise ConfigError(
                f"line {envelope.lineno}: record {field!r} is missing or blank",
                category="invalid",
            )
        return identifier

    def _row(self, envelope: PipeEnvelope) -> R:
        if self._through(envelope):
            from untaped.sdk.contracts._select import bridged  # noqa: PLC0415 - only via pays

            given = None if envelope.kind is None else self._known.get(envelope.kind)
            return cast("R", bridged(cast("Callable[..., Any]", self.via), envelope, given))
        try:
            return cast("R", self._model(envelope).model_validate(envelope.record))
        except ValidationError as exc:
            raise ConfigError(
                f"line {envelope.lineno}: {first_validation_error(exc)}", category="invalid"
            ) from exc


class Lines(FieldBlock):
    """Raw stdin lines with no flag, for a command whose stdin is its protocol.

    ``lines: Lines`` is read in ``run`` as ``for line in self.lines``: each
    line stripped, blank lines skipped, and nothing from a terminal (it never
    waits for typing).
    """

    field_kind = "lines"

    def __iter__(self) -> Iterator[str]:
        from untaped.sdk._stdin import read_stdin  # noqa: PLC0415 - read only when iterated

        return iter(read_stdin())


class Confirm(FieldBlock):
    """This command asks before it commits: adds ``--yes`` and ``--dry-run``.

    ``run`` calls it once, where it is about to commit:
    ``if self.confirm("Delete profile 'x'?", preview=lambda: lines): ...``.
    """

    field_kind = "confirm"
    panel = "Confirmation"
    options = (
        FieldOption("--yes", "bool", "Go ahead without asking.", short="-y"),
        FieldOption("--dry-run", "bool", "Show what would change and change nothing."),
    )

    def __init__(self) -> None:
        self._yes = False
        self._dry_run = False
        #: The command line asking (``untaped hello delete``); ``None`` while unfilled.
        self._command: str | None = None

    @property
    def yes(self) -> bool:
        """``--yes`` was passed."""
        return self._yes

    @property
    def dry_run(self) -> bool:
        """``--dry-run`` was passed."""
        return self._dry_run

    def as_dry_run(self) -> Confirm:
        """The same answers with ``--dry-run`` on: what a ``--check`` flag passes on.

        Noted on the current run, so core treats the invocation as a preview:
        call it at the top of ``run``, before any early return.
        """
        from untaped.sdk._run import note_preview  # noqa: PLC0415 - only a --check pays

        note_preview()
        dry = _copy(self)
        dry._dry_run = True
        return dry

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Confirm:
        """A copy for one invocation, from ``--yes`` and ``--dry-run``."""
        filled = _copy(self)
        filled._yes = flags.get("--yes") is True
        filled._dry_run = flags.get("--dry-run") is True
        filled._command = " ".join(call.words)
        return filled

    def ensure_answerable(self, question: str) -> None:
        """Refuse now when ``question`` could never be answered, before work the ask would waste.

        For a command that asks only after slow or side-effecting work (running
        hooks to plan): without a terminal, ``--yes`` or ``--dry-run`` it raises
        the :class:`NeedsYesError` (exit 2) asking would raise later; otherwise
        nothing happens, and the command asks as usual.
        """
        from untaped.sdk._run import current_run  # noqa: PLC0415 - only a command asking pays

        if self._dry_run or self._yes:
            return
        if self._command is None:
            raise NeedsYesError(question)
        try:
            with current_run().terminal().terminal():
                pass
        except UsageError:
            raise NeedsYesError(question, command=self._command) from None

    def __call__(
        self,
        question: str,
        *,
        preview: Callable[[], Iterable[str | Text]] | None = None,
        needed: bool = True,
    ) -> bool:
        """Whether to carry out what the command is about to do.

        ``True``: go ahead. ``False`` only under ``--dry-run``, after the preview:
        nothing happens, and the command prints its planned rows. A decline never
        returns (exit 1); without a terminal and ``--yes`` it raises
        :class:`NeedsYesError` (exit 2), and so does an unfilled field, so a command
        built outside a run never goes ahead unasked. ``preview`` returns the lines
        printed on stderr before the question; ``--yes`` and ``needed=False`` skip it.
        """
        from untaped.sdk._run import current_run  # noqa: PLC0415 - only a command asking pays

        if self._dry_run:
            _print_preview(preview)
            return False
        if not needed or self._yes:
            return True
        if self._command is None:
            raise NeedsYesError(question)
        terminal = current_run().terminal()
        with ExitStack() as stack:
            try:
                stack.enter_context(terminal.terminal())
            except UsageError:
                raise NeedsYesError(question, command=self._command) from None
            _print_preview(preview)
            if not terminal.confirm(question):
                raise OperationCancelledError
        return True


def _print_preview(preview: Callable[[], Iterable[str | Text]] | None) -> None:
    """The preview's lines on stderr, unmuted: what the user decides on or would get."""
    if preview is None:
        return
    from untaped.sdk._run import current_run  # noqa: PLC0415 - only a preview pays

    diag = current_run().diag
    for line in preview():
        diag.info(line, unmuted=True)


class DryRun(FieldBlock):
    """This command writes without asking and can preview instead: adds ``--dry-run`` alone.

    ``if self.dry_run.go_ahead(preview=lines): write()`` prints the preview,
    as :class:`Confirm` does, and writes nothing after ``--dry-run``; ``if
    self.dry_run: ...`` is true after ``--dry-run``. A command that asks first
    takes :class:`Confirm`, which has its own ``--dry-run``; one command takes
    one of the two.
    """

    field_kind = "dry_run"
    panel = "Confirmation"
    options = (FieldOption("--dry-run", "bool", "Show what would change and change nothing."),)

    def __init__(self) -> None:
        self._dry_run = False

    def __bool__(self) -> bool:
        """``--dry-run`` was passed."""
        return self._dry_run

    def go_ahead(self, preview: Callable[[], Iterable[str | Text]] | None = None) -> bool:
        """``True``: write. After ``--dry-run``, ``False`` once the preview is on stderr."""
        if not self._dry_run:
            return True
        _print_preview(preview)
        return False

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> DryRun:
        """A copy for one invocation, from ``--dry-run``."""
        del call
        filled = _copy(self)
        filled._dry_run = flags.get("--dry-run") is True
        return filled


class Refresh(FieldBlock):
    """Which caches ``--refresh`` / ``--cached`` are about: ``refresh: Refresh = Refresh(MOTD)``.

    Each argument is a cache on the plugin class or a cached method of a
    contract the plugin offers or fills; the help is generated from them.
    """

    field_kind = "refresh"
    options = (
        FieldOption(
            "--refresh",
            "bool",
            "--refresh checks upstream now; --cached reads what is cached and never checks.",
            negative="--cached",
        ),
    )

    def __init__(self, *caches: object) -> None:
        self.caches = caches
        self._directive: bool | None = None

    @property
    def directive(self) -> bool | None:
        """``True`` after ``--refresh``, ``False`` after ``--cached``, ``None`` otherwise.

        What a cache's ``serve(refresh=...)`` takes.
        """
        return self._directive

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Refresh:
        """A copy for one invocation, from ``--refresh`` / ``--cached``."""
        del call
        directive = flags.get("--refresh")
        filled = _copy(self)
        filled._directive = directive if isinstance(directive, bool) else None
        return filled


class Parallel(FieldBlock):
    """How many operations run at once: ``-j/--parallel``, ``Parallel(cap=10)``.

    ``default`` is a number or a ``key(...)`` to the setting that holds one;
    ``cap`` clamps the value with a warning. ``unset`` is the number an
    optional setting left unset stands for (``Parallel(default=key(S).parallel,
    unset=8, cap=16)``): the command's own default, so the cap lowers it
    without a warning.
    """

    field_kind = "parallel"
    options = (
        FieldOption(
            "--parallel", "int", "Maximum number of operations to run at once.", short="-j"
        ),
    )

    def __init__(
        self,
        *,
        default: int | SettingKey = 4,
        unset: int | None = None,
        cap: int | None = None,
    ) -> None:
        if any(isinstance(each, bool) for each in (default, unset, cap)):
            raise ValueError("Parallel's default, unset and cap are numbers, not booleans")
        if isinstance(default, int) and default < 1:
            raise ValueError("Parallel's default is at least 1")
        if unset is not None and not isinstance(default, SettingKey):
            raise ValueError("Parallel's unset is for a default read from a setting")
        if unset is not None and unset < 1:
            raise ValueError("Parallel's unset is at least 1")
        if cap is not None and cap < 1:
            raise ValueError("Parallel's cap is at least 1")
        self.default = default
        self.unset = unset
        self.cap = cap
        self._requested: int | None = None
        self._value: int | None = None
        #: Only a per-invocation copy keeps its value: an unfilled field is the
        #: class default every build shares, so it reads its setting afresh.
        self._filled = False

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Parallel:
        """A copy for one invocation, from ``-j/--parallel``; below 1 is a usage error."""
        del call
        requested = _count(flags, "--parallel")
        filled = _copy(self)
        filled._requested, filled._value, filled._filled = requested, None, True
        return filled

    @property
    def value(self) -> int:
        """How many operations run at once: ``-j``, else the default or its setting, capped.

        A value above ``cap`` is clamped with a warning, once per invocation;
        an unset setting's ``unset`` is lowered to ``cap`` silently. Read on
        an unfilled block under a run, it is the default (no ``-j``).
        """
        if self._value is not None:
            return self._value
        if self._requested is not None:
            value = self._clamped(self._requested, f"--parallel {self._requested}")
        else:
            value = self._from_default()
        if self._filled:
            self._value = value
        return value

    def _from_default(self) -> int:
        if isinstance(self.default, int):
            return self._clamped(self.default, f"the default {self.default}")
        from untaped.sdk._run import current_run, settings_of  # noqa: PLC0415 - a setting pays

        model = SettingKey.model_of(self.default)
        value: object = settings_of(model)
        for part in SettingKey.path_of(self.default):
            value = getattr(value, part)
        if value is None and self.unset is not None:
            return self.unset if self.cap is None else min(self.unset, self.cap)
        setting = f"{current_run().composition.owner_of_settings(model)}.{self.default}"
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(
                f"setting {setting} is {value!r}; it must be a number of at least 1",
                category="invalid",
            )
        return self._clamped(value, f"{setting} {value}")

    def map[ItemT, ResultT](
        self,
        fn: Callable[[ItemT], ResultT],
        items: Iterable[ItemT],
        *,
        ordered: bool = False,
        on_abort: Callable[[], None] | None = None,
        while_running: Callable[[], None] | None = None,
    ) -> Iterator[tuple[ItemT, ResultT]]:
        """Run ``fn`` on every item, :attr:`value` at a time, yielding ``(item, result)``.

        In completion order, so a slow first item never holds back the rest
        (``ordered=True`` keeps the input order). An exception from ``fn`` is
        raised here: catch it in ``fn`` to turn it into a failed row.
        ``on_abort`` runs when the work stops early (Ctrl-C, an exception,
        the iterator closed) for the calls in flight, such as killing their
        jobs; ``while_running`` does foreground work on this thread while the
        workers run, such as draining a queue they feed, and must return on
        its own: the pairs come after it.
        """
        from untaped.sdk._concurrency import bounded_iter  # noqa: PLC0415 - threads pay

        return bounded_iter(
            fn,
            items,
            concurrency=self.value,
            ordered=ordered,
            on_abort=on_abort,
            while_running=while_running,
        )

    def _clamped(self, value: int, source: str) -> int:
        """``value`` within the cap; ``source`` says where it came from in the warning."""
        if self.cap is None or value <= self.cap:
            return value
        from untaped.sdk._run import current_run  # noqa: PLC0415 - only a clamp pays

        current_run().diag.warning(f"{source} clamped to {self.cap} (this command's cap)")
        return self.cap


class Limit(FieldBlock):
    """``--limit N``: return at most this many results (unset: no limit).

    ``cap`` is the most a command can return (``Limit(default=30, cap=1000)``):
    a larger ``--limit`` is clamped with a warning, and help says the cap.
    """

    field_kind = "limit"
    options = (FieldOption("--limit", "int", "Return at most this many results."),)

    def __init__(self, *, default: int | None = None, cap: int | None = None) -> None:
        if isinstance(default, bool) or isinstance(cap, bool):
            raise ValueError("Limit's default and cap are numbers, not booleans")
        if default is not None and default < 1:
            raise ValueError("Limit's default is at least 1")
        if cap is not None and cap < 1:
            raise ValueError("Limit's cap is at least 1")
        if default is not None and cap is not None and default > cap:
            raise ValueError("Limit's default is at most its cap")
        self.default = default
        self.cap = cap
        self._requested: int | None = None
        self._clamped = False

    @property
    def value(self) -> int | None:
        """At most this many results; ``None`` is no limit.

        A ``--limit`` above ``cap`` is the cap, with a warning once per
        invocation.
        """
        if self._requested is None:
            return self.default
        if self.cap is None or self._requested <= self.cap:
            return self._requested
        if not self._clamped:
            from untaped.sdk._run import current_run  # noqa: PLC0415 - only a clamp pays

            current_run().diag.warning(
                f"--limit {self._requested} clamped to {self.cap} (this command's cap)"
            )
            self._clamped = True
        return self.cap

    def _option_default(self, option: FieldOption) -> str | int | None:
        return self.default if option.flag == "--limit" else None

    def _option_help(self, option: FieldOption) -> str:
        if self.cap is None or option.flag != "--limit":
            return option.help
        return f"{option.help.removesuffix('.')}, up to {self.cap}."

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Limit:
        """A copy for one invocation, from ``--limit``; below 1 is a usage error."""
        del call
        filled = _copy(self)
        filled._requested, filled._clamped = _count(flags, "--limit"), False
        return filled


def _count(flags: Mapping[str, object], flag: str) -> int | None:
    """The number ``flag`` was given, if any; below 1 is a usage error."""
    value = flags.get(flag)
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < 1:
        raise UsageError(f"{flag}: must be at least 1, got {value}")
    return value


FIELD_BLOCKS: tuple[type[FieldBlock], ...] = (
    Output,
    Stdin,
    Lines,
    Confirm,
    Refresh,
    Parallel,
    Limit,
)
"""Every field type core fills, in the order their panels list."""

#: The short flags core's blocks (and ``--help``) own; a command's ``Short`` may not take one.
CORE_SHORTS: frozenset[str] = frozenset(
    {"-h", *(option.short for block in FIELD_BLOCKS for option in block.options if option.short)}
)
