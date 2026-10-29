"""Git plugin settings: where the repo store lives, and how worktrees ask for credentials."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Dir, key, settings_of


class GitSettings(BaseModel):
    """User-tunable ``git`` profile settings."""

    model_config = ConfigDict(frozen=True)

    store_dir: Path | None = Field(
        default=None,
        description=(
            "Root of the repo store: one shared bare repository per remote, under "
            "`<store_dir>/<host>/<path>.git`; an absolute path. Unset: `plugins/git/store/` "
            "under the home."
        ),
    )
    untaped_helper_only: bool = Field(
        default=False,
        description=(
            "In untaped's worktrees, ask only untaped for a host's credentials, "
            "instead of your own Git credential helpers first."
        ),
    )


def git_settings() -> GitSettings:
    """The active profile's ``git`` settings.

    The repo store is called from other plugins' code, so it resolves its own
    settings rather than receiving them from a command.
    """
    return settings_of(GitSettings)


#: The repo store's folder: ``git.store_dir``, or ``plugins/git/store/`` under the home.
STORE = Dir(at=key(GitSettings).store_dir)


def store_root() -> Path:
    """The repo store's directory (:data:`STORE`'s path)."""
    return STORE.path


def credential_rank() -> tuple[str, ...]:
    """The plugins ranked for ``GitHost.credential`` (``git.extensions.git_host.rank``)."""
    extensions = getattr(git_settings(), "extensions", None) or {}
    extension = extensions.get("git_host")
    return () if extension is None else tuple(extension.rank.get("credential", ()))
