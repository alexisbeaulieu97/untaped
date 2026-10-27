"""Workspace settings (``workspace:`` in ``config.yml``)."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

#: The most workers a command runs: twice this machine's CPUs.
MAX_WORKERS = (os.cpu_count() or 1) * 2


class WorkspaceSettings(BaseModel):
    """User-tunable workspace profile settings."""

    model_config = ConfigDict(frozen=True)

    workspaces_dir: Path = Field(
        default=Path("~/.untaped/workspaces"),
        description="Parent directory of every workspace (`<workspaces_dir>/NAME`).",
    )
    parallel: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Default workers for `create`/`add`/`run` and for status/archive "
            "checks. Unset means `min(8, 2 * CPUs)`; values above `2 * CPUs` are "
            "clamped."
        ),
    )
    branch_template: str = Field(
        default="{name}",
        description="Branch name for writable repos; `{name}` is the workspace name.",
    )
