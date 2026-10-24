"""The unknown-fields rule: a write keeps what this release's model doesn't know.

A row or a data file a newer untaped wrote may hold keys this release's model
has no field for. A write lays the new dump over what was stored, so those
keys survive and an unchanged field keeps its stored spelling.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from typing import Any, Union

from pydantic import AliasChoices, BaseModel


def write_back(
    model: type[BaseModel], stored: Mapping[str, Any], known: Mapping[str, Any], new: dict[str, Any]
) -> dict[str, Any]:
    """``new`` (a dump) written over ``stored`` (a raw row whose known part dumps as ``known``).

    A field whose known part is unchanged is written exactly as stored; a
    changed nested model merges one level down by the same rule; a list keeps
    an element's stored form when its dump is unchanged at the same index.
    Keys the model doesn't know (a newer untaped's) are kept; the stored order
    is kept, new keys follow.
    """
    out: dict[str, Any] = {}
    for key, value in new.items():
        old, before = stored.get(key), known.get(key)
        if key in stored and key in known and before == value:
            out[key] = old
        elif isinstance(old, dict) and isinstance(before, dict) and isinstance(value, dict):
            nested = nested_model(model, key)
            out[key] = value if nested is None else write_back(nested, old, before, value)
        elif (
            isinstance(old, list)
            and isinstance(before, list)
            and isinstance(value, list)
            and len(old) == len(before)
        ):
            out[key] = [
                old[i] if i < len(before) and before[i] == item else item
                for i, item in enumerate(value)
            ]
        else:
            out[key] = value
    names = known_keys(model)
    for key, value in stored.items():
        if key not in names:
            out[key] = value
    order = [key for key in stored if key in out]
    order += [key for key in out if key not in stored]
    return {key: out[key] for key in order}


def known_keys(model: type[BaseModel]) -> frozenset[str]:
    """Every key a row of ``model`` may spell a field with: names and aliases."""
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        keys.add(name)
        for alias in (field.alias, field.serialization_alias):
            if alias:
                keys.add(alias)
        validation = field.validation_alias
        if isinstance(validation, str):
            keys.add(validation)
        elif isinstance(validation, AliasChoices):
            keys.update(choice for choice in validation.choices if isinstance(choice, str))
    return frozenset(keys)


def nested_model(model: type[BaseModel], key: str) -> type[BaseModel] | None:
    """The model class of ``model``'s field stored as ``key`` (``X | None`` too), if it is one."""
    for name, field in model.model_fields.items():
        if key in (name, field.alias, field.serialization_alias):
            annotation = field.annotation
            options = (
                typing.get_args(annotation)
                if typing.get_origin(annotation) in (Union, types.UnionType)
                else (annotation,)
            )
            models = [
                each for each in options if isinstance(each, type) and issubclass(each, BaseModel)
            ]
            return models[0] if len(models) == 1 else None
    return None
