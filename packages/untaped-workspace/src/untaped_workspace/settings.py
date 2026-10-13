"""Workspace settings (profile) and state models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Cache, Retired
from untaped_workspace.domain.models import ArchivedRecord, WorkspaceRecord

#: The repos each ``RepoSource`` provider lists (``RepoSource.repos``'s answer cache);
#: its setting is ``workspace.caches.repo_source_repos.max_age_seconds``.
REPOS = Cache("workspace", "repo_source_repos", label="repos", max_age=timedelta(hours=6))


class WorkspaceSettings(BaseModel):
    """User-tunable workspace profile settings."""

    model_config = ConfigDict(frozen=True)

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "cache_dir": Retired(note="deleted in 11.0; the repo store lives under git.store_dir"),
        "protocol": Retired(note="deleted in 11.0; set github.git_protocol"),
    }

    workspaces_dir: Path = Field(default=Path("~/.untaped/workspaces"))
    parallel: int | None = Field(default=None, ge=1)
    """Workers for ``create``/``add`` checkouts and ``status``/``archive`` checks.

    ``None`` means ``min(8, 2 x CPUs)``.
    """
    branch_template: str = "{name}"


class WorkspaceState(BaseModel):
    """Active and archived workspaces (``state.yml`` → ``workspace``)."""

    model_config = ConfigDict(frozen=True)

    active: list[WorkspaceRecord] = Field(default_factory=list)
    archived: list[ArchivedRecord] = Field(default_factory=list)
