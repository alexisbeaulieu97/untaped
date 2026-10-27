"""The wire models: the kind machinery records and diagnostics share, and the stderr kinds.

:class:`WireModel` is the one base for everything untaped writes as JSON with
a ``kind``: stdout records (``untaped.sdk.records.Record``) and stderr diagnostics
(:class:`Diagnostic`). A subclass declares its kind as a class keyword
(``class Thing(Message, kind="toy.thing")``), checked against the kind grammar
and registered in one registry, so ``untaped plugin schema <kind>`` prints a
record's or a diagnostic's schema alike; every subclass is checked at
definition for fields that would not survive a JSON round trip.

Every stderr line is a :class:`Diagnostic`:

- :class:`Message` (``untaped.message``): an ``info``, ``success``,
  ``warning`` or ``hint`` line, the one kind whose level varies;
- :class:`Failure` (``untaped.failure``): an ``error``, counted toward the
  exit code; also the ``error`` of a failed row;
- :class:`Debug` (``untaped.debug``): a log record under ``--verbose``;
- :class:`StaleData` (``untaped.stale``): a ``warning`` that cached data was
  served stale, and why;
- :class:`DeprecationNotice` (``untaped.deprecation``): a ``warning`` that a
  deprecated thing was used, and the major that removes it.

``level`` is a class constant on every fixed-level kind (only ``Message``
carries it as a field), so a ``Failure`` nested in a row dumps its payload
only. Plugins add fields, never levels.

Also here: :func:`attribute`, the one classifier of any exception, and the
error rendering (:func:`format_error`, :func:`error_message`).
"""

from __future__ import annotations

import annotationlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from functools import cache
from types import MappingProxyType
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    ForwardRef,
    Literal,
    Self,
    TypeAliasType,
    evaluate_forward_ref,
    get_args,
    get_origin,
)

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    Secret,
    SecretBytes,
    SecretStr,
    SerializerFunctionWrapHandler,
    WrapSerializer,
    model_serializer,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from untaped.runtime.redaction import redact_url_password
from untaped.sdk._run import is_verbose
from untaped.sdk.errors import ErrorCategory, FixBy, HttpError, UntapedError, fix_by
from untaped.sdk.words import ago_text, duration_text

# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def _to_utc(value: datetime) -> datetime:
    """Normalize to an aware UTC datetime (naive values are taken as UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Render ``value`` as RFC 3339 UTC with a ``Z`` suffix, keeping any microseconds.

    Tables cut the fraction when they show it (``untaped.runtime.render``).
    """
    return _to_utc(value).isoformat().replace("+00:00", "Z")


def parse_utc(value: object) -> datetime | None:
    """An ISO 8601 time as aware UTC (naive taken as UTC), or ``None`` for anything else."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return _to_utc(value) if isinstance(value, datetime) else None


UtcTimestamp = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]
"""A UTC ``datetime`` field that renders as ``2026-01-02T03:04:05Z`` in output.

Every format but a table keeps the microseconds
(``2026-01-02T03:04:05.000678Z``), so it reads back unchanged; tables show
it to the second. Name such fields ``<event>_at`` (``created_at``,
``scanned_at``)."""

#: Serializers known to round-trip, allowed on wire-model fields.
_ROUND_TRIP_SERIALIZERS: Final = frozenset({format_utc})

# --------------------------------------------------------------------------- #
# Kinds
# --------------------------------------------------------------------------- #

#: The kind grammar: two or three lowercase dot-separated segments. The first
#: is the declaring plugin's name verbatim, hyphens included (``untaped`` for
#: core); the others are snake_case.
KIND_PATTERN: Final = re.compile(
    r"^[a-z][a-z0-9]*(-[a-z0-9]+)*(\.[a-z][a-z0-9]*(_[a-z0-9]+)*){1,2}$"
)


def check_data_kind(kind: str) -> None:
    """Reject a kind that is not ``<plugin>.<noun>`` or ``<plugin>.<noun>.summary``.

    ``.summary`` is the only third segment a data kind may have: pipe
    consumers that skip informational records rely on it. Raises
    ``ValueError``: a bad kind is a programming error that must surface at
    development time.
    """
    segments = kind.split(".")
    if (
        not KIND_PATTERN.match(kind)
        or segments[1] == "summary"
        or (len(segments) == 3 and segments[2] != "summary")
    ):
        raise ValueError(
            f"invalid record kind {kind!r}: expected '<plugin>.<noun>' with the plugin's "
            "name verbatim and a snake_case noun, optionally followed by '.summary', e.g. "
            "'github.code_hit', 'acme-tools.widget' or 'awx.apply_outcome.summary'"
        )


class KindError(ValueError):
    """A kind declaration composition refuses; ``reason`` is its quarantine reason."""

    reason: ClassVar[str]


class DuplicateKindError(KindError):
    """Two models declare the same kind (reason ``duplicate-kind``)."""

    reason = "duplicate-kind"


class ForeignKindError(KindError):
    """A model declares a kind under another plugin's name (reason ``foreign-kind``)."""

    reason = "foreign-kind"


