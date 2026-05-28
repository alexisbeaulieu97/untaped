"""Use case: report the effective active profile and where it came from.

Powers ``untaped profile current`` — a one-line answer to "which profile
am I using right now?" with a stderr breadcrumb explaining whether the
answer came from the env var, the persisted ``active:`` key, or the
implicit ``default`` fallback. When env or config explicitly names a
profile, the use case also validates that the profile actually exists,
so the documented pipe usage
``untaped --profile $(untaped profile current)`` can't silently print a
typo'd name that other commands then reject.
"""

from __future__ import annotations

from dataclasses import dataclass

from untaped import DEFAULT_PROFILE, ConfigError, ProfileSource
from untaped_profile.application.ports import ProfileReader


@dataclass(frozen=True, slots=True)
class CurrentProfileResult:
    name: str
    source: ProfileSource


class CurrentProfile:
    """Resolve the effective active profile, classifying its source."""

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
