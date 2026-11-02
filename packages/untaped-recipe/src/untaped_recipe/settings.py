"""Settings for the recipe plugin, and the library they place."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Dir, key
from untaped.sdk.health import HomeOnly


class RecipeSettings(BaseModel):
    """Profile settings for local recipe storage."""

    model_config = ConfigDict(frozen=True)

    library_dir: Annotated[Path | None, HomeOnly()] = Field(
        default=None,
        description=(
            "Directory holding installed recipe packs and backups; "
            "unset, `plugins/recipe/library/` under the untaped home."
        ),
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


#: The installed packs, ``packs.toml`` and ``backups/``.
LIBRARY = Dir(at=key(RecipeSettings).library_dir)
