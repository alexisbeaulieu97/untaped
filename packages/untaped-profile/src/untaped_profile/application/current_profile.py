"""Use case: report the effective active profile and where it came from.

Powers ``untaped profile current`` — a one-line answer to "which profile
am I using right now?" with a stderr breadcrumb explaining whether the
answer came from the env var, the persisted ``active:`` key, or the
implicit ``default`` fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from untaped_core.profile_resolver import ACTIVE_PROFILE_ENV, DEFAULT_PROFILE

from untaped_profile.application.ports import ProfileRepository

ProfileSource = Literal["env", "config", "fallback"]


@dataclass(frozen=True, slots=True)
class CurrentProfileResult:
    name: str
    source: ProfileSource


class CurrentProfile:
    """Resolve the effective active profile, classifying its source."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    def __call__(self) -> CurrentProfileResult:
        env_value = os.environ.get(ACTIVE_PROFILE_ENV)
        if env_value:
            return CurrentProfileResult(name=env_value, source="env")
        persisted = self._repo.persisted_active_name()
        if persisted:
            return CurrentProfileResult(name=persisted, source="config")
        return CurrentProfileResult(name=DEFAULT_PROFILE, source="fallback")