_MODELS: dict[str, type[WireModel]] = {}
_KINDS: dict[type[BaseModel], str] = {}


def kind_of(model: type[BaseModel]) -> str | None:
    """The kind ``model`` declares (``None``: a kind-less model, such as the record bases).

    A parametrized generic record (``Page[int]``) has its generic's kind.
    """
    origin = model.__pydantic_generic_metadata__["origin"]
    return _KINDS.get(model) or (None if origin is None else _KINDS.get(origin))


def record_model(kind: str) -> type[WireModel] | None:
    """The record or diagnostic model that declares ``kind`` (``None``: no model declares it)."""
    return _MODELS.get(kind)


def record_kinds() -> Mapping[str, type[WireModel]]:
    """Every declared kind, records and diagnostics, and its model, read-only."""
    return MappingProxyType(_MODELS)


def _register(kind: str, model: type[WireModel]) -> None:
    """Map ``kind`` to ``model``; another model already declaring it is ``duplicate-kind``.

    The same class defined again (a module imported twice) replaces itself.
    A plugin's model declaring a kind under another name is ``foreign-kind``.
    """
    held = _MODELS.get(kind)
    if held is not None and (held.__module__, held.__qualname__) != (
        model.__module__,
        model.__qualname__,
    ):
        raise DuplicateKindError(
            f"kind {kind!r} is declared by both "
            f"{held.__module__}.{held.__qualname__} and {model.__module__}.{model.__qualname__}; "
            "each kind has exactly one schema, so give one of them another kind"
        )
    _check_owner(kind, model)
    _MODELS[kind] = model
    _KINDS[model] = kind


def _declaring_plugin(module: str) -> str | None:
    """The plugin whose import package holds ``module`` (``None``: no plugin's).

    ``untaped_acme_tools`` is ``acme-tools`` and ``untaped`` is core: plugin
    names have no ``_``, so the mapping is unambiguous.
    """
    package = module.partition(".")[0]
    if package == "untaped":
        return "untaped"
    if package.startswith("untaped_"):
        return package.removeprefix("untaped_").replace("_", "-")
    return None


def _check_owner(kind: str, model: type[WireModel]) -> None:
    """A plugin's model declares kinds under its own name only (``foreign-kind``).

    The plugin is read from the model's import package, since a plugin's
    name and its package are one name. A model outside ``untaped`` and
    ``untaped_*`` packages (a test module) is not checked.

    The plugin is quarantined only when composing it registers the model:
    a model in a module that only a command body imports registers when
    that command runs, so a foreign or duplicate kind there is a traceback
    instead. Declare models in a module the spec or app factory imports.
    """
    owner = _declaring_plugin(model.__module__)
    prefix = kind.partition(".")[0]
    if owner is None or prefix == owner:
        return
    noun = kind.partition(".")[2]
    declarer = "core" if owner == "untaped" else f"plugin {owner!r}"
    # The package can be what is misnamed: a plugin's package is its name read with ``_``.
    package = "untaped" if prefix == "untaped" else f"untaped_{prefix.replace('-', '_')}"
    raise ForeignKindError(
        f"{model.__module__}.{model.__qualname__} declares kind {kind!r}, but its package "
        f"belongs to {declarer}; a kind starts with its declaring plugin's name, "
        f"so name it {owner}.{noun}, or ship it in the package {package}"
    )


def dumped_names(model: type[BaseModel]) -> dict[str, str]:
    """Each field's key in ``model``'s dump: its serialization alias where it dumps by alias."""
    by_alias = bool(model.model_config.get("serialize_by_alias"))
    return {
        name: (info.serialization_alias if by_alias else None) or name
        for name, info in model.model_fields.items()
    }


