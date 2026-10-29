"""Application use cases for the profile bounded context.

Each class depends only on the narrowest port it needs (see
:mod:`untaped.core.profile.ports`); none of them know about YAML, files, or the
resolver. Command-specific wording in raised errors is intentionally
minimal — the CLI layer owns command-aware hints.
"""

from __future__ import annotations

import copy

from untaped.core.profile.models import ProfileDeletePreview, StoredProfile
from untaped.core.profile.ports import ProfileReader, ProfileWriter
from untaped.runtime.profile_resolver import DEFAULT_PROFILE, PROFILE_NAME, Picked
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.words import not_found, q

_BASE_RESERVED = "`default` names the base, config.yml's top level; pick another name"


def _check_name(name: str) -> None:
    """Refuse a name ``--profile`` could not pick, and ``default`` (the base)."""
    if name == DEFAULT_PROFILE:
        raise ConfigError(_BASE_RESERVED, category="invalid")
    if not PROFILE_NAME.fullmatch(name):
        raise ConfigError(
            f"invalid profile name {name!r}",
            category="usage",
            hint="a name is letters, digits, `_`, `.` and `-`, and does not start with `.` or `-`",
        )


class CreateProfile:
    """Create ``profiles.<name>``.

    ``copy_from`` seeds the new profile from another existing one, or from
    the base (``default``). The copy is a deep copy — later edits to the
    source must not affect the new profile.
    """

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def __call__(self, name: str, *, copy_from: str | None = None, dry_run: bool = False) -> None:
        _check_name(name)
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
    """Remove ``profiles.<name>``; the base (``default``) is not a profile to delete.

    ``base_hint`` is the hint refusing the base gives: how to clear its keys instead.
    """

    def __init__(self, repo: ProfileWriter, *, base_hint: str | None = None) -> None:
        self._repo = repo
        self._base_hint = base_hint

    def preview(self, name: str) -> ProfileDeletePreview:
        if name == DEFAULT_PROFILE:
            raise ConfigError(
                "cannot delete `default`: it is the base, config.yml's top level",
                category="invalid",
                hint=self._base_hint,
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
    """Return one :class:`StoredProfile` for the base (``default``), then one per named profile."""

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(self) -> list[StoredProfile]:
        selected = _selected(self._repo)
        out: list[StoredProfile] = []
        for name in self._repo.names():
            data = self._repo.read(name) or {}
            out.append(StoredProfile(name=name, data=data, selected=name in selected))
        return out


def _selected(repo: ProfileReader) -> set[str]:
    """The profiles the run reads besides the base: its pick, else the base itself."""
    return {pick.name for pick in repo.pick()} or {DEFAULT_PROFILE}


class ShowProfile:
    """Return the named profile, or the run's pick when no name is given.

    By default the data is the **effective view**: the base merged with
    the named profile (named wins per leaf), or with each picked profile in
    order. ``raw=True`` returns only what's literally written under
    ``profiles.<name>`` (the base's own sections for ``default``), so it
    needs one profile.
    """

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(
        self,
        name: str | None = None,
        *,
        raw: bool = False,
    ) -> StoredProfile:
        if name is None:
            picked = [pick.name for pick in CurrentProfile(self._repo)()]
            if len(picked) > 1:
                if raw:
                    raise UsageError(
                        f"--raw shows one profile; the run picks {', '.join(picked)}",
                        hint="name the profile: `profile show NAME --raw`",
                    )
                return StoredProfile(
                    name=",".join(picked), data=self._repo.resolved(None), selected=True
                )
            name = picked[0] if picked else DEFAULT_PROFILE
        raw_data = self._repo.read(name)
        if raw_data is None:
            raise ConfigError(
                not_found("profile", name, known=sorted(self._repo.names())), category="not_found"
            )
        data = raw_data if raw else self._repo.resolved(name)
        return StoredProfile(name=name, data=data, selected=name in _selected(self._repo))


class RenameProfile:
    """Rename ``profiles.<old>`` to ``profiles.<new>``.

    ``default`` (the base) cannot be renamed nor used as the rename target.
    """

    def __init__(self, repo: ProfileWriter) -> None:
        self._repo = repo

    def __call__(self, old_name: str, new_name: str, *, dry_run: bool = False) -> None:
        if old_name == DEFAULT_PROFILE:
            raise ConfigError("cannot rename the `default` profile", category="invalid")
        _check_name(new_name)
        if self._repo.read(old_name) is None:
            raise ConfigError(
                not_found("profile", old_name, known=sorted(self._repo.names())),
                category="not_found",
            )
        if self._repo.read(new_name) is not None:
            raise ConfigError(f"profile {new_name!r} already exists", category="conflict")
        if not dry_run:
            self._repo.rename(old_name, new_name)


class CurrentProfile:
    """The run's pick, each profile with what picked it (``()``: the base alone).

    Powers ``profile current`` — a one-line answer to "which profiles am I
    using right now?". Each picked profile must exist, so the documented
    pipe usage ``--profile $(… profile current)`` can't silently print a
    typo'd name that other commands then reject.
    """

    def __init__(self, repo: ProfileReader) -> None:
        self._repo = repo

    def __call__(self) -> tuple[Picked, ...]:
        return self._repo.pick()


__all__ = [
    "CreateProfile",
    "CurrentProfile",
    "DeleteProfile",
    "ListProfiles",
    "RenameProfile",
    "ShowProfile",
]
