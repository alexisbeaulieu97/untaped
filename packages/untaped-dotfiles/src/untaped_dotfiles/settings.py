"""Dotfiles settings (profile) and state models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped_dotfiles.domain.models import AppliedRecord, ItemChoice, OsName, RepoRecord


class DotfilesSettings(BaseModel):
    """User-tunable dotfiles profile settings."""

    model_config = ConfigDict(frozen=True)

    repos_dir: Path = Field(
        default=Path("~/.untaped/plugins/dotfiles/repos"),
        description="Where `dotfiles subscribe URL` clones repos (`<repos_dir>/NAME`).",
    )
    kept_dir: Path = Field(
        default=Path("~/.untaped/plugins/dotfiles/kept"),
        description=(
            "Where `apply`, `sync` and `remove` keep local files they replace, "
            "under `<repo>/<item>/<timestamp>/`."
        ),
    )
    state_dir: Path = Field(
        default=Path("~/.untaped/plugins/dotfiles"),
        description="Holds `status.json`, `attention` and the advisory lock.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="This machine's tags, matched against `only` and `unless` in manifests.",
    )
    os: OsName | None = Field(
        default=None, description="This machine's OS for `os` filters; detected when unset."
    )


class DotfilesState(BaseModel):
    """Subscribed repos, the machine's item choices and placed paths (``state.yml``)."""

    model_config = ConfigDict(frozen=True)

    repos: list[RepoRecord] = Field(
        default_factory=list,
        description="Subscribed repos. Managed by `dotfiles subscribe`/`unsubscribe`.",
    )
    items: list[ItemChoice] = Field(
        default_factory=list,
        description=(
            "Enabled items with their policy and skips. Managed by `dotfiles enable`/`disable`."
        ),
    )
    applied: list[AppliedRecord] = Field(
        default_factory=list,
        description=(
            "One record per path the tool placed. Managed by `dotfiles apply`, `sync` and `remove`."
        ),
    )
