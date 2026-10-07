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
from typing import Annotated, Any, Literal, Protocol, Self, get_args, get_origin

from pydantic import SecretStr
from rich.console import RenderableType

from untaped.config_schema import FieldDescriptor
from untaped.screen.components.choices import Check, ListItem, Select, SingleList
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


@experimental
def field_for(descriptor: FieldDescriptor, *, value: object = None, help: str = "") -> Field:
    """The component that edits the setting ``descriptor`` describes.

    ``Literal[...]`` of strings is a ``SingleList`` (four choices or fewer) or a
    ``Select``, ``bool`` a ``Check``, ``int`` and ``float`` a ``NumberInput`` (the
    bounds come from ``Annotated`` metadata such as ``Ge``/``Le`` when the
    annotation carries it), a path type a ``PathInput``, ``SecretStr`` a
    ``SecretInput`` and ``str`` a ``TextInput``. ``value`` is the starting
    value (the setting's default when ``None``) and ``help`` its help line,
    since a descriptor has no description. The label is the setting's key.
    Any other type raises ``TypeError`` naming the key, so a setting nobody
    mapped is an error instead of a silently missing field.
    """
    base, metadata = _unannotated(descriptor.annotation)
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
        return Check(label, initial is True, help=help)
    if base is int or base is float:
        minimum, maximum = _bounds(metadata, integer=base is int)
        return NumberInput(
            label,
            text=_number_text(initial),
            minimum=minimum,
            maximum=maximum,
            integer=base is int,
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


def _unannotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """The type under ``Annotated[...]`` and its metadata (pydantic ``Field`` constraints too)."""
    if get_origin(annotation) is not Annotated:
        return annotation, ()
    base, *extras = get_args(annotation)
    metadata: list[Any] = []
    for extra in extras:
        metadata.append(extra)
        metadata.extend(getattr(extra, "metadata", ()))
    return base, tuple(metadata)


def _bounds(metadata: tuple[Any, ...], *, integer: bool) -> tuple[float | None, float | None]:
    """The inclusive minimum and maximum the ``ge``/``gt``/``le``/``lt`` metadata set.

    An exclusive bound counts only for an integer (``gt=0`` is a minimum of 1); a
    float's exclusive bound cannot be written as an inclusive one, so it is left
    to the setting's own validation.
    """
    minimum: float | None = None
    maximum: float | None = None
    for item in metadata:
        if (ge := getattr(item, "ge", None)) is not None:
            minimum = ge if minimum is None else max(minimum, ge)
        if (gt := getattr(item, "gt", None)) is not None and integer:
            minimum = gt + 1 if minimum is None else max(minimum, gt + 1)
        if (le := getattr(item, "le", None)) is not None:
            maximum = le if maximum is None else min(maximum, le)
        if (lt := getattr(item, "lt", None)) is not None and integer:
            maximum = lt - 1 if maximum is None else min(maximum, lt - 1)
    return minimum, maximum


def _number_text(value: object) -> str:
    """The text a number starts as; a float is its ``repr``, so a no-op edit keeps its value."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return str(value) if isinstance(value, int) else repr(value)
