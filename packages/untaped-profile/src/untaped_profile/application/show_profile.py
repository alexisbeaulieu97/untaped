"""Use case: render a single profile's contents (effective or raw)."""

from __future__ import annotations

from untaped_core import ConfigError
from untaped_core.profile_resolver import DEFAULT_PROFILE

from untaped_profile.application.ports import ProfileRepository
from untaped_profile.domain import Profile


class ShowProfile:
    """Return the named profile.

    By default the data is the **effective view**: ``default`` merged with
    the named profile (named wins per leaf). ``raw=True`` returns only
    what's literally written under ``profiles.<name>``.
    """

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self, name: str, *, raw: bool = False) -> Profile:
        raw_data = self._repo.read(name)
        if raw_data is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        data = raw_data if raw else self._repo.resolved(name)
        active = self._repo.active_name() or DEFAULT_PROFILE
        return Profile(name=name, data=data, is_active=(name == active))
