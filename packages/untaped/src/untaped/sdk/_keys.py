"""Typed references to a model's fields: ``key(HelloSettings).greeting``.

Below every block that takes a key (fields, marks, storage, contracts), so
each of them can import it, and the marks can mark a field block's method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel
from pydantic_core import core_schema

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue


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
            nested = model_in(annotation)
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


def model_in(annotation: object) -> type[BaseModel] | None:
    """The one pydantic model ``annotation`` is (``Model`` or ``Model | None``), if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    models = [
        arg for arg in get_args(annotation) if isinstance(arg, type) and issubclass(arg, BaseModel)
    ]
    return models[0] if len(models) == 1 else None


def key(model: type[BaseModel]) -> SettingKey:
    """A typed reference to a model's field: ``key(HelloSettings).greeting``.

    A settings field names a setting; a record's field names a table column,
    ``Output(columns=(key(Repo).full_name,))``.
    """
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"key() takes a model class, not {model!r}")
    return SettingKey(model)
