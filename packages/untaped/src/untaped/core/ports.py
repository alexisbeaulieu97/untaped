"""The structural types core reads beside its own classes."""

from __future__ import annotations

from typing import Protocol


class InstallableSkill(Protocol):
    """A packaged agent skill the install machinery can list and install: a stub.

    Structural type implemented by :class:`~untaped.runtime.registry.SkillAsset` (a frozen
    dataclass). The members are read-only properties because the concrete type
    is frozen; a plain attribute Protocol would (wrongly) demand settability.
    """

    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
