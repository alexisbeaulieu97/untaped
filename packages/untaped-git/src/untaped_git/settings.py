"""Git plugin settings: where the repo store lives, and how worktrees ask for credentials."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import get_config_section


class GitSettings(BaseModel):
    """User-tunable ``git`` profile settings."""

    model_config = ConfigDict(frozen=True)

    store_dir: Path = Field(default=Path("~/.untaped/plugins/git/store"))
    untaped_helper_first: bool = False


def git_settings() -> GitSettings:
    """The active profile's ``git`` settings.

    The repo store is called from other plugins' code, so it resolves its own
    settings rather than receiving them from a command.
    """
    return get_config_section("git", GitSettings)
