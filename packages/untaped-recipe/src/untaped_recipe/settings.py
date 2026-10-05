"""Settings for the recipe tool."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class RecipeSettings(BaseModel):
    """Profile settings for local recipe storage."""

    renamed_keys: ClassVar[Mapping[str, str]] = {"library_root": "library_dir"}

    model_config = ConfigDict(frozen=True)

    library_dir: Path = Path("~/.untaped/untaped-recipes")
    hook_timeout_seconds: float = Field(default=60, ge=0)
    hook_startup_timeout_seconds: float = Field(default=300, ge=0)
    backup_keep: int | None = Field(default=None, ge=1)
    backup_max_age_days: int | None = Field(default=None, ge=1)
    preview_max_rows: int = Field(default=50, ge=0)
