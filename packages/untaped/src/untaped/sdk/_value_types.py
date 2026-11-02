"""Core's value types: a file parsed as a model, a path that exists, a value or stdin, a pair."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast, get_args

from pydantic import AfterValidator, BaseModel, BeforeValidator, JsonValue, ValidationError
from pydantic_core import core_schema

from untaped.sdk._block import OR_STDIN
from untaped.sdk.errors import (
    ConfigError,
    ErrorCategory,
    System,
    UntapedError,
    first_validation_error,
)

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler


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
                f"{self.path}: {first_validation_error(exc)}",
                category="invalid",
                system=System.LOCAL,
            ) from exc

    def save(self, model: M, *, replace: bool = False) -> None:
        """Write ``model`` as YAML or JSON by the suffix; replace a file only if ``replace``."""
        import yaml  # noqa: PLC0415 - only a command that writes pays for it

        from untaped.sdk.storage._fs import atomic_write  # noqa: PLC0415 - storage is a group

        if self.path.exists() and not replace:
            raise UntapedError(
                f"{self.path} already exists; remove it or choose another path",
                category=ErrorCategory.FAILED,
                system=System.LOCAL,
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


def _existing_file(path: Path) -> Path:
    """``path``, refused with its name when it is no file."""
    if not path.is_file():
        raise ValueError(
            f"path is not a file: {path}" if path.exists() else f"path does not exist: {path}"
        )
    return path


ExistingFile = Annotated[Path, AfterValidator(_existing_file)]
"""A path that must name an existing file (exit 2 otherwise, naming the path)."""


TextOrStdin = Annotated[str, OR_STDIN]
"""A value, or ``-`` to read it from stdin.

A pipe that is empty once its last newline is trimmed is a usage error, and so
is a second ``-`` reading stdin in the same command line. It marks one value,
not list items (``list[PathOrStdin]`` does).
"""


PathOrStdin = Annotated[Path, OR_STDIN]
"""A path, or ``-`` to read the content from stdin.

Core refuses ``-`` with ``--stdin`` or from a terminal; the value stays
``Path("-")`` for the command to read with :func:`read_stdin_text`. It also
marks list items: ``Positional[list[PathOrStdin]]``.
"""


def _pair(value: object, *, shape: str) -> tuple[str, str]:
    if isinstance(value, tuple):
        return cast("tuple[str, str]", value)
    if not isinstance(value, str):
        raise ValueError(f"expected {shape}")
    key, sep, rest = value.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"expected {shape}, got {value!r}")
    return key.strip(), rest


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
"""``KEY=VALUE``, split on the first ``=``, key stripped: ``labels: list[KeyValue]`` repeats."""


KeyJson = Annotated[tuple[str, JsonValue], BeforeValidator(_key_json)]
"""``KEY=<json>``, key stripped: the rest is JSON (``count=3``, ``tags=["a"]``)."""
