"""Application-layer protocols (ports) for the profile bounded context.

Two Protocols split read from write, so each use case depends on the
narrowest surface it needs.
"""

from __future__ import annotations

from typing import Any, Protocol

from untaped.runtime.profile_resolver import Picked


class ProfileReader(Protocol):
    """Read-side surface used by ``ListProfiles`` / ``ShowProfile`` / ``CurrentProfile``."""

    def names(self) -> list[str]: ...
    def pick(self) -> tuple[Picked, ...]:
        """The run's pick; a profile it names that isn't defined is a usage error."""
        ...

    def read(self, name: str) -> dict[str, Any] | None: ...
    def resolved(self, name: str | None) -> dict[str, Any]: ...


class ProfileWriter(ProfileReader, Protocol):
    """Profile-data writes used by ``CreateProfile`` / ``DeleteProfile`` / ``RenameProfile``."""

    def write(self, name: str, data: dict[str, Any]) -> None: ...
    def delete(self, name: str) -> bool: ...
    def rename(self, old: str, new: str) -> None: ...


__all__ = [
    "ProfileReader",
    "ProfileWriter",
]
