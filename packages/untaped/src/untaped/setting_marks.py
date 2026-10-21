"""Setting marks: what a plugin's settings need before it can be used, and which are secrets.

A plugin marks fields and nothing else; core owns what follows from the
marks (``setup``, ``setup plan``, doctor's ``setup`` row, ``auth``, the
token resolution):

- :data:`NeedsSetup` on an ``X | None`` field whose default is ``None``: the
  plugin can't be used until the field has a value. Nested models' fields
  count too, by their dotted path.
- :class:`Secret` on a top-level ``SecretStr | None`` field: core's secret
  block owns how it is supplied (the password store through ``auth set``, a
  command, a conventional variable). Its ``env`` replaces the
  ``token_sources`` attribute; its ``suggest`` is a command ``setup plan``
  offers when the program is on ``PATH``.

Every ``Secret`` gets a command setting, ``<field>_command`` (:data:`TokenCommand`),
injected into the composed section model (:func:`injected_commands`); a model
may still declare it itself with that type, the deprecated behaviour
:data:`SELF_DECLARED_COMMAND`. A model with no marks but ``base_url`` and
``token`` fields, or a ``token_sources`` attribute, reads as if those two
were marked: the deprecated behaviour :data:`UNMARKED_SERVICE`.

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
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from untaped.config_schema import Argv, unwrap_optional
from untaped.stability import deprecated_behaviour


def _check_argv(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not value or not value[0].strip():
        raise ValueError("token_command must be a non-empty argv list, e.g. [gh, auth, token]")
    return value


TokenCommand = Annotated[list[str] | None, Argv(), AfterValidator(_check_argv)]
"""Field type for ``<section>.token_command``: an argv list, run without a shell."""


@dataclass(frozen=True)
class TokenSources:
    """Where a settings section looks for its token when ``token`` is unset.

    ``env`` names conventional environment variables tried after
    ``token_command``, in order. A model that marks its token with
    :class:`Secret` names them there instead.
    """

    env: tuple[str, ...] = ()


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


@dataclass(frozen=True, kw_only=True)
class Secret:
    """``Annotated`` mark: a top-level ``SecretStr | None`` field whose supply core owns.

    ``env`` names conventional variables tried after the field and its
    ``<field>_command``, in order (``("GH_TOKEN", "GITHUB_TOKEN")``); they
    are ambient, not tied to a profile. ``suggest`` is a command that prints
    the secret, offered when its program is on PATH.
    """

    env: tuple[str, ...] = ()
    suggest: Suggest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.env, tuple) or not all(
            isinstance(name, str) and name.strip() for name in self.env
        ):
            raise TypeError(f"Secret(env=...) takes a tuple of variable names, not {self.env!r}")
        if self.suggest is not None and not isinstance(self.suggest, Suggest):
            raise TypeError(f"Secret(suggest=...) takes a Suggest, not {self.suggest!r}")


UNMARKED_SERVICE = deprecated_behaviour(
    "unmarked-service", removed_in=12, replacement="NeedsSetup and Secret marks on its settings"
)
"""A model with ``base_url`` and ``token`` (or ``token_sources``) but no marks, read by name."""

SELF_DECLARED_COMMAND = deprecated_behaviour(
    "self-declared-command",
    removed_in=12,
    replacement="the command setting untaped adds for each Secret",
)
"""A model declaring a ``Secret``'s ``<field>_command`` itself instead of letting core add it."""


def unmarked_subject(plugin: str) -> str:
    """How the :data:`UNMARKED_SERVICE` sentence names one plugin's fallback."""
    return f"setting up {plugin} from its base_url and token names, with no NeedsSetup marks"


def self_declared_subject(plugin: str, field: str) -> str:
    """How the :data:`SELF_DECLARED_COMMAND` sentence names one plugin's own command field."""
    return f"declaring {plugin}.{field} in the plugin's own settings model"


