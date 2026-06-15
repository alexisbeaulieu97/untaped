"""Application use cases for the profile bounded context.

Each class depends only on the narrowest port it needs (see
:mod:`untaped.profile.ports`); none of them know about YAML, files, or the
resolver. Command-specific wording in raised errors is intentionally
minimal — the CLI layer owns command-aware hints.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from untaped.errors import ConfigError
from untaped.profile.models import Profile, ProfileDeletePreview
from untaped.profile.ports import ActiveProfileWriter, ProfileReader, ProfileWriter
from untaped.profile_resolver import DEFAULT_PROFILE, ProfileSource


class CreateProfile:
    """Create ``profiles.<name>``.

    ``copy_from`` seeds the new profile from another existing one (handy
    for cloning ``default`` into a new environment). The copy is a deep
    copy — later edits to the source must not affect the new profile.
    """

    def __init__(self, repo: ProfileWriter) -> None:
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
        self._repo.write(name, data)


class DeleteProfile:
    """Remove ``profiles.<name>``.

    Refuses to delete the active profile (would orphan the ``active:``
    pointer). ``default`` is not special-cased — when it's not the
    active profile, deleting it just clears any shared overrides and
    values fall through to schema defaults.
    """

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def preview(self, name: str) -> ProfileDeletePreview:
        data = self._repo.read(name)
        if data is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        if self._repo.persisted_active_name() == name:
            raise ConfigError(
                f"cannot delete the active profile {name!r}; switch to another profile first"
            )
        return ProfileDeletePreview(name=name, top_level_keys=tuple(sorted(data)))

    def __call__(self, name: str) -> None:
        self.preview(name)
        self._repo.delete(name)


class ListProfiles:
    """Return one :class:`Profile` per stored profile."""

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(self) -> list[Profile]:
        active = self._repo.active_name() or DEFAULT_PROFILE
        out: list[Profile] = []
        for name in self._repo.names():
            data = self._repo.read(name) or {}
            out.append(Profile(name=name, data=data, is_active=(name == active)))
        return out


class ShowProfile:
    """Return the named profile.

    By default the data is the **effective view**: ``default`` merged with
    the named profile (named wins per leaf). ``raw=True`` returns only
    what's literally written under ``profiles.<name>``.
    """

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(
        self,
        name: str,
        *,
        raw: bool = False,
        allow_conceptual_default: bool = False,
    ) -> Profile:
        raw_data = self._repo.read(name)
        if raw_data is None and name == DEFAULT_PROFILE and allow_conceptual_default:
            data = {} if raw else self._repo.resolved(name)
            active = self._repo.active_name() or DEFAULT_PROFILE
            return Profile(name=name, data=data, is_active=(name == active))
        if raw_data is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        data = raw_data if raw else self._repo.resolved(name)
        active = self._repo.active_name() or DEFAULT_PROFILE
        return Profile(name=name, data=data, is_active=(name == active))


class RenameProfile:
    """Rename ``profiles.<old>`` to ``profiles.<new>``.

    ``default`` cannot be renamed nor used as the rename target. If the
    profile being renamed is the active one, ``active:`` is updated in
    the same operation so the pointer stays valid.
    """

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def __call__(self, old_name: str, new_name: str) -> None:
        if not new_name:
            raise ConfigError("new profile name cannot be empty")
        if old_name == DEFAULT_PROFILE:
            raise ConfigError("cannot rename the `default` profile")
        if new_name == DEFAULT_PROFILE:
            raise ConfigError("cannot rename to `default` (reserved name)")
        if self._repo.read(old_name) is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {old_name!r} does not exist. Known: {known}")
        if self._repo.read(new_name) is not None:
            raise ConfigError(f"profile {new_name!r} already exists")
        self._repo.rename(old_name, new_name)


@dataclass(frozen=True, slots=True)
class CurrentProfileResult:
    name: str
    source: ProfileSource


class CurrentProfile:
    """Resolve the effective active profile, classifying its source.

    Powers ``profile current`` — a one-line answer to "which profile am I
    using right now?". When env or config explicitly names a profile, the
    use case validates that the profile actually exists, so the documented
    pipe usage ``--profile $(… profile current)`` can't silently print a
    typo'd name that other commands then reject.
    """

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(self) -> CurrentProfileResult:
        name, source = self._repo.classify_active()
        if source in ("env", "config"):
            assert name is not None  # invariant of classify_active
            if name not in self._repo.names():
                known = ", ".join(sorted(self._repo.names())) or "(none)"
                raise ConfigError(
                    f"active profile {name!r} (from {source}) is not defined; known: {known}"
                )
            return CurrentProfileResult(name=name, source=source)
        # Fallback: nothing explicitly names a profile. Report the
        # conceptual `default` placeholder regardless of whether a
        # `default` profile exists on disk — schema defaults are in
        # effect either way, and that case is not a user typo to
        # protect against.
        return CurrentProfileResult(name=DEFAULT_PROFILE, source="fallback")


class UseProfile:
    """Validate the named profile exists, then persist ``active: <name>``."""

    def __init__(self, repo: ActiveProfileWriter) -> None:
        self._repo = repo

    def __call__(self, name: str) -> None:
        if self._repo.read(name) is None:
            known = ", ".join(sorted(self._repo.names())) or "(none)"
            raise ConfigError(f"profile {name!r} does not exist. Known: {known}")
        self._repo.set_active(name)


__all__ = [
    "CreateProfile",
    "CurrentProfile",
    "CurrentProfileResult",
    "DeleteProfile",
    "ListProfiles",
    "RenameProfile",
    "ShowProfile",
    "UseProfile",
]