@cache
def _field_order(model: type[WireModel]) -> tuple[str, ...]:
    """Dumped keys by declaring class, most derived first, then the model's own reordering.

    Pydantic lists inherited fields first; wire models want their own fields
    (including re-declared base fields) ahead of the base-class ones. An
    ``extra="allow"`` model keeps pydantic's base-first order: a service's
    answer leads with the identity its base types (``id``, ``name``), then
    the fields a subclass adds.
    """
    order: dict[str, None] = {}
    declared_by: dict[str, type] = {}
    for klass in model.__mro__:
        own = annotationlib.get_annotations(klass, format=annotationlib.Format.FORWARDREF)
        for name in own:
            if name in model.model_fields:
                order.setdefault(name)
                declared_by.setdefault(name, klass)
    if model.model_config.get("extra") == "allow":
        order = dict.fromkeys(model.model_fields)
    keys = dumped_names(model)
    return tuple(keys[name] for name in model._reorder(list(order), declared_by))


def _is_none(value: object) -> bool:
    return value is None


# --------------------------------------------------------------------------- #
# The field-type ban
# --------------------------------------------------------------------------- #


def _lossy(where: str, why: str) -> TypeError:
    return TypeError(
        f"{where}: {why}. A record's or diagnostic's fields must survive a JSON round trip "
        "(pipes, state.yml, caches and stderr readers read them back), so this would lose data"
    )


def _check_round_trip(model: type[BaseModel], seen: set[Any], where: str | None = None) -> None:
    """Reject the field features that silently lose data in a JSON round trip.

    Covers ``model`` and every model nested in its fields: an unresolved
    forward reference (nothing could check it), secrets (``SecretStr``,
    ``SecretBytes``, ``Secret``), ``Field(exclude=True)``, an ``exclude_if``
    other than "omit when ``None``", a key the dump uses that validation does
    not accept, a computed field (``extra="forbid"`` rejects it on read),
    ``field_serializer``, ``model_serializer`` or
    ``PlainSerializer``/``WrapSerializer`` other than core's own, and, with
    ``extra="allow"``, extras not typed ``dict[str, JsonValue]``. ``where``
    names the field a nested model sits in.
    """
    if model in seen:
        return
    seen.add(model)
    prefix = where or model.__qualname__
    if not model.__pydantic_fields_complete__:
        raise _lossy(prefix, "it refers to a type not defined yet, so its fields can't be checked")
    decorators = model.__pydantic_decorators__
    if serializers := decorators.field_serializers:
        fields = sorted({field for each in serializers.values() for field in each.info.fields})
        raise _lossy(prefix, f"field_serializer on {', '.join(fields)} may not invert")
    if any(each.func is not _ORDERED for each in decorators.model_serializers.values()):
        raise _lossy(prefix, "a model_serializer may not invert")
    if computed := model.model_computed_fields:
        raise _lossy(prefix, f"computed field {', '.join(sorted(computed))} does not read back")
    config = model.model_config
    if config.get("extra") == "allow" and _extras_type(model) != dict[str, JsonValue]:
        raise _lossy(
            prefix,
            'extra="allow" keeps fields no type checks; declare '
            "__pydantic_extra__: dict[str, JsonValue] = Field(init=False) so they are JSON values",
        )
    by_alias = bool(config.get("serialize_by_alias"))
    by_name = bool(config.get("validate_by_name") or config.get("populate_by_name"))
    reads_alias = config.get("validate_by_alias") is not False
    for name, info in model.model_fields.items():
        field = f"{prefix}.{name}"
        _check_field(field, name, info, by_alias=by_alias, by_name=by_name, reads_alias=reads_alias)
        for meta in info.metadata:
            _check_serializer(field, meta)
        _check_type(field, info.annotation, seen)


def _extras_type(model: type[BaseModel]) -> object:
    """The type ``model`` declares for its extras (``None``: none, or one that can't resolve)."""
    for klass in model.__mro__:
        own = annotationlib.get_annotations(klass, format=annotationlib.Format.FORWARDREF)
        if "__pydantic_extra__" not in own:
            continue
        declared = own["__pydantic_extra__"]
        if isinstance(declared, str):  # ``from __future__ import annotations``
            declared = ForwardRef(declared, module=klass.__module__)
        if isinstance(declared, ForwardRef):
            try:
                return evaluate_forward_ref(declared, owner=klass)
            except NameError:
                return None
        return declared
    return None


