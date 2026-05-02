"""Walk a Pydantic ``Settings`` model into a flat list of leaf descriptors.

Used by ``untaped config list/set/unset`` to enumerate what's configurable
without hard-coding the schema. Lists, dicts, and other collection types are
skipped — they are managed by domain-specific commands (e.g.
``untaped workspace add``).
"""

from __future__ import annotations

import copy
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, get_args, get_origin

from pydantic import BaseModel, SecretStr
from pydantic_core import PydanticUndefined


@dataclass(frozen=True)
class FieldDescriptor:
    """One configurable leaf in the settings model."""

    path: tuple[str, ...]
    """Dotted path components, e.g. ``("awx", "token")``."""

    annotation: type[Any]
    """Resolved Python type at the leaf (``Optional[X]`` is unwrapped to ``X``)."""

    default: Any
    """Default value if ``has_default`` is ``True``, else ``None``."""

    has_default: bool
    """Whether the model declares a default value."""

    is_secret: bool
    """``True`` for ``SecretStr`` fields — render as ``***`` unless explicitly revealed."""

    @property
    def key(self) -> str:
        """Dotted key, e.g. ``"awx.token"``."""
        return ".".join(self.path)


def walk_settings(
    model_cls: type[BaseModel],
    _prefix: tuple[str, ...] = (),
) -> list[FieldDescriptor]:
    """Return every leaf scalar field of ``model_cls``, recursing into nested models."""
    entries: list[FieldDescriptor] = []
    for name, field in model_cls.model_fields.items():
        annotation = _unwrap_optional(field.annotation)
        path = (*_prefix, name)

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            entries.extend(walk_settings(annotation, path))
            continue

        if _is_collection(annotation):
            continue

        if field.default is not PydanticUndefined:
            default: Any = field.default
            has_default = True
        elif field.default_factory is not None:
            # Pydantic accepts both zero-arg and one-arg (init values) factories;
            # for our settings schema only zero-arg factories are used.
            default = field.default_factory()  # type: ignore[call-arg]
            has_default = True
        else:
            default = None
            has_default = False

        entries.append(
            FieldDescriptor(
                path=path,
                annotation=annotation,
                default=default,
                has_default=has_default,
                is_secret=annotation is SecretStr,
            )
        )
    return entries


def find_descriptor(descriptors: list[FieldDescriptor], key: str) -> FieldDescriptor | None:
    """Return the descriptor matching the dotted ``key``, or ``None``."""
    for d in descriptors:
        if d.key == key:
            return d
    return None


def secret_field_paths(model_cls: type[BaseModel]) -> list[tuple[str, ...]]:
    """Return the dotted paths of every ``SecretStr``-typed leaf in ``model_cls``."""
    return [d.path for d in walk_settings(model_cls) if d.is_secret]


def redact_secrets(
    data: Mapping[str, Any],
    paths: Iterable[tuple[str, ...]],
    *,
    placeholder: str = "***",
) -> dict[str, Any]:
    """Deep-copy ``data`` and replace each leaf at ``paths`` with ``placeholder``.

    Paths that aren't present in ``data`` are silently skipped — profiles can
    omit any subset of the schema. ``None`` leaves are also left alone, so a
    user who has not set a secret still sees ``None`` rather than ``***``.
    """
    out: dict[str, Any] = copy.deepcopy(dict(data))
    for path in paths:
        _redact_path(out, path, placeholder)
    return out


def _redact_path(data: dict[str, Any], path: tuple[str, ...], placeholder: str) -> None:
    if not path:
        return
    cursor: Any = data
    for part in path[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    leaf = path[-1]
    if isinstance(cursor, dict) and leaf in cursor and cursor[leaf] is not None:
        cursor[leaf] = placeholder


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _is_collection(annotation: Any) -> bool:
    return get_origin(annotation) in (list, dict, set, tuple, frozenset)
