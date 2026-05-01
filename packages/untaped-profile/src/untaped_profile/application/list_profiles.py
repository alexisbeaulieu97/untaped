"""Use case: list every profile, marking which one is active."""

from __future__ import annotations

from untaped_profile.application.ports import ProfileRepository
from untaped_profile.domain import Profile


class ListProfiles:
    """Return one :class:`Profile` per stored profile."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self) -> list[Profile]:
        active = self._repo.active_name() or "default"
        out: list[Profile] = []
        for name in self._repo.names():
            data = self._repo.read(name) or {}
            out.append(Profile(name=name, data=data, is_active=(name == active)))
        return out
