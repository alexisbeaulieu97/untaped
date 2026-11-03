"""A server's version, as a ``ServerApi`` reads it: :class:`ServerVersion` and :class:`Release`.

Their own module, apart from the block, so a plugin's settings model can
type its override (``server_version: JiraVersion | None``) without loading
the block, its HTTP client and its doctor row: composition imports every
settings model on every start (``tests/repo/test_import_cost.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Self, cast

from pydantic_core import core_schema

from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue


def _text_schema(read: Callable[[Any], Any]) -> core_schema.CoreSchema:
    """A schema that validates through ``read`` and serialises as ``str()``: one setting value."""
    return core_schema.no_info_plain_validator_function(
        read, serialization=core_schema.plain_serializer_function_ser_schema(str)
    )


@experimental
class Release(tuple[int, ...]):
    """A dotted release, ``Release.parse("11.2.1")``: a tuple of numbers, so ``>= (12,)`` works.

    ``str()`` is ``11.2.1``. As a pydantic field it validates from that text
    (or a sequence of numbers, as an API returns it) and serialises to it.

    Experimental. First consumer: jira.
    """

    __slots__ = ()

    def __new__(cls, numbers: Sequence[int]) -> Self:
        parts = tuple(numbers)
        if not parts or not all(
            isinstance(part, int) and not isinstance(part, bool) and part >= 0 for part in parts
        ):
            raise ValueError(f"a release is one or more numbers, like 11.2.1, not {numbers!r}")
        return super().__new__(cls, parts)

    @classmethod
    def parse(cls, text: str) -> Self:
        """The release ``text`` spells (``11.2.1``); :class:`ValueError` for anything else."""
        parts = text.strip().split(".")
        if not all(part.isdigit() and part.isascii() for part in parts):
            raise ValueError(f"a release is dotted numbers, like 11.2.1, not {text!r}")
        return cls([int(part) for part in parts])

    def __str__(self) -> str:
        return ".".join(map(str, self))

    def __repr__(self) -> str:
        return f"Release({str(self)!r})"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def read(value: object) -> Release:
            if isinstance(value, cls):
                return value
            if isinstance(value, str):
                return cls.parse(value)
            if isinstance(value, list | tuple):
                return cls(cast("Sequence[int]", value))
            raise ValueError(f"a release is dotted numbers, like 11.2.1, not {value!r}")

        return _text_schema(read)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        del schema, handler
        return {"type": "string", "examples": ["11.2.1"]}


@experimental
class ServerVersion:
    """What one server runs: the bound of a :class:`ServerApi`'s version type.

    Subclass it as a frozen dataclass of the facts adapters choose by (a
    deployment, a :class:`Release`). ``str()`` is how people read the
    version and how they set it (``Jira Data Center 11.2.1``); :meth:`parse`
    reads that text back, and composition checks ``parse(str(v)) == v`` for
    every recorded version. No order is needed: adapters compare their own
    fields. As a pydantic field (the override setting) it validates from its
    text and serialises to it, so it is one setting value.

    Experimental. First consumer: jira.
    """

    @classmethod
    def parse(cls, text: str) -> Self:
        """The version ``text`` spells; :class:`ValueError` when it spells none."""
        raise NotImplementedError(f"{cls.__qualname__} defines no parse()")

    def __str__(self) -> str:
        raise NotImplementedError(f"{type(self).__qualname__} defines no __str__()")

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        target = cast("type[ServerVersion]", source)

        def read(value: object) -> ServerVersion:
            if isinstance(value, target):
                return value
            if isinstance(value, str):
                return target.parse(value)
            raise ValueError(f"a {target.__name__} is written as text, not {value!r}")

        return _text_schema(read)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        del schema, handler
        return {"type": "string"}


__all__ = ["Release", "ServerVersion"]
