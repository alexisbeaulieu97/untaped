"""The dotfiles settings (``dotfiles:`` in ``config.yml``)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped_dotfiles.models import OsName


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
        description="Holds `status.json` and `attention`, the files a prompt segment reads.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="This machine's tags, matched against `only` and `unless` in manifests.",
    )
    os: OsName | None = Field(
        default=None, description="This machine's OS for `os` filters; detected when unset."
    )