def _check_field(
    where: str, name: str, info: FieldInfo, *, by_alias: bool, by_name: bool, reads_alias: bool
) -> None:
    if info.exclude is True:
        raise _lossy(where, "Field(exclude=True) leaves it out of every dump")
    if info.exclude_if is not None and (
        info.exclude_if is not _is_none or info.get_default(call_default_factory=True) is not None
    ):
        # Only "omit when None, which is the default" provably reads back the same.
        raise _lossy(where, "exclude_if may only omit a None default")
    # ``emit`` dumps by field name unless the model serializes by alias; a
    # serialization alias must read back too, for a dump that uses it.
    alias = info.serialization_alias
    dumped = [(alias if by_alias else None) or name, *([alias] if alias else [])]
    accepted = _accepted_keys(name, info, by_name=by_name, by_alias=reads_alias)
    for key in dumped:
        if key not in accepted:
            raise _lossy(where, f"dumps as {key!r}, which validation does not accept")


def _accepted_keys(name: str, info: FieldInfo, *, by_name: bool, by_alias: bool) -> set[str]:
    alias = info.validation_alias
    if not by_alias:
        return {name} if by_name or alias is None else set()
    if alias is None:
        return {name}
    accepted = {name} if by_name else set()
    if isinstance(alias, str):
        return accepted | {alias}
    choices = getattr(alias, "choices", ())
    return accepted | {choice for choice in choices if isinstance(choice, str)}


def _check_serializer(where: str, meta: object) -> None:
    if isinstance(meta, PlainSerializer | WrapSerializer) and (
        meta.func not in _ROUND_TRIP_SERIALIZERS
    ):
        raise _lossy(where, f"{type(meta).__name__} {meta.func.__name__} may not invert")


def _check_type(where: str, annotation: object, seen: set[Any]) -> None:
    if isinstance(annotation, TypeAliasType):
        if annotation not in seen:  # a recursive alias (``JsonValue``) is checked once
            seen.add(annotation)
            _check_type(where, annotation.__value__, seen)
        return
    origin = get_origin(annotation)
    if origin is Literal:
        return
    if origin is Annotated:
        for meta in getattr(annotation, "__metadata__", ()):
            _check_serializer(where, meta)
    target = origin if isinstance(origin, type) else annotation
    if isinstance(target, type):
        if issubclass(target, SecretStr | SecretBytes | Secret):
            raise _lossy(where, f"{target.__name__} dumps masked")
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            _check_round_trip(annotation, seen, where)
            return
    for arg in get_args(annotation):
        _check_type(where, arg, seen)


class WireModel(BaseModel):
    """Base for what untaped writes as JSON with a kind: frozen, unknown fields rejected.

    Dumps list the model's own fields before inherited ones. A subclass
    declares its kind with a class keyword (``kind="gitlab.project"``),
    checked against the kind grammar (:func:`check_data_kind`) and registered
    kind → model; a second model declaring the same kind raises
    :class:`DuplicateKindError`. A subclass without ``kind=`` is kind-less,
    not its parent's kind. Every subclass, with every model nested in it, is
    checked for what would lose data in a JSON round trip (a kind-less
    diagnostic only where a record or a kind nests it): secrets
    (``SecretStr``, ``Secret``), ``Field(exclude=True)``, an ``exclude_if``
    other than core's omit-when-``None``, an alias validation does not accept,
    computed fields, custom serializers and forward references not yet
    defined. The class definition raises ``TypeError`` naming the field.

    A record passing a service's answer through sets ``extra="allow"``: it
    types the fields untaped reads and keeps every other one, typed
    ``__pydantic_extra__: dict[str, JsonValue]``. Its dump is the row it
    was given, its typed fields first (base classes' first, so the identity
    leads): a declared field the row left out stays out.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    def __init_subclass__(cls, *, kind: str | None = None, **kwargs: Any) -> None:
        if kind is not None:
            check_data_kind(kind)
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(cls, *, kind: str | None = None, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if kind is not None or cls._checked_without_kind():
            _check_round_trip(cls, set())
        if kind is not None:
            _register(kind, cls)

    @classmethod
    def _checked_without_kind(cls) -> bool:
        """Whether a kind-less subclass is checked at definition (records: yes)."""
        return True

    @classmethod
    def _reorder(cls, names: list[str], declared_by: Mapping[str, type]) -> list[str]:
        """Hook: adjust the own-fields-first order (``Record`` moves ``action``)."""
        del declared_by
        return names

    # No return annotation: pydantic then documents the fields in the
    # serialization JSON schema (``-> Any`` would turn it into ``{}``).
    @model_serializer(mode="wrap")
    def _own_fields_first(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        data = handler(self)
        if not isinstance(data, dict):
            return data
        if type(self).model_config.get("extra") == "allow":
            data = _given(self, data)
        ordered = {name: data[name] for name in _field_order(type(self)) if name in data}
        return ordered | data


def _given(model: WireModel, data: dict[str, Any]) -> dict[str, Any]:
    """An ``extra="allow"`` row's dump less the declared fields it was never given.

    So dumping a validated answer writes back the fields it had: an absent
    field stays absent rather than becoming ``null``.
    """
    names = dumped_names(type(model))
    given = {names.get(name, name) for name in model.model_fields_set}
    return {key: value for key, value in data.items() if key in given}


#: The base's own serializer (field order), the one ``model_serializer`` allowed.
_ORDERED: Final = WireModel.__pydantic_decorators__.model_serializers["_own_fields_first"].func

# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #

Level = Literal["error", "warning", "hint", "info", "success", "debug", "progress"]
"""A stderr line's ``level``: the filter key, written beside ``kind``."""

