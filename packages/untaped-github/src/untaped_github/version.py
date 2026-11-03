"""Which GitHub a server is: github.com, or a GitHub Enterprise Server release.

The version type of github's server API (:mod:`untaped_github.server`). Its
text is how people read and set it (``github.server_version``):
``github.com``, or ``GitHub Enterprise Server 3.14.2``. It lives apart from
the adapter so the settings can type the override.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from untaped.sdk.network import Release, ServerVersion

#: How GitHub Enterprise Server's versions read, before the release.
_GHES = "GitHub Enterprise Server "


class Product(StrEnum):
    """The GitHub a server runs."""

    #: github.com, and the hosted API roots that read the same (GHE.com).
    DOTCOM = "github.com"
    #: GitHub Enterprise Server, upgraded on its owner's schedule.
    GHES = "ghes"


@dataclass(frozen=True)
class GithubVersion(ServerVersion):
    """Which GitHub a server is: ``github.com``, or ``GitHub Enterprise Server 3.14.2``.

    github.com has no version, and GHE.com and other hosted API roots read
    the same.
    """

    product: Product
    #: The GHES release; github.com has none.
    release: Release | None = None

    def __post_init__(self) -> None:
        if (self.release is None) != (self.product is Product.DOTCOM):
            raise ValueError("GitHub Enterprise Server has a release; github.com has none")

    def __str__(self) -> str:
        if self.product is Product.DOTCOM:
            return Product.DOTCOM.value
        return f"{_GHES}{self.release}"

    @classmethod
    def parse(cls, text: str) -> Self:
        """``github.com``, or ``GitHub Enterprise Server`` and a dotted release."""
        spelled = text.strip()
        if spelled == Product.DOTCOM.value:
            return cls(Product.DOTCOM)
        if spelled.startswith(_GHES):
            return cls(Product.GHES, Release.parse(spelled.removeprefix(_GHES)))
        raise ValueError(
            "a GitHub server version reads 'github.com' or "
            f"'GitHub Enterprise Server 3.14.2', not {text!r}"
        )


__all__ = ["GithubVersion", "Product"]
