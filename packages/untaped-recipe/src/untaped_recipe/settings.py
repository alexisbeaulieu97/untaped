"""Settings for the recipe plugin."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class RecipeSettings(BaseModel):
    """Profile settings for local recipe storage."""

    retired_keys: ClassVar[Mapping[str, str]] = {"library_root": "library_dir"}

    model_config = ConfigDict(frozen=True)

    library_dir: Path = Field(
        default=Path("~/.untaped/plugins/recipe/library"),
        description="Directory holding installed recipe packs.",
    )
    hook_timeout_seconds: float = Field(
        default=60, ge=0, description="Per-hook request timeout; `0` disables it."
    )
    hook_startup_timeout_seconds: float = Field(
        default=300, ge=0, description="Timeout for preparing a hook environment."
    )
    backup_keep: int | None = Field(
        default=None, ge=1, description="`backups prune` keeps this many newest bundles by default."
    )
    backup_max_age_days: int | None = Field(
        default=None,
        ge=1,
        description="`backups prune` deletes bundles older than this by default.",
    )
    preview_max_rows: int = Field(
        default=50,
        ge=0,
        description="Preview rows before `apply` collapses per-file rows; `0` is unlimited.",
    )
