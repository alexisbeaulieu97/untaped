"""Repository inventory values: per-repo metadata and the cached listing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepositoryInventoryItem(BaseModel):
    """Repository metadata needed by sibling tools for source expansion."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    full_name: str
    name: str | None = None
    html_url: str | None = None
    clone_url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    description: str | None = None
    private: bool = False
    archived: bool = False
    fork: bool = False
    # Last push to any ref; lets a sweep skip fetching an unchanged repo.
    pushed_at: str | None = None


@dataclass(frozen=True)
class RepoInventory:
    """The repositories a scope can see, and when they were listed."""

    repos: tuple[RepositoryInventoryItem, ...]
    refreshed_at: datetime | None
    scope_key: str
    error: str = ""
    """Why a due refresh failed; the repos are then the stale cached ones."""