#: An optional field left out of the dump when unset.
_Omitted = Annotated[str | None, Field(exclude_if=_is_none)]


class Diagnostic(WireModel):
    """One stderr line: never a ``Record``, never on stdout.

    ``message`` is the human line without its ``level:`` prefix; ``hint``
    the "what next" line, without its ``hint:`` prefix. ``level`` is a class
    constant on every fixed-level kind, written beside ``kind``; an
    intermediate base with no ``kind=`` is never a line by itself.
    """

    level: ClassVar[Level]
    message: str
    hint: _Omitted = None

    @classmethod
    def _checked_without_kind(cls) -> bool:
        # A kind-less diagnostic is never a line by itself (``emit`` refuses it):
        # it is checked where a record or a kind nests it.
        return False


class Message(Diagnostic, kind="untaped.message"):
    """An ``info``, ``success``, ``warning`` or ``hint`` line."""

    level: Literal["info", "warning", "hint", "success"]  # type: ignore[misc]


def _jsonable(details: Mapping[str, object]) -> dict[str, JsonValue]:
    """``details`` as JSON values: anything else becomes its string, never an error."""
    converted = to_jsonable_python(dict(details), fallback=str)
    return converted if isinstance(converted, dict) else {}


class Failure(Diagnostic, kind="untaped.failure"):
    """Why something failed: a counted ``error`` line, and a failed row's ``error``.

    ``category`` selects the exit code; ``system`` names who is responsible;
    ``retryable`` and ``exit_code`` follow from ``category`` (filled when
    absent, rejected when they disagree); ``item`` names a per-item failure;
    ``details`` holds JSON values only. :meth:`from_exception` builds one
    without emitting it, for a failed row's ``error=``.
    """

    level: ClassVar[Level] = "error"

    item: Annotated[_Omitted, Field(description="The item that failed, on a per-item failure.")] = (
        None
    )
    category: ErrorCategory = Field(
        description="What kind of failure it is; it selects `exit_code` and `retryable`."
    )
    system: str = Field(
        description=(
            "Who is responsible: `untaped` (a bug or the command line), `local` (config, "
            "files, the environment), `git`, `http`, or a service such as `awx`, which a "
            "plugin may refine (`awx.scm`)."
        )
    )
    # ``retryable`` and ``exit_code`` defaults are never used: both are filled
    # from ``category`` before validation; they only keep them optional to build.
    retryable: bool = Field(
        default=False, description="`true` only for an `unavailable` failure: retry later."
    )
    # Re-declared to keep the wire order: message before hint.
    message: str = Field(description="The failure, in one line, as a person reads it.")
    hint: str | None = Field(
        default=None, description="What to do next, such as a command to run, or `null`."
    )
    exit_code: int = Field(default=1, description="The exit code this failure alone selects.")
    details: dict[str, JsonValue] = Field(
        default={},
        description="Machine context, such as `status`, `url` and `attempts` (`{}` when none).",
    )
    fix_by: Annotated[FixBy | None, Field(exclude_if=_is_none)] = Field(
        default=None,
        description=(
            "`user` when only the person can take the hint's step (a login, a secret "
            "variable): an agent reports it and does not run it; absent otherwise."
        ),
    )

    @classmethod
    def _reorder(cls, names: list[str], declared_by: Mapping[str, type]) -> list[str]:
        """A subclass's own fields (a case's ``evidence``) follow the failure's."""
        del declared_by
        common = [name for name in names if name in Failure.model_fields]
        return [*common, *(name for name in names if name not in Failure.model_fields)]

    @model_validator(mode="before")
    @classmethod
    def _fill_from_category(cls, data: Any) -> Any:
        """Fill ``retryable`` and ``exit_code`` from ``category``, which alone selects them."""
        if not isinstance(data, dict) or "category" not in data:
            return data
        try:
            category = ErrorCategory(data["category"])
        except ValueError:
            return data
        filled = dict(data)
        filled.setdefault("retryable", category.retryable)
        filled.setdefault("exit_code", int(category.exit_code))
        return filled

    @model_validator(mode="after")
    def _agrees_with_category(self) -> Self:
        if self.retryable != self.category.retryable:
            raise ValueError(
                f"retryable={self.retryable} disagrees with category {self.category.value!r}"
            )
        if self.exit_code != self.category.exit_code:
            raise ValueError(
                f"exit_code={self.exit_code} disagrees with category {self.category.value!r} "
                f"(exit code {int(self.category.exit_code)})"
            )
        return self

    @classmethod
    def from_exception(cls, error: BaseException, *, message: str | None = None) -> Failure:
        """The attribution of ``error``, rendered as :func:`format_error` does.

        ``message`` replaces the rendered message (e.g. a redacted one); a
        ``hint:`` line in it becomes ``hint``. An error's ``details`` are
        copied as JSON values (anything else as its string), so building one
        never fails.
        """
        text, hint = error_message(error, message=message)
        category, system = attribute(error)
        details = error.details if isinstance(error, UntapedError) else {}
        return cls(
            category=category,
            system=system,
            message=text,
            hint=hint,
            details=_jsonable(details),
            fix_by=fix_by(error),
        )

    @classmethod
    def plain(cls, text: str, *, hint: str | None = None) -> Failure:
        """A plain ``error:`` line: category ``failed``, system ``untaped``."""
        return cls(category=ErrorCategory.FAILED, system="untaped", message=text, hint=hint)

    def to_exception(self) -> UntapedError:
        """This failure as an error to raise again, with its attribution, hint and details."""
        return UntapedError(
            self.message,
            category=self.category,
            system=self.system,
            hint=self.hint,
            details=self.details,
            fix_by=self.fix_by,
        )


