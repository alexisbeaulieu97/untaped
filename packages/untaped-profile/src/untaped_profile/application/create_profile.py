"""Use case: create a new profile, optionally seeded from another."""

from __future__ import annotations

import copy

from untaped_core import ConfigError
from untaped_core.profile_resolver import DEFAULT_PROFILE

from untaped_profile.application.ports import ProfileRepository


class CreateProfile:
    """Create ``profiles.<name>``.

    ``copy_from`` seeds the new profile from another existing one (handy
    for cloning ``default`` into a new environment). The copy is a deep
    copy — later edits to the source must not affect the new profile.
    """

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self, name: str, *, copy_from: str | None = None) -> None:
        if not name:
            raise ConfigError("profile name cannot be empty")
        if self._repo.read(name) is not None:
            raise ConfigError(f"profile {name!r} already exists")
        if copy_from is not None:
            source = self._repo.read(copy_from)
            if source is None:
                known = ", ".join(sorted(self._repo.names())) or "(none)"
                raise ConfigError(
                    f"cannot copy from {copy_from!r}: profile does not exist. Known: {known}"
                )
            data = copy.deepcopy(source)
        else:
            data = {}
        # Bootstrap an empty `default` so the resolver invariant
        # "non-empty profiles ⇒ default exists" holds. Mirrors the
        # auto-bootstrap that `untaped config set` already performs.
        if name != DEFAULT_PROFILE and self._repo.read(DEFAULT_PROFILE) is None:
            self._repo.write(DEFAULT_PROFILE, {})
        self._repo.write(name, data)
