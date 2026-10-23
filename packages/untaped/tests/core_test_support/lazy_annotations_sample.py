"""Deprecated parameters in a module Python evaluates lazily: no future import."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from untaped.sdk.marks import deprecated, deprecated_parameters

if TYPE_CHECKING:
    from untaped.sdk import Record  # never imported at run time


@deprecated_parameters
def hinted(x: Annotated[int, deprecated(removed_in=12)] = 0, y: Record | None = None) -> int:
    """A marked parameter beside a hint only type checkers import."""
    return x


@deprecated_parameters
def unresolved(x: Annotated[int, deprecated(removed_in=12, replacement=helper)] = 0) -> int:  # type: ignore[name-defined]  # noqa: F821
    """A mark whose replacement names nothing the module defines."""
    return x


@deprecated_parameters
def extra(**kwargs: Annotated[int, deprecated(removed_in=12)]) -> int:
    """A marked ``**kwargs``."""
    return len(kwargs)


@deprecated_parameters
def element(x: list[Annotated[int, deprecated(removed_in=12)]]) -> int:
    """A mark on a list's element type, not on the parameter."""
    return len(x)


@dataclass
class Settings:
    """A dataclass field marked deprecated: its generated __init__ can't warn."""

    flag: Annotated[bool, deprecated(removed_in=12)] = False


__all__ = ["Settings", "element", "extra", "hinted", "unresolved"]
