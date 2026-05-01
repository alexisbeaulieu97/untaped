"""Use case: rename a profile, keeping ``active:`` consistent."""

from __future__ import annotations

from untaped_core import ConfigError
from untaped_core.profile_resolver import DEFAULT_PROFILE

from untaped_profile.application.ports import ProfileRepository


class RenameProfile:
    """Rename ``profiles.<old>`` to ``profiles.<new>``.

    ``default`` cannot be renamed nor used as the rename target. If the
    profile being renamed is the active one, ``active:`` is updated in
    the same operation so the pointer stays valid.
    """

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self, old_name: str, new_name: str) -> None:
        if not new_name:
            raise ConfigError("new profile name cannot be empty")
        if old_name == DEFAULT_PROFILE:
            raise ConfigError("cannot rename the `default` profile")
        if new_name == DEFAULT_PROFILE:
            raise ConfigError("cannot rename to `default` (reserved name)")
        source = self._repo.read(old_name)
        if source is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {old_name!r} does not exist. Known: {known}")
        if self._repo.read(new_name) is not None:
            raise ConfigError(f"profile {new_name!r} already exists")
        self._repo.write(new_name, source)
        self._repo.delete(old_name)
        if self._repo.persisted_active_name() == old_name:
            self._repo.set_active(new_name)
