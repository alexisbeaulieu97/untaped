"""The component contract (:class:`Field`) and :func:`field_for`, which maps a setting to one.

A component is a frozen dataclass: its constructor is its ``init``,
``update(message)`` returns the next component and any commands, and
``view(frame, focused=..., width=...)`` draws it. ``update`` hands back the same
object for a message it does not consume; that identity is how the runtime
knows whether a key was handled, so a component never returns an equal copy
for a key it ignores. Focus belongs to the parent (a form, a tab strip, the
screen), which passes it to ``view``.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Any, Literal, NamedTuple, Protocol, Self, get_args, get_origin

from pydantic import SecretStr
from rich.console import RenderableType

from untaped.config_schema import FieldDescriptor, annotated_metadata
from untaped.screen.components.choices import Check, Cycle, ListItem, Select, SingleList
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.screen.core import Cmd, Frame
from untaped.stability import experimental

__all__ = ["Field", "field_for"]

#: A literal with this many choices or fewer is drawn as a list, with more as a ``Select``.
LIST_CHOICES = 4


@experimental
class Field(Protocol):
    """What every component offers a parent that holds it."""

    @property
    def value(self) -> object:
        """What the component currently holds (a string, a number, a ``SecretStr``, ...)."""
        ...

    @property
    def error(self) -> str:
        """The error text it shows (empty when none)."""
        ...

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """The component after ``message``: ``self`` itself when it consumed nothing."""
        ...

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The component drawn ``width`` cells wide (the frame's width by default)."""
        ...

    def with_error(self, text: str) -> Self:
        """The component showing ``text`` as its error (empty clears it)."""
        ...

    def validate(self) -> str:
        """The error text for what it holds now (empty when it is fine); never sets it."""
        ...


def field_for(descriptor: FieldDescriptor, *, value: object = None, help: str = "") -> Field:
    """The component that edits the setting ``descriptor`` describes.

    ``Literal[...]`` of strings is a ``SingleList`` (four choices or fewer) or a
    ``Select``, ``bool`` a ``Check`` (an optional ``bool | None`` a three-state
    ``Cycle``: unset, on, off, holding ``None``, ``True`` or ``False``), ``int``
    and ``float`` a ``NumberInput``, a path type a ``PathInput``, ``SecretStr``
    a ``SecretInput`` and ``str`` a ``TextInput``. A number's bounds come from
    the descriptor's ``metadata`` (``Ge``/``Le``/``Gt``/``Lt``, also those of an
    ``Annotated`` type); an optional number may be left empty (unset, ``None``),
    a required one may not. ``value`` is the starting value (the setting's
    default when ``None``) and ``help`` its help line (the setting's
    ``description`` when empty). The label is the setting's key. Any other type
    raises ``TypeError`` naming the key, so a setting nobody mapped is an error
    instead of a silently missing field.
    """
    base, extras = _unannotated(descriptor.annotation)
    metadata = (*descriptor.metadata, *extras)
    help = help or descriptor.description or ""
    initial = (
        value if value is not None else (descriptor.default if descriptor.has_default else None)
    )
    label = descriptor.key
    if get_origin(base) is Literal:
        choices = get_args(base)
        if not all(isinstance(choice, str) for choice in choices):
            raise TypeError(
                f"no component for setting {descriptor.key!r}: only Literal strings are supported, "
                f"not {descriptor.annotation!r}"
            )
        items = tuple(ListItem(choice, choice) for choice in choices)
        current = initial if isinstance(initial, str) and initial in choices else ""
        if len(items) <= LIST_CHOICES:
            return SingleList(label, items, current, help=help)
        return Select(label, items, current, help=help)
    if base is bool:
        if descriptor.optional:
            return Cycle(
                label,
                (None, True, False),
                initial if isinstance(initial, bool) else None,
                labels=_TRISTATE_LABELS,
                help=help,
            )
        return Check(label, initial is True, help=help)
    if base is int or base is float:
        bounds = _bounds(metadata, integer=base is int)
        return NumberInput(
            label,
            text=_number_text(initial),
            minimum=bounds.minimum,
            maximum=bounds.maximum,
            above=bounds.above,
            below=bounds.below,
            integer=base is int,
            required=not descriptor.optional,
            help=help,
        )
    if isinstance(base, type) and issubclass(base, PurePath):
        return PathInput(label, "" if initial is None else str(initial), help=help)
    if base is SecretStr:
        secret = initial if isinstance(initial, SecretStr) else SecretStr(str(initial or ""))
        return SecretInput(label, secret, help=help)
    if base is str:
        return TextInput(label, "" if initial is None else str(initial), help=help)
    raise TypeError(
        f"no component for setting {descriptor.key!r}: unsupported type {descriptor.annotation!r}"
    )


#: The words of the three states of an optional ``bool`` (``None``, ``True``, ``False``).
_TRISTATE_LABELS = ("unset", "on", "off")


def _unannotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """The type under ``Annotated[...]`` and its metadata (pydantic ``Field`` constraints too)."""
    if get_origin(annotation) is not Annotated:
        return annotation, ()
    return get_args(annotation)[0], annotated_metadata(annotation)


class _Bounds(NamedTuple):
    minimum: float | None = None
    maximum: float | None = None
    above: float | None = None
    below: float | None = None


def _bounds(metadata: tuple[Any, ...], *, integer: bool) -> _Bounds:
    """The limits the ``ge``/``gt``/``le``/``lt`` metadata set, the tightest of each kind.

    An integer's exclusive bound is the next integer (``gt=0`` is a minimum of 1);
    a float keeps it exclusive (``above``/``below``), since no inclusive bound
    says it.
    """
    minimum: float | None = None
    maximum: float | None = None
    above: float | None = None
    below: float | None = None
    for item in metadata:
        if (ge := getattr(item, "ge", None)) is not None:
            minimum = ge if minimum is None else max(minimum, ge)
        if (gt := getattr(item, "gt", None)) is not None:
            if integer:
                minimum = gt + 1 if minimum is None else max(minimum, gt + 1)
            else:
                above = gt if above is None else max(above, gt)
        if (le := getattr(item, "le", None)) is not None:
            maximum = le if maximum is None else min(maximum, le)
        if (lt := getattr(item, "lt", None)) is not None:
            if integer:
                maximum = lt - 1 if maximum is None else min(maximum, lt - 1)
            else:
                below = lt if below is None else min(below, lt)
    return _Bounds(minimum, maximum, above, below)


def _number_text(value: object) -> str:
    """The text a number starts as; a float is its ``repr``, so a no-op edit keeps its value."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return str(value) if isinstance(value, int) else repr(value)