@dataclass(frozen=True)
class MarkedField:
    """One field a plugin marked (or the legacy fallback reads as marked)."""

    path: tuple[str, ...]
    """Its path within the section: ``("base_url",)``, ``("sweep", "org")``."""
    secret: Secret | None = None
    """Its :class:`Secret` mark; ``None`` for a field that is not a store-backed secret."""
    needs_setup: bool = True
    """Whether it must be set before the plugin can be used."""
    placeholder: str = ""
    """The ``<NAME>`` a command that sets it asks for; ``<KEY>`` (its name, upper case) when
    empty. The legacy fallback keeps ``<URL>`` for ``base_url``."""
    masked: bool = False
    """Whether its value is a ``SecretStr`` (a secret even without a :class:`Secret` mark)."""

    @property
    def value_name(self) -> str:
        """The ``<NAME>`` placeholder of a command that sets it: ``<WORKSPACE_ID>``."""
        return self.placeholder or f"<{self.path[-1].upper()}>"

    @property
    def key(self) -> str:
        """Its dotted key within the section: ``base_url``."""
        return ".".join(self.path)

    @property
    def command(self) -> str | None:
        """The command setting of a secret (``token_command`` for ``token``), else ``None``."""
        return None if self.secret is None else f"{self.path[0]}_command"


@dataclass(frozen=True)
class SetupMarks:
    """What a section model marks: its fields, and whether they come from the legacy fallback."""

    fields: tuple[MarkedField, ...]
    legacy: bool = False
    """Read from the names ``base_url`` and ``token`` (:data:`UNMARKED_SERVICE`), not marks."""

    @property
    def required(self) -> tuple[MarkedField, ...]:
        """The fields that need setting up (``NeedsSetup``), in declaration order."""
        return tuple(field for field in self.fields if field.needs_setup)

    @property
    def secrets(self) -> tuple[MarkedField, ...]:
        """The store-backed secrets, in declaration order."""
        return tuple(field for field in self.fields if field.secret is not None)

    @property
    def token(self) -> MarkedField | None:
        """The secret ``auth`` and the setup form act on: the one named ``token``."""
        return next((field for field in self.secrets if field.path == ("token",)), None)


def _marks_of(field: FieldInfo) -> list[object]:
    return [item for item in field.metadata if isinstance(item, _NeedsSetup | Secret)]


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
    """The fields ``model`` marks itself, or ``None`` when it marks none (the legacy aside)."""
    found: list[MarkedField] = []
    for path, field, is_model in _fields(model):
        marks = _marks_of(field)
        if not marks or is_model:
            continue
        secret = next((mark for mark in marks if isinstance(mark, Secret)), None)
        needs = any(isinstance(mark, _NeedsSetup) for mark in marks)
        masked = unwrap_optional(field.annotation) is SecretStr
        found.append(MarkedField(path, secret, needs_setup=needs, masked=masked))
    return SetupMarks(tuple(found)) if found else None


@cache
def setup_marks(model: type[BaseModel]) -> SetupMarks | None:
    """What ``model`` needs set up: its marks, else the legacy fallback, else ``None``.

    The fallback reads an unmarked model with ``base_url`` and ``token``
    fields, or a ``token_sources`` attribute, as if ``base_url`` (when it has
    one) and ``token`` (a secret with the declared variables) were marked.
    """
    declared = declared_marks(model)
    if declared is not None:
        return declared
    sources = getattr(model, "token_sources", None)
    names = model.model_fields
    if not ({"base_url", "token"} <= names.keys() or isinstance(sources, TokenSources)):
        return None
    env = sources.env if isinstance(sources, TokenSources) else ()
    found = [MarkedField(("base_url",), placeholder="<URL>")] if "base_url" in names else []
    if "token" in names:
        found.append(MarkedField(("token",), Secret(env=tuple(env)), masked=True))
    return SetupMarks(tuple(found), legacy=True)


def is_token_command(field: FieldInfo) -> bool:
    """Whether ``field`` is typed :data:`TokenCommand`."""
    return any(
        isinstance(item, AfterValidator) and item.func is _check_argv for item in field.metadata
    )


def injected_commands(model: type[BaseModel]) -> tuple[str, ...]:
    """The ``<field>_command`` settings core adds for ``model``'s secrets.

    One per :class:`Secret` the model marks, unless it declares that field
    itself (:data:`SELF_DECLARED_COMMAND`); the legacy fallback adds none.
    """
    marks = declared_marks(model)
    if marks is None:
        return ()
    return tuple(
        command
        for field in marks.secrets
        if (command := field.command) is not None and command not in model.model_fields
    )


