"""Application-layer protocols (ports) for the profile bounded context.

Three Protocols are layered along two axes: read vs. write, and "writes
profile data" vs. "writes the active-profile pointer". The parallel
sibling shape (``ProfileWriter`` and ``ActiveProfileWriter`` both
extending ``ProfileReader``) lets each use case depend on the narrowest
surface it needs.
"""

from __future__ import annotations

from typing import Any, Protocol

from untaped.profile_resolver import ProfileSource


class ProfileReader(Protocol):
    """Read-side surface used by ``ListProfiles`` / ``ShowProfile`` / ``CurrentProfile``."""

    def names(self) -> list[str]: ...
    def active_name(self) -> str | None: ...
    def persisted_active_name(self) -> str | None: ...
    def classify_active(self) -> tuple[str | None, ProfileSource]: ...
    def read(self, name: str) -> dict[str, Any] | None: ...
    def resolved(self, name: str) -> dict[str, Any]: ...


class ProfileWriter(ProfileReader, Protocol):
    """Profile-data writes used by ``CreateProfile`` / ``DeleteProfile`` / ``RenameProfile``."""

    def write(self, name: str, data: dict[str, Any]) -> None: ...
    def delete(self, name: str) -> bool: ...
    def rename(self, old: str, new: str) -> None: ...


class ActiveProfileWriter(ProfileReader, Protocol):
    """Rewrites the ``active:`` pointer; used by ``UseProfile``."""

    def set_active(self, name: str) -> None: ...


__all__ = [
    "ActiveProfileWriter",
    "ProfileReader",
    "ProfileWriter",
]
