"""Application use cases for the profile bounded context.

Each class depends only on the narrowest port it needs (see
:mod:`untaped.core.profile.ports`); none of them know about YAML, files, or the
resolver. Command-specific wording in raised errors is intentionally
minimal — the CLI layer owns command-aware hints.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from untaped.core.profile.models import Profile, ProfileDeletePreview
from untaped.core.profile.ports import ProfileReader, ProfileWriter
from untaped.runtime.profile_resolver import DEFAULT_PROFILE, ProfileSource
from untaped.sdk.errors import ConfigError
from untaped.sdk.words import not_found, q

_BASE_RESERVED = "`default` names the base, config.yml's top level; pick another name"


class CreateProfile:
    """Create ``profiles.<name>``.

    ``copy_from`` seeds the new profile from another existing one, or from
    the base (``default``). The copy is a deep copy — later edits to the
    source must not affect the new profile.
    """

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def __call__(self, name: str, *, copy_from: str | None = None, dry_run: bool = False) -> None:
        if not name:
            raise ConfigError("profile name cannot be empty", category="invalid")
        if name == DEFAULT_PROFILE:
            raise ConfigError(_BASE_RESERVED, category="invalid")
        if self._repo.read(name) is not None:
            raise ConfigError(f"profile {name!r} already exists", category="conflict")
        if copy_from is not None:
            source = self._repo.read(copy_from)
            if source is None:
                raise ConfigError(
                    f"cannot copy from {q(copy_from)}: "
                    + not_found("profile", copy_from, known=sorted(self._repo.names())),
                    category="not_found",
                )
            data = copy.deepcopy(source)
        else:
            data = {}
        if not dry_run:
            self._repo.write(name, data)


class DeleteProfile:
    """Remove ``profiles.<name>``; the base (``default``) is not a profile to delete."""

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def preview(self, name: str) -> ProfileDeletePreview:
        if name == DEFAULT_PROFILE:
            raise ConfigError(
                "cannot delete `default`: it is the base, config.yml's top level",
                category="invalid",
                hint="remove its keys with `untaped config unset`",
            )
        data = self._repo.read(name)
        if data is None:
            raise ConfigError(
                not_found("profile", name, known=sorted(self._repo.names())), category="not_found"
            )
        return ProfileDeletePreview(name=name, top_level_keys=tuple(sorted(data)))

    def __call__(self, name: str) -> None:
        self.preview(name)
        self._repo.delete(name)


class ListProfiles:
    """Return one :class:`Profile` for the base (``default``), then one per named profile."""

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

    By default the data is the **effective view**: the base merged with
    the named profile (named wins per leaf). ``raw=True`` returns only
    what's literally written under ``profiles.<name>`` (the base's own
    sections for ``default``).
    """

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(
        self,
        name: str,
        *,
        raw: bool = False,
    ) -> Profile:
        raw_data = self._repo.read(name)
        if raw_data is None:
            raise ConfigError(
                not_found("profile", name, known=sorted(self._repo.names())), category="not_found"
            )
        data = raw_data if raw else self._repo.resolved(name)
        active = self._repo.active_name() or DEFAULT_PROFILE
        return Profile(name=name, data=data, is_active=(name == active))


class RenameProfile:
    """Rename ``profiles.<old>`` to ``profiles.<new>``.

    ``default`` (the base) cannot be renamed nor used as the rename target.
    """

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def __call__(self, old_name: str, new_name: str, *, dry_run: bool = False) -> None:
        if not new_name:
            raise ConfigError("new profile name cannot be empty", category="invalid")
        if old_name == DEFAULT_PROFILE:
            raise ConfigError("cannot rename the `default` profile", category="invalid")
        if new_name == DEFAULT_PROFILE:
            raise ConfigError(_BASE_RESERVED, category="invalid")
        if self._repo.read(old_name) is None:
            raise ConfigError(
                not_found("profile", old_name, known=sorted(self._repo.names())),
                category="not_found",
            )
        if self._repo.read(new_name) is not None:
            raise ConfigError(f"profile {new_name!r} already exists", category="conflict")
        if not dry_run:
            self._repo.rename(old_name, new_name)


@dataclass(frozen=True, slots=True)
class CurrentProfileResult:
    name: str
    source: ProfileSource


class CurrentProfile:
    """Resolve the picked profile, classifying its source.

    Powers ``profile current`` — a one-line answer to "which profile am I
    using right now?". When the flag or the variable names a profile, the
    use case validates that the profile actually exists, so the documented
    pipe usage ``--profile $(… profile current)`` can't silently print a
    typo'd name that other commands then reject.
    """

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(self) -> CurrentProfileResult:
        name, source = self._repo.classify_active()
        if source in ("flag", "env"):
            assert name is not None  # invariant of classify_active
            if name not in self._repo.names():
                known = ", ".join(sorted(self._repo.names())) or "(none)"
                raise ConfigError(
                    f"active profile {name!r} (from {source}) is not defined; known: {known}"
                )
            return CurrentProfileResult(name=name, source=source)
        # Nothing picks: the run reads the base alone.
        return CurrentProfileResult(name=DEFAULT_PROFILE, source="fallback")


__all__ = [
    "CreateProfile",
    "CurrentProfile",
    "CurrentProfileResult",
    "DeleteProfile",
    "ListProfiles",
    "RenameProfile",
    "ShowProfile",
]
