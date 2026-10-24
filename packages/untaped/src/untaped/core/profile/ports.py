"""Application-layer protocols (ports) for the profile bounded context.

Two Protocols split read from write, so each use case depends on the
narrowest surface it needs.
"""

from __future__ import annotations

from typing import Any, Protocol

from untaped.runtime.profile_resolver import ProfileSource


class ProfileReader(Protocol):
    """Read-side surface used by ``ListProfiles`` / ``ShowProfile`` / ``CurrentProfile``."""

    def names(self) -> list[str]: ...
    def active_name(self) -> str | None: ...
    def classify_active(self) -> tuple[str | None, ProfileSource]: ...
    def read(self, name: str) -> dict[str, Any] | None: ...
    def resolved(self, name: str) -> dict[str, Any]: ...


class ProfileWriter(ProfileReader, Protocol):
    """Profile-data writes used by ``CreateProfile`` / ``DeleteProfile`` / ``RenameProfile``."""

    def write(self, name: str, data: dict[str, Any]) -> None: ...
    def delete(self, name: str) -> bool: ...
    def rename(self, old: str, new: str) -> None: ...


__all__ = [
    "ProfileReader",
    "ProfileWriter",
]
