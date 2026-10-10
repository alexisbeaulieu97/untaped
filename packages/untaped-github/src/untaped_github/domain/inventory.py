"""The cached repository inventory: the repos a scope can see, and when they were listed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from untaped_github.domain.models import GithubRepo


@dataclass(frozen=True)
class RepoInventory:
    """The repositories a scope can see, and when they were listed."""

    repos: tuple[GithubRepo, ...]
    refreshed_at: datetime | None
    scope_key: str
