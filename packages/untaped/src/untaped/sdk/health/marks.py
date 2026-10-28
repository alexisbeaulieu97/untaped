"""Setting marks: what a plugin's settings need before it can be used.

A plugin marks fields and declares credentials, and nothing else; core owns
what follows (``setup``, ``setup plan``, doctor's ``setup`` row, ``auth``):

- :data:`NeedsSetup` on an ``X | None`` field whose default is ``None``
  (a :class:`~untaped.sdk.network.credentials.CredentialKey` model included): the plugin
  can't be used until the field has a value. Nested models' fields count
  too, by their dotted path.
- each :class:`~untaped.sdk.network.credentials.Credential` in ``PluginSpec(credentials=)``
  is a secret the plugin needs: set once :meth:`~untaped.sdk.network.credentials.Credential.answer`
  names a source. It is no setting: ``untaped auth login`` stores it.

The marks don't change the JSON schema: pydantic ignores them. Every way a
mark can fail to take effect is a sentence of :func:`setup_mark_errors`, and
composition quarantines the plugin with it.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from untaped.sdk._schema import unwrap_optional

if TYPE_CHECKING:
    from untaped.sdk.network.credentials import Credential


@dataclass(frozen=True)
class _NeedsSetup:
    """The ``NeedsSetup`` mark's type: one instance, compared by type."""

    def __repr__(self) -> str:
        return "NeedsSetup"


NeedsSetup = _NeedsSetup()
"""``Annotated`` mark: the plugin can't be used until this ``X | None = None`` field is set."""


@dataclass(frozen=True)
class Suggest:
    """A command that prints a secret, which ``setup plan`` offers when its program is on PATH.

    ``argv`` runs without a shell; ``note`` is its prerequisite (``after `gh
    auth login```).
    """

    argv: tuple[str, ...]
    note: str = ""

    def __post_init__(self) -> None:
        argv = self.argv
        if not isinstance(argv, tuple) or not argv or not all(isinstance(a, str) for a in argv):
            raise TypeError(f"Suggest takes a non-empty tuple of strings, not {argv!r}")
        if not argv[0].strip():
            raise TypeError("Suggest's program must not be blank")


@dataclass(frozen=True)
class MarkedField:
    """One thing a plugin needs set up: a marked setting, or a declared credential."""

    path: tuple[str, ...]
    """Its path within the section: ``("server",)``, ``("sweep", "org")``; a credential's name."""
    credential: Credential[Any] | None = None
    """The declared credential this stands for (then ``path`` is its name, not a setting)."""
    key_model: type[BaseModel] | None = None
    """The :class:`~untaped.sdk.network.credentials.CredentialKey` a marked setting holds."""
    needs_setup: bool = True
    """Whether it must be set before the plugin can be used."""
    masked: bool = False
    """Whether its value is a ``SecretStr``: a secret setting, whose value is never shown."""

    @property
    def value_name(self) -> str:
        """The ``<NAME>`` placeholder of a command that sets it: ``<WORKSPACE_ID>``."""
        return f"<{self.path[-1].upper()}>"

    @property
    def key(self) -> str:
        """Its dotted key within the section: ``server.url``; a credential's name."""
        return ".".join(self.path)

    @property
    def is_secret(self) -> bool:
        """Whether it is a secret: a declared credential."""
        return self.credential is not None

    @property
    def env(self) -> tuple[str, ...]:
        """A secret's conventional variables, in order."""
        return self.credential.env if self.credential is not None else ()


@dataclass(frozen=True)
class SetupMarks:
    """What a section model marks, and the credentials its plugin declares."""

    fields: tuple[MarkedField, ...]

    @property
    def required(self) -> tuple[MarkedField, ...]:
        """The fields that need setting up (``NeedsSetup``), in declaration order."""
        return tuple(field for field in self.fields if field.needs_setup)

    @property
    def secrets(self) -> tuple[MarkedField, ...]:
        """The secrets: each declared credential."""
        return tuple(field for field in self.fields if field.is_secret)

    @property
    def token(self) -> MarkedField | None:
        """The secret named ``token``, which the setup form's token tabs act on."""
        return next((field for field in self.secrets if field.path == ("token",)), None)

    @property
    def default_secret(self) -> MarkedField | None:
        """The secret a bare section names (``auth login <section>``).

        The one named ``token``, else the only one; ``None`` with none, or with
        several and no ``token``.
        """
        if self.token is not None:
            return self.token
        return self.secrets[0] if len(self.secrets) == 1 else None