class _NotCounted:
    """The marker type of :data:`NOT_COUNTED`."""

    def __repr__(self) -> str:
        return "NOT_COUNTED"


NOT_COUNTED: Final = _NotCounted()
"""``Annotated`` metadata on a row's ``Failure`` field: a remembered failure, never a failed row.

A row counts toward the exit code through every top-level ``Failure`` it
holds, except one marked with this (a cache's last refresh error on a
status row, a test case's failure that the run reports itself).
"""


class Debug(Diagnostic, kind="untaped.debug"):
    """A log record under ``--verbose``: ``logger`` names who logged it."""

    level: ClassVar[Level] = "debug"

    logger: str


class Progress(Diagnostic, kind="untaped.progress"):
    """A step of a long operation, at the sink's cadence: never counted, muted by ``--quiet``.

    The sink builds every one from the task a ``progress(label)`` block
    opened; a plugin reports through the handle and never constructs one.
    ``message`` is the line a human reads, composed the same way for every
    task: ``<label>[ · <phase>][ <done>/<total>][ · <text>] (<N>s)`` while
    running, ``<label>: done[ · <text>] (<N>s)`` or ``<label>: failed[ ·
    <text>] (<N>s)`` at the end, each bracket only when its part is set (a
    ``done`` without a ``total`` prints alone). A task opened inside
    another keeps the outer ``label`` and puts its own in ``phase``
    (``<inner label>[ · <inner phase>]``). ``elapsed`` is seconds since
    the task opened.
    """

    level: ClassVar[Level] = "progress"

    label: str
    phase: str | None
    done: int | None
    total: int | None
    elapsed: float
    state: Literal["running", "done", "failed"]


