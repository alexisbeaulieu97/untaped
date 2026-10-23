"""An SDK module for the walker tests; loaded as ``untaped_sample`` so its classes count as ours."""

from __future__ import annotations

import enum
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, Protocol

from pydantic import BaseModel

from untaped.sdk import UntapedError
from untaped.sdk.marks import deprecated, deprecated_parameters, experimental

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "NO_LIMIT",
    "VERSION",
    "Boxed",
    "Color",
    "Fill",
    "Format",
    "Name",
    "Point",
    "Record",
    "Refused",
    "Settings",
    "Shout",
    "Teller",
    "Widget",
    "fill",
    "fill_later",
    "gather",
    "hook",
    "legacy",
    "listing",
    "marked",
    "place",
    "preview",
    "watch",
    "watch_bare",
]

VERSION = "1"
NO_LIMIT = object()


class Color(enum.Enum):
    RED = "red"
    BLUE = 2


Format = Literal["json", "table"]

type Name = Annotated[str, "a name"]


class Shout(Protocol):
    def __call__(self, text: str, loud: bool) -> None: ...


class Teller(Protocol):
    def tell(self, text: str) -> None: ...


def fill(
    source: Callable[[str, int], None], items: Sequence[str], *, limit: object = NO_LIMIT
) -> None:
    """A required callback, a TYPE_CHECKING name and a default naming an export."""


def fill_later(items: list[str], each: Shout | None = None) -> None:
    """An optional callback through a Protocol."""


def gather[**P](method: Callable[P, object], /, *args: P.args, **kwargs: P.kwargs) -> None:
    """A ParamSpec callback, positional-only, with *args and **kwargs."""


def listing(rows: dict[str, Callable[..., object]], *, color: Color = Color.RED) -> None:
    """A callable inside a container counts for its owner."""


def place(where: str, at: int = 0, *, near: str, flags: list[str] | None = None) -> None:
    """Required keyword-only after an optional positional."""


def hook(name: str, run: Callable, size: int = 0, /) -> None:  # type: ignore[type-arg]
    """A bare Callable, and an optional positional-only parameter."""


@experimental
def preview(count: int = 3) -> None:
    """Experimental: every line carries the mark."""


@warnings.deprecated("use fill")
def legacy() -> None:
    """Deprecated through warnings.deprecated."""


def watch(callback: "Undefined[[int], None]") -> None:  # type: ignore[name-defined]  # noqa: F821, UP037
    """Its callback annotation can't be resolved, quoted or not."""


def watch_bare(callback: Undefined[[int], None]) -> None:  # type: ignore[name-defined]  # noqa: F821
    """The same, unquoted."""


class Fill(Exception):
    """Exception's own methods never become lines."""

    category: ClassVar[str] = "fill"
    hint = "try again"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class Record(BaseModel):
    """A pydantic model: its fields only, never BaseModel's API."""

    table_columns: ClassVar[tuple[str, ...]] = ()

    name: str
    tags: list[str] = field(default_factory=list)  # type: ignore[assignment]
    count: int = 0

    def __init_subclass__(cls, *, kind: str = "", **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

    @property
    def title(self) -> str:
        return self.name.title()

    def show(self, *, wide: bool = False) -> str:
        return self.name


@dataclass(frozen=True)
class Point:
    x: int
    y: int = 0
    labels: list[str] = field(default_factory=list)
    made: list[str] = field(default_factory=lambda: ["a"])

    def moved(self, dx: int, /, dy: int = 0) -> Point:
        return Point(self.x + dx, self.y + dy)


@dataclass
class Settings:
    path: str = "~/.untaped"


class Widget:
    """A plain class: constructor, a static method, a class method and a slot."""

    __slots__ = ("size",)

    def __init__(self, size: int, on_change: Callable[[int], None] | None = None) -> None:
        self.size = size

    @staticmethod
    def default() -> Widget:
        return Widget(1)

    @classmethod
    def sized(cls, size: int) -> Widget:
        return cls(size)

    @experimental
    def grow(self) -> None:
        self.size += 1


class Refused(UntapedError):
    """A plugin's error: only what it adds is its own."""

    category = "refused"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def why(self) -> str:
        return self.reason


@deprecated_parameters
def marked(
    x: int, *, old: Annotated[bool, deprecated(removed_in=12, replacement="x")] | None = None
) -> None:
    """A parameter marked inside a union."""


class Boxed:
    """A constructor parameter marked deprecated."""

    @deprecated_parameters
    def __init__(self, old: Annotated[bool, deprecated(removed_in=12)] = False) -> None:
        self.old = old
