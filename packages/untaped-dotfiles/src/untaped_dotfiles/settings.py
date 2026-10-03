"""Dotfiles settings (profile) and state models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped_dotfiles.domain.models import AppliedRecord, ItemChoice, OsName, RepoRecord


class DotfilesSettings(BaseModel):
    """User-tunable dotfiles profile settings."""

    model_config = ConfigDict(frozen=True)

    repos_dir: Path = Field(default=Path("~/.untaped/dotfiles/repos"))
    kept_dir: Path = Field(default=Path("~/.untaped/dotfiles/kept"))
    state_dir: Path = Field(default=Path("~/.untaped/dotfiles"))
    tags: list[str] = Field(default_factory=list)
    os: OsName | None = None


class DotfilesState(BaseModel):
    """Subscribed repos, the machine's item choices and placed paths (``state.yml``)."""

    model_config = ConfigDict(frozen=True)

    repos: list[RepoRecord] = Field(default_factory=list)
    items: list[ItemChoice] = Field(default_factory=list)
    applied: list[AppliedRecord] = Field(default_factory=list)
