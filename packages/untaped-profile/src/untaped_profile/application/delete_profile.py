"""Use case: delete a profile (refusing destructive cases)."""

from __future__ import annotations

from untaped_core import ConfigError

from untaped_profile.application.ports import ProfileRepository

DEFAULT_PROFILE = "default"


class DeleteProfile:
    """Remove ``profiles.<name>``.

    Refuses two cases that would leave the user in a broken state:
    deleting ``default`` (the required fallback layer) and deleting the
    currently active profile (would orphan the ``active:`` pointer).
    """

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self, name: str) -> None:
        if name == DEFAULT_PROFILE:
            raise ConfigError(
                "cannot delete the `default` profile; it's the required fallback layer"
            )
        if self._repo.read(name) is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        if (self._repo.active_name() or DEFAULT_PROFILE) == name:
            raise ConfigError(
                f"cannot delete the active profile {name!r}; "
                "switch to another profile first with `untaped profile use`"
            )
        self._repo.delete(name)
