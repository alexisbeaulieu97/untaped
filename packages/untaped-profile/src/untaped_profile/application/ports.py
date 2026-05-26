"""Application-layer protocols (ports) for the profile bounded context.

The four Protocols split the surface along two axes: read vs. write,
and "writes profile data" vs. "writes the active-profile pointer".
Use cases declare the narrowest port they actually need; the single
concrete adapter (``ProfileFileRepository``) satisfies every variant
structurally.
"""

from __future__ import annotations

from typing import Any, Protocol

from untaped_core import ProfileSource


class ProfileReader(Protocol):
    """Read-side surface used by ``ListProfiles`` / ``ShowProfile`` /
    ``CurrentProfile``."""

    def names(self) -> list[str]: ...
    def active_name(self) -> str | None: ...
    def persisted_active_name(self) -> str | None: ...
    def classify_active(self) -> tuple[str | None, ProfileSource]: ...
    def read(self, name: str) -> dict[str, Any] | None: ...
    def resolved(self, name: str) -> dict[str, Any]: ...


class ProfileWriter(ProfileReader, Protocol):
    """Adds profile-data mutations (``write`` / ``delete`` / ``rename``).

    Used by ``CreateProfile`` / ``DeleteProfile`` / ``RenameProfile``.
    Does **not** include ``set_active`` — that lives on
    :class:`ActiveProfileWriter` so ``UseProfile`` doesn't have to
    declare a write-data surface it never touches.
    """

    def write(self, name: str, data: dict[str, Any]) -> None: ...
    def delete(self, name: str) -> bool: ...
    def rename(self, old: str, new: str) -> None: ...


class ActiveProfileWriter(ProfileReader, Protocol):
    """Adds the ``set_active`` mutation. Used by ``UseProfile``."""

    def set_active(self, name: str) -> None: ...


class ProfileRepository(ProfileWriter, ActiveProfileWriter, Protocol):
    """The widest variant — combines both writer axes.

    Concrete adapters (``ProfileFileRepository`` plus the in-memory
    ``FakeProfileRepository`` in this package's tests) satisfy this
    Protocol structurally; no use case takes this widest variant
    directly today.
    """