class StaleData(Diagnostic, kind="untaped.stale"):
    """Cached data served past its freshness, and why: a ``warning``, never counted.

    ``plugin`` names whose data it is: the plugin that declared the cache
    (``Cache.plugin``), or for the answer cache the provider whose answer
    was served. ``cache`` names the cache (``<plugin>.<name>``;
    ``<owner>.<contract>_<method>`` for the answer cache), ``key`` the entry
    in it. ``error`` is why the refresh
    failed or what is missing (``None`` under ``--cached``); it never counts
    toward the exit code. ``outdated``: the data was made by another version
    of the code that fills the cache (``Cache(version=)``), whatever its age.
    :meth:`describe` builds one with its wording.
    """

    level: ClassVar[Level] = "warning"

    plugin: str
    cache: str
    key: str
    refreshed_at: UtcTimestamp
    age_seconds: float
    max_age_seconds: int
    partial: bool = False
    outdated: bool = False
    attempted_at: UtcTimestamp | None = None
    retry_at: UtcTimestamp | None = None
    error: Failure | None = None

    @classmethod
    def describe(
        cls,
        *,
        plugin: str,
        subject: str,
        cache: str,
        key: str,
        refreshed_at: datetime,
        now: datetime,
        max_age: timedelta,
        attempted_at: datetime | None = None,
        retry_at: datetime | None = None,
        error: Failure | None = None,
        partial: bool = False,
        outdated: bool = False,
        hint: str | None = None,
        cached: bool = False,
    ) -> StaleData:
        """A stale line about ``subject`` (``source prod``, a provider), worded for its case.

        A failed refresh (``error``, not ``partial``): ``refresh failed
        (<cause>)``, or ``refresh interrupted`` for an interrupted one; a
        partial fill (``partial``): the error's message; ``--cached`` (no
        ``error``): ``cached data from … (--cached)``, or ``made by another
        version of the <plugin> plugin; using data from …`` when
        ``outdated``, ending ``(--cached)`` only when ``cached`` (auto meets
        outdated data when another version filled it during a lock wait).
        Each says when the data is from and how old; ``retry_at`` adds when
        auto checks again.
        ``cached``: the reader passed ``--cached``, so a remembered ``error``
        line ends ``(--cached)`` too and says no next check.
        """
        age = now - refreshed_at
        outdated_by = plugin if outdated else None
        cause = _stale_cause(error, attempted_at, now, partial=partial, outdated_by=outdated_by)
        message = f"{subject}: {cause}"
        if error is None:
            message += f"{_utc_minute(refreshed_at)} UTC, {duration_text(age.total_seconds())} old"
            if cached or not outdated:  # outdated data reaches auto when another version filled it
                message += " (--cached)"
        else:
            message += f"; using data from {_utc_minute(refreshed_at)} UTC"
            if not partial:
                message += f", {duration_text(age.total_seconds())} old"
            if cached:
                message += " (--cached)"
        if retry_at is not None and not cached:
            message += f"; next check after {_to_utc(retry_at):%H:%M} UTC"
        return cls(
            message=message,
            hint=hint,
            plugin=plugin,
            cache=cache,
            key=key,
            refreshed_at=refreshed_at,
            age_seconds=age.total_seconds(),
            max_age_seconds=int(max_age.total_seconds()),
            partial=partial,
            outdated=outdated,
            attempted_at=attempted_at,
            retry_at=retry_at,
            error=error,
        )


class Pruned(Diagnostic, kind="untaped.pruned"):
    """A store layout nothing holds, removed after a complete fill: an ``info`` line, unmuted.

    ``plugin`` and ``store`` name the store, ``layout`` the removed layout's
    number (built by :meth:`untaped.sdk.storage.store.Store.prune`). ``path``
    is where it was, ``bytes`` what it freed (its sidecars and its caches'
    folders included).
    """

    level: ClassVar[Level] = "info"

    plugin: str
    store: str
    layout: int
    path: str
    bytes: int


class DeprecationNotice(Diagnostic, kind="untaped.deprecation"):
    """A deprecated thing was used: a ``warning``, never counted; the sentence is ``message``.

    ``id`` is its surface identity (``cli untaped awx put``,
    ``cfg awx.base_url -> awx.server.url``;
    a setting's line adds its type and default after it), so a harness can
    allow-list exactly what it has reviewed. ``removed_in`` is the major that
    removes it (``None`` when its mark names none); ``distribution`` whose
    major that is (``None`` for core's own, the plugin's distribution
    otherwise); ``replacement`` what to use instead. Built by
    :func:`untaped.sdk.marks.notify_deprecation`.
    """

    level: ClassVar[Level] = "warning"

    id: str
    removed_in: int | None
    distribution: str | None
    replacement: str | None
    hint: str | None = None
    message: str


def _utc_minute(value: datetime) -> str:
    return f"{_to_utc(value):%Y-%m-%d %H:%M}"