def self_declared_commands(model: type[BaseModel]) -> tuple[str, ...]:
    """The ``<field>_command`` fields ``model`` declares itself beside a marked ``Secret``."""
    marks = declared_marks(model)
    if marks is None:
        return ()
    return tuple(
        command
        for field in marks.secrets
        if (command := field.command) is not None and command in model.model_fields
    )


def command_collisions(model: type[BaseModel]) -> list[str]:
    """The ``<field>_command`` fields ``model`` declares with a type other than ``TokenCommand``."""
    return [
        command
        for command in self_declared_commands(model)
        if not is_token_command(model.model_fields[command])
    ]


def setup_mark_errors(model: type[BaseModel]) -> list[str]:
    """One sentence per setting mark on ``model`` that would not take effect; ``[]`` when none."""
    errors: list[str] = []
    for path, field, is_model in _fields(model):
        key = ".".join(path)
        marks = _marks_of(field)
        if _mark_inside_type(field.annotation):
            errors.append(
                f"the setup mark on setting {key!r} sits inside its type, where pydantic "
                "drops it; annotate the field: Annotated[str | None, NeedsSetup] = None"
            )
        if not marks:
            continue
        if is_model:
            errors.append(f"setting {key!r} is a model; annotate its fields instead")
            continue
        if sum(isinstance(mark, _NeedsSetup) for mark in marks) > 1 or (
            sum(isinstance(mark, Secret) for mark in marks) > 1
        ):
            errors.append(f"setting {key!r} repeats a setup mark")
        if any(isinstance(mark, _NeedsSetup) for mark in marks) and not _none_by_default(field):
            errors.append(
                f"setting {key!r} is marked NeedsSetup but is not optional with a None "
                "default, so it would always look set; annotate the field: "
                "Annotated[X | None, NeedsSetup] = None"
            )
        if any(isinstance(mark, Secret) for mark in marks):
            if len(path) > 1:
                errors.append(
                    f"setting {key!r} is marked Secret inside a nested model; only a "
                    "top-level SecretStr | None field takes it"
                )
            elif unwrap_optional(field.annotation) is not SecretStr or not _is_optional(
                field.annotation
            ):
                errors.append(
                    f"setting {key!r} is marked Secret but is not a SecretStr | None; "
                    "annotate the field: Annotated[SecretStr | None, NeedsSetup, Secret()] = None"
                )
    errors.extend(
        f"setting {command!r} is the command setting untaped adds for a Secret; declare it "
        "as TokenCommand or leave it to untaped"
        for command in command_collisions(model)
    )
    errors.extend(_token_sources_errors(model))
    return errors


def _token_sources_errors(model: type[BaseModel]) -> list[str]:
    """A ``token_sources`` that would drift from the ``Secret`` on ``token``."""
    sources = getattr(model, "token_sources", None)
    marks = declared_marks(model)
    token = None if marks is None else marks.token
    if not isinstance(sources, TokenSources) or token is None or token.secret is None:
        return []
    if tuple(sources.env) == token.secret.env:
        return []
    return [
        f"token_sources names {', '.join(sources.env) or 'no variables'} but the Secret on "
        f"'token' names {', '.join(token.secret.env) or 'none'}; drop token_sources "
        "(Secret(env=...) replaces it) or give both the same variables"
    ]


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
        isinstance(arg, _NeedsSetup | Secret) or _mark_inside_type(arg)
        for arg in typing.get_args(annotation)
    )


def is_set(value: object) -> bool:
    """Whether a resolved setting counts as set: not ``None``, and not a blank string.

    ``[]`` counts as set. A ``SecretStr`` is unwrapped (only a :class:`Secret`
    field is asked about its source instead).
    """
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is not None and not (isinstance(value, str) and not value.strip())


__all__ = [
    "SELF_DECLARED_COMMAND",
    "UNMARKED_SERVICE",
    "MarkedField",
    "NeedsSetup",
    "Secret",
    "SetupMarks",
    "Suggest",
    "TokenCommand",
    "TokenSources",
    "command_collisions",
    "declared_marks",
    "injected_commands",
    "is_set",
    "is_token_command",
    "self_declared_commands",
    "self_declared_subject",
    "setup_mark_errors",
    "setup_marks",
    "unmarked_subject",
]
