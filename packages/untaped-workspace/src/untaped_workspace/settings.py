"""Workspace settings (profile) and state models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Retired
from untaped_workspace.domain.models import ArchivedRecord, WorkspaceRecord


class WorkspaceSettings(BaseModel):
    """User-tunable workspace profile settings."""

    model_config = ConfigDict(frozen=True)

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "cache_dir": Retired(note="deleted in 11.0; the repo store lives under git.store_dir"),
    }

    workspaces_dir: Path = Field(default=Path("~/.untaped/workspaces"))
    parallel: int | None = Field(default=None, ge=1)
    """Workers for ``create``/``add`` checkouts and ``status``/``archive`` checks.

    ``None`` means ``min(8, 2 x CPUs)``.
    """
    branch_template: str = "{name}"
    protocol: Literal["https", "ssh"] = "https"


class WorkspaceState(BaseModel):
    """Active and archived workspaces (``state.yml`` → ``workspace``)."""

    model_config = ConfigDict(frozen=True)

    active: list[WorkspaceRecord] = Field(default_factory=list)
    archived: list[ArchivedRecord] = Field(default_factory=list)
