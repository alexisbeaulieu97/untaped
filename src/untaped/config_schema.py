"""Walk a Pydantic ``Settings`` model into a flat list of leaf descriptors.

Used by the root ``untaped config list/set/unset`` commands to enumerate
what's configurable without hard-coding the schema. Lists, dicts, and other
collection types are skipped unless ``include_collections`` asks for them as
whole-value leaves (the ``config`` commands do, e.g. ``ui.symbols``).
"""

from __future__ import annotations

import copy
import re
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
        """Dotted key, e.g. ``"http.verify_ssl"``."""
        return ".".join(self.path)

    @property
    def is_collection(self) -> bool:
        """Whether the leaf holds a whole mapping or list (e.g. ``ui.symbols``)."""
        return _is_collection(self.annotation)


def walk_settings(
    model_cls: type[BaseModel],
    _prefix: tuple[str, ...] = (),
    *,
    include_collections: bool = False,
) -> list[FieldDescriptor]:
    """Return every leaf scalar field of ``model_cls``, recursing into nested models.

    ``include_collections`` also returns list/dict fields as single leaves.
    """
    entries: list[FieldDescriptor] = []
    for name, field in model_cls.model_fields.items():
        annotation = _unwrap_optional(field.annotation)
        path = (*_prefix, name)

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            entries.extend(walk_settings(annotation, path, include_collections=include_collections))
            continue

        if _is_collection(annotation) and not include_collections:
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


_URL_PASSWORD = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*://[^/@:\s]*):[^/@\s]*@")


def redact_url_password(value: str, *, placeholder: str = "***") -> str:
    """Mask the password in a ``scheme://user:password@host`` URL.

    Settings such as ``http.proxy`` are plain strings that may still carry
    credentials; the user name stays visible so the value remains
    recognizable. Anything that is not such a URL is returned unchanged.
    """
    return _URL_PASSWORD.sub(rf"\g<prefix>:{placeholder}@", value, count=1)


def redact_nested_url_passwords(value: Any, *, placeholder: str = "***") -> Any:
    """Copy of ``value`` with :func:`redact_url_password` applied to every string."""
    return _redact_url_passwords(copy.deepcopy(value), placeholder)


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
    Passwords embedded in URL strings anywhere in ``data`` are masked too
    (see :func:`redact_url_password`).
    """
    out: dict[str, Any] = _redact_url_passwords(copy.deepcopy(dict(data)), placeholder)
    for path in paths:
        _redact_path(out, path, placeholder)
    return out


def _redact_url_passwords(value: Any, placeholder: str) -> Any:
    if isinstance(value, str):
        return redact_url_password(value, placeholder=placeholder)
    if isinstance(value, dict):
        return {key: _redact_url_passwords(item, placeholder) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_url_passwords(item, placeholder) for item in value]
    return value


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
