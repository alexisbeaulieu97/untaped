"""What a command's fields can be: positionals and marks, core's value types, core-filled blocks.

A command is a pydantic model (:class:`untaped.sdk.plugin.Command`); this
module is the vocabulary its fields use beyond plain Python types:

- ``Positional[T]`` makes an argument; ``AllowHyphen`` and ``Short("-x")``
  shape one; ``Column``, ``Key``, ``Following`` and ``Written`` mark a
  record's or a file option's fields for core.
- ``File[M]``, ``ExistingFile``, ``TextOrStdin``, ``PathOrStdin``,
  ``KeyValue`` and ``KeyJson`` are value types core parses.
- ``Output[R]``, ``Stdin[R]``, ``Lines``, ``Confirm``, ``Refresh``,
  ``Parallel`` and ``Limit`` are fields core fills: each adds its fixed flags,
  configured by the field's default value.
- ``key(Model).field`` is a typed reference to one settings field.

The declaration walk (``untaped.runtime.composition``) reads all of it; a
command never parses a token itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast, get_args

from pydantic import BaseModel, BeforeValidator, JsonValue, ValidationError
from pydantic.types import PathType
from pydantic_core import core_schema

from untaped.sdk._block import OR_STDIN, POSITIONAL, FieldBlock, FieldOption, Mark
from untaped.sdk.errors import ConfigError, ErrorCategory, UntapedError, first_validation_error

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler

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
"""A record field a table shows by default: ``name: Annotated[str, Column]`` (none marked: all)."""

Key = Mark("Key")
"""The record field a bare identifier stands for, on stdin and in state rows."""

Following = Mark("Following")
"""A list-of-models field a table prints as a second table after the first."""

Written = Mark("Written")
"""A ``File[M]`` option the command writes rather than reads: the model's "Starter" line."""


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

    ``.path`` is the path, for a plugin that parses itself; ``.load()`` reads
    and validates the document; ``.save(model)`` writes one and refuses to
    replace an existing file unless ``replace=True``. The model's docstring is
    the input's page; ``Annotated[File[M], Written]`` is a file the command
    writes.
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


OUTPUT_FORMATS: tuple[str, ...] = ("table", "json", "yaml", "pipe", "raw")
"""The formats every ``Output`` offers, before a plugin's own renderers."""


class Output[R: BaseModel](FieldBlock):
    """What a command prints: ``output: Output[Salutation]`` adds ``--format`` and ``--columns``.

    ``R`` is the record ``run`` yields or returns (the return type must
    agree); its ``Column`` marks are the table's default columns. The default
    configures it: ``Output(columns=("key", "summary"))`` gives this command
    its own default columns, ``Output(render={"tree": render_tree})`` adds a
    format of the plugin's own.
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
    ) -> None:
        self.columns = tuple(columns)
        self.render: Mapping[str, Callable[..., Any]] = dict(render or {})

    @property
    def formats(self) -> tuple[str, ...]:
        """Every value ``--format`` takes for this command."""
        return (*OUTPUT_FORMATS, *self.render)


class Stdin[R: BaseModel](FieldBlock):
    """What a command accepts on stdin: ``stdin: Stdin[Salutation]`` adds ``--stdin``.

    ``R`` is the record kind (or a union, or a base every accepted kind
    subclasses; ``Stdin[Record]`` takes any kind); a bare line stands for
    ``R``'s ``Key`` field. Accepting a kind never claims it. ``via=`` reads a
    provider's own kind through its contract's bridge method.
    """

    field_kind = "stdin"
    options = (FieldOption("--stdin", "bool", "Read the input from stdin."),)

    def __init__(self, *, via: Callable[..., Any] | None = None) -> None:
        self.via = via


class Lines(FieldBlock):
    """Raw stdin lines with no flag, for a command whose stdin is its protocol."""

    field_kind = "lines"


class Confirm(FieldBlock):
    """This command asks before it commits: adds ``--yes`` and ``--dry-run`` (#818)."""

    field_kind = "confirm"
    panel = "Confirmation"
    options = (
        FieldOption("--yes", "bool", "Go ahead without asking.", short="-y"),
        FieldOption("--dry-run", "bool", "Show what would change and change nothing."),
    )


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


class Parallel(FieldBlock):
    """How many operations run at once: ``-j/--parallel``, ``Parallel(cap=10)``.

    ``default`` is a number or a ``key(...)`` to the setting that holds one;
    ``cap`` clamps the value with a warning.
    """

    field_kind = "parallel"
    options = (
        FieldOption(
            "--parallel", "int", "Maximum number of operations to run at once.", short="-j"
        ),
    )

    def __init__(self, *, default: int | SettingKey = 4, cap: int | None = None) -> None:
        if isinstance(default, int) and default < 1:
            raise ValueError("Parallel's default is at least 1")
        if cap is not None and cap < 1:
            raise ValueError("Parallel's cap is at least 1")
        self.default = default
        self.cap = cap


class Limit(FieldBlock):
    """``--limit N``: return at most this many results (unset: no limit)."""

    field_kind = "limit"
    options = (FieldOption("--limit", "int", "Return at most this many results."),)

    def __init__(self, *, default: int | None = None) -> None:
        self.default = default


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
