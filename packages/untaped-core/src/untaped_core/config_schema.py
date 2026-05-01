"""Walk a Pydantic ``Settings`` model into a flat list of leaf descriptors.

Used by ``untaped config list/set/unset`` to enumerate what's configurable
without hard-coding the schema. Lists, dicts, and other collection types are
skipped — they are managed by domain-specific commands (e.g.
``untaped workspace add``).
"""

from __future__ import annotations

import types
import typing
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


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _is_collection(annotation: Any) -> bool:
    return get_origin(annotation) in (list, dict, set, tuple, frozenset)
