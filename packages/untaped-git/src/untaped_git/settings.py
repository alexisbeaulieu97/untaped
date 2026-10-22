"""Git plugin settings: where the repo store lives, and how worktrees ask for credentials."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import deprecated_behaviour, get_config_section

NONCELESS_HELPER = deprecated_behaviour("nonceless-credential-helper", removed_in=12)
"""``git credential get`` answering a helper line without ``--nonce`` (11.0 wrote them)."""


class GitSettings(BaseModel):
    """User-tunable ``git`` profile settings."""

    model_config = ConfigDict(frozen=True)

    store_dir: Path = Field(
        default=Path("~/.untaped/plugins/git/store"),
        description=(
            "Root of the repo store: one shared bare repository per remote, under "
            "`<store_dir>/<host>/<path>.git`."
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
    return get_config_section("git", GitSettings)


def store_root() -> Path:
    """The repo store's directory (``git.store_dir``, ``~`` expanded)."""
    return git_settings().store_dir.expanduser()


def credential_rank() -> tuple[str, ...]:
    """The plugins ranked for ``GitHost.credential`` (``git.extensions.git_host.rank``)."""
    extensions = getattr(git_settings(), "extensions", None) or {}
    extension = extensions.get("git_host")
    return () if extension is None else tuple(extension.rank.get("credential", ()))