def _marks_of(field: FieldInfo) -> list[object]:
    return [item for item in field.metadata if isinstance(item, _NeedsSetup)]


def _fields(
    model: type[BaseModel], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], FieldInfo, bool]]:
    """``(path, field, is a model)`` for every field, recursing into nested models."""
    for name, field in model.model_fields.items():
        nested = unwrap_optional(field.annotation)
        is_model = isinstance(nested, type) and issubclass(nested, BaseModel)
        yield (*prefix, name), field, is_model
        if is_model:
            yield from _fields(nested, (*prefix, name))


@cache
def declared_marks(model: type[BaseModel]) -> SetupMarks | None:
    """The fields ``model`` marks itself, or ``None`` when it marks none.

    A marked model field (a :class:`~untaped.sdk.network.credentials.CredentialKey`) is
    one field: set means not ``None``.
    """
    found: list[MarkedField] = []
    for path, field, is_model in _fields(model):
        if not _marks_of(field):
            continue
        if is_model and any(path[: len(each.path)] == each.path for each in found):
            continue
        inner = unwrap_optional(field.annotation)
        masked = inner is SecretStr
        key_model = inner if is_model and _is_key(inner) else None
        found.append(MarkedField(path, key_model=key_model, masked=masked))
    return SetupMarks(tuple(found)) if found else None


@cache
def setup_marks(
    model: type[BaseModel], credentials: tuple[Credential[Any], ...] = ()
) -> SetupMarks | None:
    """What ``model`` marks and its plugin's ``credentials``, else ``None`` for neither."""
    declared = declared_marks(model)
    secrets = tuple(MarkedField((each.name,), credential=each) for each in credentials)
    if declared is None and not secrets:
        return None
    return SetupMarks((*(declared.fields if declared else ()), *secrets))


def _is_key(model: type[BaseModel]) -> bool:
    from untaped.sdk.network.credentials import (  # noqa: PLC0415 - credentials imports marks
        CredentialKey,
    )

    return issubclass(model, CredentialKey)


def section_marks(section: str, model: type[BaseModel] | None) -> SetupMarks | None:
    """:func:`setup_marks` of ``section``'s model (if any) and its plugin's credentials."""
    from untaped.sdk.network.credentials import declared_credentials  # noqa: PLC0415

    credentials = declared_credentials(section)
    if model is None:
        return setup_marks(_NoSettings, credentials) if credentials else None
    return setup_marks(model, credentials)


class _NoSettings(BaseModel):
    """The settings of a plugin that declares credentials and no settings."""


def setup_mark_errors(model: type[BaseModel]) -> list[str]:
    """One sentence per setting mark on ``model`` that would not take effect; ``[]`` when none."""
    errors: list[str] = []
    for path, field, _is_model in _fields(model):
        key = ".".join(path)
        marks = _marks_of(field)
        if _mark_inside_type(field.annotation):
            errors.append(
                f"the setup mark on setting {key!r} sits inside its type, where pydantic "
                "drops it; annotate the field: Annotated[str | None, NeedsSetup] = None"
            )
        if not marks:
            continue
        if len(marks) > 1:
            errors.append(f"setting {key!r} repeats a setup mark")
        if not _none_by_default(field):
            errors.append(
                f"setting {key!r} is marked NeedsSetup but is not optional with a None "
                "default, so it would always look set; annotate the field: "
                "Annotated[X | None, NeedsSetup] = None"
            )
    return errors


def _none_by_default(field: FieldInfo) -> bool:
    return (
        _is_optional(field.annotation)
        and field.default_factory is None
        and field.default is not PydanticUndefined
        and field.default is None
    )


def _is_optional(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    return (origin is types.UnionType or origin is typing.Union) and type(None) in typing.get_args(
        annotation
    )


def _mark_inside_type(annotation: object) -> bool:
    """Whether a setup mark sits inside the type itself, where pydantic drops it."""
    return any(
        isinstance(arg, _NeedsSetup) or _mark_inside_type(arg)
        for arg in typing.get_args(annotation)
    )


def is_set(value: object) -> bool:
    """Whether a resolved setting counts as set: not ``None``, and not a blank string.

    ``[]`` counts as set. A ``SecretStr`` is unwrapped.
    """
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is not None and not (isinstance(value, str) and not value.strip())


__all__ = [
    "MarkedField",
    "NeedsSetup",
    "SetupMarks",
    "Suggest",
    "declared_marks",
    "is_set",
    "section_marks",
    "setup_mark_errors",
    "setup_marks",
]
