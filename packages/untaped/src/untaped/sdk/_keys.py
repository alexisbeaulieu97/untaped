"""Typed references to a model's fields: ``key(HelloSettings).greeting``.

Below every block that takes a key (fields, marks, storage, contracts), so
each of them can import it, and the marks can mark a field block's method.
"""

from __future__ import annotations

import re
import types
import typing
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import core_schema

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue


class SettingKey:
    """A typed reference to one field of a settings model: ``key(HelloSettings).greeting``.

    Created by :func:`key`; each attribute step is checked against the
    model's fields, so a typo fails where it is written, and a nested model
    is followed (``key(AcmeSettings).server.url``). A ``dict[str, X]`` field
    steps into one entry by name, then into ``X``
    (``key(DotfilesSettings).sources["dotfiles"].url``); the name is checked
    against :data:`ENTRY_NAME`. The key knows its model and its path; core
    spells the full dotted key (with the plugin's section) from the
    composition. ``str()`` is the path inside the section.
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

    def __getitem__(self, name: str) -> SettingKey:
        value = map_value_type(self._annotation())
        if value is None:
            raise TypeError(
                f"{self!r} is not a map setting; [name] names an entry of a dict[str, ...] field"
            )
        if not isinstance(name, str) or ENTRY_NAME.fullmatch(name) is None:
            raise ValueError(f"{self!r} entry {name!r} can't be named in a key: {ENTRY_NAME_RULE}")
        return SettingKey(self._model, (*self._parts, name))

    __iter__ = None  # ``[name]`` names an entry; a key is not a sequence

    def _annotation(self) -> Any:
        """The type this key reaches: the settings model, a field's type, or a map entry's."""
        current: Any = self._model
        for part in self._parts:
            value = map_value_type(current)
            if value is not None:
                current = value
                continue
            model = model_in(current)
            assert model is not None  # each step was checked when the key was built
            current = model.model_fields[part].annotation
        return current

    def _field_model(self) -> type[BaseModel]:
        """The model the next attribute step reads: the settings model, or the nested one."""
        nested = model_in(self._annotation())
        if nested is None:
            raise AttributeError(
                f"{'.'.join(self._parts)} of {self._model.__name__} is a value, not a model"
            )
        return nested

    @staticmethod
    def model_of(setting: SettingKey) -> type[BaseModel]:
        """The settings model ``setting`` starts from (what composition maps to a section).

        A static method, read on the class, so no settings field can shadow it.
        """
        return setting._model

    @staticmethod
    def path_of(setting: SettingKey) -> tuple[str, ...]:
        """The field names (and map entry names) from the settings model down to ``setting``."""
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


def value_in(setting: SettingKey, settings: BaseModel) -> Any:
    """``setting``'s value in ``settings``, a validated instance of its model.

    ``None`` when a map entry on the way is missing.
    """
    value: Any = settings
    for part in SettingKey.path_of(setting):
        if isinstance(value, Mapping):
            value = value.get(part)
        elif value is not None:
            value = getattr(value, part)
    return value


ENTRY_NAME = re.compile(r"[a-z0-9_][a-z0-9_-]*")
"""The map entry names a key can address: no dot, so a key splits one way, and lower case,
because ``UNTAPED_*`` variables match without regard to case."""

ENTRY_NAME_RULE = (
    "a key names an entry with lower-case letters, digits, '_' and '-', not starting with '-'"
)
""":data:`ENTRY_NAME` in words, for the messages that refuse a name."""


def names_entry(setting: SettingKey) -> bool:
    """Whether ``setting`` steps into a map entry on its way (``sources["main"].url``)."""
    current: Any = SettingKey.model_of(setting)
    for part in SettingKey.path_of(setting):
        value = map_value_type(current)
        if value is not None:
            return True
        model = model_in(current)
        assert model is not None  # each step was checked when the key was built
        current = model.model_fields[part].annotation
    return False


def model_in(annotation: object) -> type[BaseModel] | None:
    """The one pydantic model ``annotation`` is (``Model`` or ``Model | None``), if any.

    Only a union is looked into: a ``list[Model]`` or ``dict[str, Model]`` is not a model.
    """
    annotation = _bare(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) not in (types.UnionType, typing.Union):
        return None
    models = [
        arg for arg in get_args(annotation) if isinstance(arg, type) and issubclass(arg, BaseModel)
    ]
    return models[0] if len(models) == 1 else None


def nested_model(annotation: object) -> type[BaseModel] | None:
    """The model a settings walk steps into for ``annotation`` (``Model``, ``Model | None``).

    Its settings are the ones inside it; a union with anything else
    (``Model | str``) is one setting. ``walk_settings`` reads fields by it.
    """
    if get_origin(annotation) in (types.UnionType, typing.Union):
        present = [arg for arg in get_args(annotation) if arg is not type(None)]
        annotation = present[0] if len(present) == 1 else None
    return (
        annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None
    )


def map_value_type(annotation: object) -> Any:
    """``X`` for a ``dict[str, X]`` annotation (or ``dict[str, X] | None``); ``None`` otherwise.

    An ``Annotated`` value type is returned bare.
    """
    annotation = _bare(annotation)
    if get_origin(annotation) in (types.UnionType, typing.Union):
        present = [arg for arg in get_args(annotation) if arg is not type(None)]
        annotation = present[0] if len(present) == 1 else None
    if get_origin(annotation) is not dict:
        return None
    args = get_args(annotation)
    return _bare(args[1]) if len(args) == 2 and args[0] is str else None


def _bare(annotation: object) -> object:
    """``annotation`` without an ``Annotated`` wrapper."""
    return get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation


def key(model: type[BaseModel]) -> SettingKey:
    """A typed reference to a model's field: ``key(HelloSettings).greeting``.

    A settings field names a setting; a record's field names a table column,
    ``Output(columns=(key(Repo).full_name,))``.
    """
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"key() takes a model class, not {model!r}")
    return SettingKey(model)