def _stale_cause(
    error: Failure | None,
    attempted_at: datetime | None,
    now: datetime,
    *,
    partial: bool,
    outdated_by: str | None,
) -> str:
    """What happened to the refresh, as the start of a stale line.

    ``outdated_by``: the plugin whose other version made the data, when it did.
    """
    if error is None and outdated_by is not None:
        return f"made by another version of the {outdated_by} plugin; using data from "
    if error is None:
        return "cached data from "
    ago = (
        f" {ago_text(attempted_at, now)}"
        if attempted_at is not None and now - attempted_at >= timedelta(minutes=1)
        else ""
    )
    if partial:
        return f"{error.message}{ago}"
    if error.category is ErrorCategory.INTERRUPTED:
        return f"refresh interrupted{ago}"
    return f"refresh failed{ago} ({error.message})"


# --------------------------------------------------------------------------- #
# Attribution and rendering
# --------------------------------------------------------------------------- #

_HINT_PREFIX = "hint: "
_USER_HINT_PREFIX = "hint (your step): "


def attribute(error: BaseException) -> tuple[ErrorCategory, str]:
    """``(category, system)`` of any exception: the one place that classifies them."""
    if isinstance(error, UntapedError):
        return error.category, error.system
    if isinstance(error, KeyboardInterrupt):
        return ErrorCategory.INTERRUPTED, "untaped"
    return ErrorCategory.FAILED, "untaped"


def format_error(exc: UntapedError) -> str:
    """Render an :class:`UntapedError` the way ``report_errors`` prints it.

    Adds the URL to bodiless HTTP errors and surfaces the API's own message
    from a JSON error body (the raw body under ``--verbose``); an error whose
    message already describes its body (``describes_body``) adds the raw body
    under ``--verbose`` only. A ``hint`` not already in the message follows
    on its own ``hint:`` line, ``hint (your step):`` when only the person
    can take it (``fix_by``). URL passwords are masked.
    """
    message = _with_body(exc)
    if exc.hint and exc.hint not in message:
        prefix = _USER_HINT_PREFIX if fix_by(exc) == "user" else _HINT_PREFIX
        message = f"{message}\n{prefix}{exc.hint}"
    return redact_url_password(message)


def _with_body(exc: UntapedError) -> str:
    message = str(exc)
    if not isinstance(exc, HttpError):
        return message
    if not exc.body:
        if exc.url and exc.url not in message:
            message = f"{message} for {exc.url}"
        return message
    if exc.describes_body:
        return f"{message}\nresponse: {exc.body}" if is_verbose() else message
    friendly = _api_error_message(exc.body)
    if friendly is None:
        # Unparseable / unrecognised body — show it raw so detail isn't lost.
        return f"{message}\nresponse: {exc.body}"
    if is_verbose():
        return f"{message} — {friendly}\nresponse: {exc.body}"
    return f"{message} — {friendly}"


def _api_error_message(body: str) -> str | None:
    """Pull a human message out of a JSON error body, if present.

    Recognises the shapes most JSON APIs use — a top-level
    ``message``/``error``/``detail`` string or ``errors: [{"message": ...}]``
    (GitHub, AWX, DRF, ...) — and returns the first match. Returns ``None`` for
    a non-JSON body or an unrecognised shape so the caller falls back to the raw
    snippet.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("message", "error", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    errors = data.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                nested = item.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def split_hint(text: str) -> tuple[str, str | None]:
    """Split ``hint: …`` lines off ``text``: ``(message, hint)`` (hint without its prefix)."""
    kept: list[str] = []
    hints: list[str] = []
    for line in text.split("\n"):
        prefix = next((p for p in (_HINT_PREFIX, _USER_HINT_PREFIX) if line.startswith(p)), None)
        if prefix is not None:
            hints.append(line.removeprefix(prefix))
        else:
            kept.append(line)
    return "\n".join(kept).strip(), "\n".join(hints) or None


def error_message(error: BaseException, *, message: str | None = None) -> tuple[str, str | None]:
    """``(message, hint)`` for ``error``: its rendering, or ``message``, with hints split off."""
    if message is None:
        message = format_error(error) if isinstance(error, UntapedError) else _plain(error)
    text, hint = split_hint(redact_url_password(message))
    if hint is None and isinstance(error, UntapedError):
        hint = error.hint
    return text, hint


def _plain(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = [
    "KIND_PATTERN",
    "NOT_COUNTED",
    "Debug",
    "DeprecationNotice",
    "Diagnostic",
    "DuplicateKindError",
    "Failure",
    "ForeignKindError",
    "KindError",
    "Level",
    "Message",
    "Pruned",
    "StaleData",
    "UtcTimestamp",
    "WireModel",
    "attribute",
    "check_data_kind",
    "dumped_names",
    "error_message",
    "format_error",
    "format_utc",
    "kind_of",
    "record_kinds",
    "record_model",
    "split_hint",
]
