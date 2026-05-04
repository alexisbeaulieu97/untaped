"""Use case: delete a profile (refusing destructive cases)."""

from __future__ import annotations

from untaped_core import ConfigError
from untaped_core.profile_resolver import DEFAULT_PROFILE

from untaped_profile.application.ports import ProfileRepository


class DeleteProfile:
    """Remove ``profiles.<name>``.

    Refuses to delete the active profile (would orphan the ``active:``
    pointer). ``default`` is not special-cased — when it's not the
    active profile, deleting it just clears any shared overrides and
    values fall through to schema defaults.
    """

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self, name: str) -> None:
        if self._repo.read(name) is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        if (self._repo.persisted_active_name() or DEFAULT_PROFILE) == name:
            raise ConfigError(
                f"cannot delete the active profile {name!r}; "
                "switch to another profile first with `untaped profile use`"
            )
        self._repo.delete(name)
