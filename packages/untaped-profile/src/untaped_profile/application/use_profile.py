"""Use case: persist the active profile."""

from __future__ import annotations

from untaped_core import ConfigError

from untaped_profile.application.ports import ProfileRepository


class UseProfile:
    """Validate the named profile exists, then persist ``active: <name>``."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self, name: str) -> None:
        if self._repo.read(name) is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        self._repo.set_active(name)
